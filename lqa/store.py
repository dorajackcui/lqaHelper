import base64
import copy
import hashlib
import io
import json
import logging
import math
import os
import re
import secrets
import shutil
import struct
import tempfile
import threading
import time
import uuid
import zlib
from contextlib import contextmanager
from pathlib import Path

from vendor import imagesize
from .hotkeys import DEFAULT_HOTKEYS, LEGACY_HOTKEYS
from .strings import StringCatalog


PREVIEW_FADE_SECONDS = 0.2
EDITOR_LEASE_SECONDS = 180
MAX_CAPTURE_BYTES = 48 * 1024 * 1024
DEFAULT_REASONING_EFFORT = "xhigh"
REASONING_EFFORTS = {
    "low": "Low", "medium": "Medium", "high": "High",
    "xhigh": "Extra High", "max": "Max", "ultra": "Ultra",
}
SHAREX_FILENAME = re.compile(r"LQA_[A-Za-z0-9_-]{5,100}\.(png|jpe?g)", re.IGNORECASE | re.ASCII)


class Conflict(ValueError):
    pass


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".writing-", delete=False) as out:
        tmp = Path(out.name)
        try:
            if isinstance(data, Path):
                with data.open("rb") as source:
                    shutil.copyfileobj(source, out)
            else:
                out.write(data)
            out.flush()
            os.fsync(out.fileno())
        except BaseException:
            out.close()
            tmp.unlink(missing_ok=True)
            raise
    try:
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def write_json(path, obj):
    atomic(path, (json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def png_size(data):
    if not 45 <= len(data) <= MAX_CAPTURE_BYTES or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("请选择有效的 PNG 截图（最大 48 MB）。")
    pos, width, height, pixels = 8, 0, 0, False
    while pos + 12 <= len(data):
        length, kind = struct.unpack_from(">I4s", data, pos)
        end = pos + 8 + length
        if end + 4 > len(data) or zlib.crc32(memoryview(data)[pos+4:end]) != struct.unpack_from(">I", data, end)[0]:
            break
        if pos == 8:
            if kind != b"IHDR" or length != 13:
                break
            width, height = struct.unpack_from(">II", data, pos + 8)
            if not width or not height or width * height > 100_000_000:
                raise ValueError("PNG 图像尺寸超出可处理范围。")
        if kind == b"IDAT" and length:
            pixels = True
        if kind == b"IEND" and not length and pixels and end + 4 == len(data):
            return [width, height]
        pos = end + 4
    raise ValueError("PNG 文件不完整，请重新保存。")


def jpeg_size(data):
    if not (len(data) <= MAX_CAPTURE_BYTES and data.startswith(b"\xff\xd8\xff")
            and data.endswith(b"\xff\xd9") and b"\xff\xda" in data):
        raise ValueError("JPG 截图不完整或超过 48 MB。")
    try:
        width, height = imagesize.get(io.BytesIO(data))
    except (ValueError, OSError) as exc:
        raise ValueError("无法读取 JPG 截图。") from exc
    if min(width, height) <= 0 or width * height > 100_000_000:
        raise ValueError("JPG 图像尺寸超出可处理范围。")
    return [width, height]


def decode64(value):
    return base64.b64decode(value, validate=True)


def ensure_project_directories(folder):
    (folder / "captures").mkdir(parents=True, exist_ok=True)
    (folder / "references").mkdir(exist_ok=True)


def normalize_text(source, target, encoding):
    with source.open("r", encoding=encoding, newline="") as inp, target.open("w", encoding="utf-8", newline="") as out:
        shutil.copyfileobj(inp, out, length=256 * 1024)
        out.flush()
        os.fsync(out.fileno())


@contextmanager
def prepared_reference(directory, upload):
    if not upload:
        yield None
        return
    filename, chunks = upload
    name = Path(filename).name
    ext = Path(name).suffix.lower()
    excel = ext in (".xlsx", ".xls", ".xlsm")
    if not excel and ext not in (".txt", ".md", ".csv", ".tsv"):
        raise ValueError("参考文件支持 Excel（XLSX、XLS、XLSM）、CSV、TSV、TXT 和 MD。")
    # Stage on the archive volume so publishing large files requires only a rename.
    parent = next((p for p in (directory, *directory.parents) if p.is_dir()), None)
    if parent is None:
        raise ValueError("项目归档目录不可用。")
    with tempfile.TemporaryDirectory(prefix=".lqa-import-", dir=parent) as temp:
        source = Path(temp) / ("source" + ext)
        digest = hashlib.sha256()
        with source.open("wb") as output:
            for block in chunks:
                digest.update(block)
                output.write(block)
            output.flush()
            os.fsync(output.fileno())
        stored = digest.hexdigest()[:16] + ext
        ref = {"name": name, "file": stored}
        if excel:
            from .excel import write_workbook_index
            ref["textFile"] = stored + ".tsv"
            index = Path(temp) / "index.tsv"
            with index.open("w", encoding="utf-8", newline="") as output:
                write_workbook_index(source, ext, output)
                output.flush()
                os.fsync(output.fileno())
            yield ref, {stored: source, ref["textFile"]: index}
        else:
            normalized = Path(temp) / "utf8"
            try:
                normalize_text(source, normalized, "utf-8-sig")
            except UnicodeDecodeError:
                normalize_text(source, normalized, "gb18030")
            yield ref, {stored: normalized}


def crop_bounds(value, size):
    if value is None:
        return None
    if (not isinstance(value, dict) or set(value) != {"x", "y", "width", "height"}
            or any(type(n) is not int for n in value.values())):
        raise ValueError("裁剪范围格式无效。")
    x, y, w, h = (value[k] for k in ("x", "y", "width", "height"))
    if min(x, y) < 0 or min(w, h) < 1 or x + w > size[0] or y + h > size[1]:
        raise ValueError("裁剪范围超出原图。")
    return None if [w, h] == size else dict(x=x, y=y, width=w, height=h)


def clipped_text(value, limit, label):
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(label + "内容过长或格式错误。")
    return value.strip()


def issue_key(issue):
    text = issue["sourceText"].strip() or issue["description"].strip()
    return tuple(s.casefold() for s in (issue["category"], text, issue["reference"]))


def sharex_id(name):
    return uuid.uuid5(uuid.NAMESPACE_URL, "sharex/" + name.lower()).hex


def sharex_image_format(name):
    match = SHAREX_FILENAME.fullmatch(name)
    if not match:
        raise ValueError("ShareX 截图应使用 LQA 文件名和 PNG 或 JPG 格式。")
    return "png" if match[1].lower() == "png" else "jpg"


def check_capture_folder(folder):
    expected = folder.parent.parent.resolve() / "captures" / folder.name
    if not re.fullmatch(r"[a-f0-9]{32}", folder.name) or folder.resolve() != expected:
        raise ValueError("截图归档路径无效。")


def finish_deletion(folder):
    check_capture_folder(folder)
    for file in folder.iterdir():
        if re.fullmatch(r"original\.(?:png|jpg)|marked(?:-[0-9]+)?\.png", file.name):
            file.unlink()
    (folder / ".deleting").unlink()
    if not any(folder.iterdir()):
        folder.rmdir()


class Store:
    """One local writer. A capture becomes visible only after its sidecar is committed."""

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.string_catalog = StringCatalog(self.root / "cache" / "strings")
        self.inbox = self.root / "inbox"
        self.discarded = self.root / "discarded-sharex"
        self.inbox.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.config_path = self.root / "library.json"
        if self.config_path.exists():
            self.config = read_json(self.config_path)
            self.config.setdefault("reasoningEffort", DEFAULT_REASONING_EFFORT)
            if "hotkeys" not in self.config:
                self.save_config(hotkeys=LEGACY_HOTKEYS.copy())
        else:
            self.config = {
                "projects": [], "active": "", "sessions": [], "seconds": 3, "hotkeys": DEFAULT_HOTKEYS.copy(),
                "agent": False, "reasoningEffort": DEFAULT_REASONING_EFFORT, "secret": secrets.token_urlsafe(32),
            }
            write_json(self.config_path, self.config)
        self.shots = {}
        self.load_errors = []
        self.inbox_errors = []
        for project in self.config["projects"]:
            folder = Path(project["folder"])
            if not folder.is_dir():
                self.load_errors.append("归档目录暂不可用：" + str(folder))
                continue
            try:
                ensure_project_directories(folder)
            except OSError as exc:
                self.load_errors.append(f"无法准备项目子目录 {folder}（{exc}）")
            for marker in (folder / "captures").glob("*/.deleting"):
                try:
                    deleted = read_json(marker)
                    if deleted["project"] == project["id"] and deleted["id"] == marker.parent.name:
                        finish_deletion(marker.parent)
                except (OSError, ValueError, KeyError) as exc:
                    self.load_errors.append(f"无法清理已删除的截图 {marker.parent.name}（{exc}）")
            for file in (folder / "captures").glob("*/capture.json"):
                try:
                    shot = read_json(file)
                    if shot["project"] != project["id"] or file.parent.name != shot["id"]:
                        continue
                    shot.setdefault("crop", None)
                    if shot["status"] == "running":
                        shot["status"] = "queued"
                        write_json(file, shot)
                    self.shots[shot["id"]] = shot
                except (OSError, ValueError, KeyError) as exc:
                    self.load_errors.append(f"无法读取 {file}（{exc}）")

    def project(self, key):
        for proj in self.config["projects"]:
            if proj["id"] == key:
                return proj
        raise KeyError("项目不存在。")

    def save_config(self, **changes):
        with self.lock:
            updated = dict(self.config, **changes)
            write_json(self.config_path, updated)
            self.config = updated

    def set_reasoning_effort(self, value):
        if not isinstance(value, str) or value not in REASONING_EFFORTS:
            raise ValueError("请选择列表中的推理强度。")
        self.save_config(reasoningEffort=value)

    def check_new_project_folder(self, folder):
        if (folder == self.inbox or self.inbox in folder.parents
                or any(folder == Path(p["folder"]) for p in self.config["projects"])):
            raise ValueError("请选择独立的项目归档目录。")

    def save_project(self, form, *, reference_upload=None):
        creating = not form.get("id")
        pid = uuid.uuid4().hex[:12] if creating else form["id"]
        project = dict(id=pid, name=clipped_text(form.get("name", ""), 80, "项目名称"),
                       description=clipped_text(form.get("description", ""), 4000, "说明"),
                       prompt=clipped_text(form.get("prompt", ""), 16000, "项目要求"))
        if not project["name"]:
            raise ValueError("请输入项目名称。")
        with self.lock:
            if creating:
                specified = clipped_text(form.get("folder", ""), 1024, "目录")
                if specified and not Path(specified).is_absolute():
                    raise ValueError("请输入绝对目录路径。")
                folder = Path(specified).resolve() if specified else self.root / "projects" / pid
                self.check_new_project_folder(folder)
            else:
                folder = Path(self.project(pid)["folder"])
        if reference_upload is None and form.get("reference"):
            legacy = form["reference"]
            reference_upload = (legacy["name"], (decode64(legacy["data"]),))
        references = folder / "references"
        # Transfer and indexing happen outside the lock shared with the capture queue.
        with prepared_reference(references, reference_upload) as prepared, self.lock:
            if creating:
                self.check_new_project_folder(folder)
            existing = None if creating else self.project(pid)
            project["folder"] = str(folder)
            project["reference"] = existing["reference"] if existing and not form.get("removeReference") else None
            ensure_project_directories(folder)
            if prepared:
                ref, files = prepared
                for basename, staged in files.items():
                    staged.replace(references / basename)
                project["reference"] = ref
            projects = list(self.config["projects"])
            if existing:
                projects[projects.index(existing)] = project
                self.save_config(projects=projects)
            else:
                projects.append(project)
                self.save_config(projects=projects, active=pid,
                                 sessions=self.config["sessions"] + [[time.time(), pid]])
            return pid

    def activate(self, pid):
        with self.lock:
            self.project(pid)
            if self.config["active"] != pid:
                self.save_config(active=pid, sessions=self.config["sessions"] + [[time.time(), pid]])

    def folder(self, shot):
        return Path(self.project(shot["project"])["folder"]) / "captures" / shot["id"]

    def related_entries(self, pid):
        catalog, _ = self.related_entry_snapshot(pid)
        return catalog

    def related_entry_snapshot(self, pid):
        # Commits replace shot dictionaries. Keep the matching revisions while the index is queried.
        with self.lock:
            project = self.project(pid)
            shots = {s["id"]: s for s in self.shots.values() if s["project"] == pid and s["status"] == "done"}
        issues = [(s["id"], s["analyzedRevision"], i, issue)
                  for s in shots.values() for i, issue in enumerate(s["issues"])]
        return self.string_catalog.related(project, issues), shots

    def commit(self, shot):
        write_json(self.folder(shot) / "capture.json", shot)
        self.shots[shot["id"]] = shot

    def add(self, data, name, pid=None, sharex=False, captured=None):
        name = Path(name).name
        extension = sharex_image_format(name) if sharex else "png"
        key = sharex_id(name) if sharex else uuid.uuid4().hex
        with self.lock:
            if key in self.shots:
                shot = self.shots[key]
                if sharex and shot["status"] in ("queued", "running"):
                    self.commit(dict(shot, status="queued", reviewAfter=self.preview_end()))
                return key
            if sharex and (self.discarded / key).is_file():
                return None
            size = jpeg_size(data) if extension == "jpg" else png_size(data)
            captured = captured if captured is not None else time.time()
            if sharex:
                pid = next((p for start, p in reversed(self.config["sessions"]) if start <= captured), None)
            if not pid:
                raise ValueError("请先创建并选择项目。")
            self.project(pid)
            shot = dict(id=key, project=pid, name=name, created=captured, size=size, revision=1,
                        tags=[], note="", marks=[], crop=None, original="original." + extension, marked=None,
                        status="queued", error="", summary="", issues=[], analyzedRevision=None)
            atomic(self.folder(shot) / shot["original"], data)
            shot["reviewAfter"] = self.preview_end()
            self.commit(shot)
            return key

    def preview_end(self):
        return time.time() + self.config["seconds"] + PREVIEW_FADE_SECONDS

    def open_editor(self, key, token):
        if not isinstance(token, str) or not re.fullmatch(r"[a-f0-9-]{36}", token):
            raise ValueError("编辑窗口标识无效。")
        with self.lock:
            shot = self.shots.get(key)
            if shot is None:
                raise KeyError("截图已删除或不存在。")
            now = time.time()
            sessions = {t: end for t, end in shot.get("editorLeases", {}).items() if end > now}
            sessions[token] = now + EDITOR_LEASE_SECONDS
            shot = dict(shot, editorLeases=sessions, status="queued" if shot["status"] == "running" else shot["status"])
            self.commit(shot)
            return dict(shot=shot, project=self.project(shot["project"])["name"])

    def close_editor(self, key, token):
        with self.lock:
            shot = self.shots.get(key)
            if shot and token in shot.get("editorLeases", {}):
                sessions = dict(shot["editorLeases"])
                del sessions[token]
                self.commit(dict(shot, editorLeases=sessions))

    def delete(self, key, revision):
        with self.lock:
            shot = self.shots.get(key)
            if shot is None:
                return True
            if revision != shot["revision"]:
                raise Conflict("截图已在另一个窗口更新，请重新打开后再删除。")
            folder = self.folder(shot)
            check_capture_folder(folder)
            name = Path(shot["name"]).name
            native = key == sharex_id(name)
            if native:
                atomic(self.discarded / key, b"")
            # Renaming the sidecar commits the deletion even if cleanup is interrupted.
            (folder / "capture.json").rename(folder / ".deleting")
            del self.shots[key]
            try:
                finish_deletion(folder)
                if native:
                    (self.inbox / name).unlink(missing_ok=True)
            except OSError as exc:
                logging.warning("截图已删除，文件清理待重试 %s: %s", key, exc)
                return False
            return True

    def edit(self, key, form):
        with self.lock:
            shot = self.shots[key]
            if form.get("revision") != shot["revision"]:
                raise Conflict("这张截图已有新版本，请重新打开后再保存。")
            raw_tags, marks = form.get("tags", []), form.get("marks", [])
            if not isinstance(raw_tags, list) or len(raw_tags) > 12:
                raise ValueError("每张图最多 12 个标签。")
            tags = list(dict.fromkeys(t for v in raw_tags if (t := clipped_text(v, 40, "标签"))))
            note = clipped_text(form.get("note", ""), 4000, "备注")
            crop = crop_bounds(form.get("crop", shot["crop"]), shot["size"])
            if not isinstance(marks, list) or len(marks) > 200:
                raise ValueError("标注数量过多。")
            for m in marks:
                if not isinstance(m, dict) or m.get("type") not in ("rect", "arrow") or any(
                    type(m.get(k)) not in (int, float) or not math.isfinite(m[k]) or not 0 <= m[k] <= 1
                    for k in ("x1", "y1", "x2", "y2")
                ):
                    raise ValueError("标注坐标无效。")
            image_changed = (marks, crop) != (shot["marks"], shot["crop"])
            if not image_changed and (tags, note) == (shot["tags"], shot["note"]):
                return
            updated = dict(shot, tags=tags, note=note, marks=marks, crop=crop, revision=shot["revision"] + 1,
                           status="queued", error="")
            if image_changed:
                updated["marked"] = None
                if marks or crop:
                    bitmap = decode64(form.get("rendered", ""))
                    expected = [crop["width"], crop["height"]] if crop else shot["size"]
                    if png_size(bitmap) != expected:
                        raise ValueError("保存的图片尺寸与选定范围不一致。")
                    updated["marked"] = f'marked-{updated["revision"]}.png'
                    atomic(self.folder(shot) / updated["marked"], bitmap)
            self.commit(updated)

    def claim(self):
        with self.lock:
            now = time.time()
            for shot in sorted(self.shots.values(), key=lambda s: s["created"]):
                if (shot["status"] == "queued" and shot.get("reviewAfter", 0) <= now
                        and not any(end > now for end in shot.get("editorLeases", {}).values())):
                    running = dict(shot, status="running", reasoningEffort=self.config["reasoningEffort"])
                    running.pop("editorLeases", None)
                    self.commit(running)
                    return copy.deepcopy(running), copy.deepcopy(self.project(shot["project"]))

    def is_running(self, key, revision):
        with self.lock:
            shot = self.shots.get(key)
            return shot is not None and shot["status"] == "running" and shot["revision"] == revision

    def complete(self, key, revision, report=None, error=""):
        with self.lock:
            if not self.is_running(key, revision):
                return
            shot = self.shots[key]
            if error:
                self.commit(dict(shot, status="error", error=error))
                return
            resolved = {issue_key(i) for i in shot["issues"] if i.get("resolved")}
            issues = [dict(i, resolved=issue_key(i) in resolved) for i in report["issues"]]
            self.commit(dict(shot, status="done", summary=report["summary"], issues=issues,
                             analyzedRevision=revision, error=""))

    def retry(self, key):
        with self.lock:
            shot = self.shots[key]
            if shot["status"] != "running":
                self.commit(dict(shot, status="queued", error=""))

    def resolve(self, key, index, revision, resolved):
        with self.lock:
            shot = copy.deepcopy(self.shots[key])
            if shot["status"] != "done" or shot["analyzedRevision"] != revision:
                raise Conflict("问题已更新，请刷新列表。")
            if type(index) is not int or not 0 <= index < len(shot["issues"]):
                raise ValueError("问题不存在。")
            shot["issues"][index]["resolved"] = resolved is True
            self.commit(shot)

    def recover_inbox(self):
        errors = []
        for file in self.inbox.glob("LQA_*.*"):
            key = sharex_id(file.name)
            try:
                with self.lock:
                    stat = file.stat()
                    shot = self.shots.get(key)
                    if shot:
                        if time.time() - stat.st_mtime > 86400:
                            original = self.folder(shot) / shot["original"]
                            if original.is_file() and original.stat().st_size == stat.st_size and original.read_bytes() == file.read_bytes():
                                file.unlink()
                    elif (self.discarded / key).is_file():
                        file.unlink()
                    elif time.time() - stat.st_mtime > 2:
                        sharex_image_format(file.name)
                        if stat.st_size > MAX_CAPTURE_BYTES:
                            raise ValueError("截图超过 48 MB。")
                        self.add(file.read_bytes(), file.name, sharex=True, captured=stat.st_ctime)
            except (ValueError, OSError) as exc:
                if isinstance(exc, FileNotFoundError) and not file.exists():
                    continue  # The user can delete a shot while the inbox is being scanned.
                message = f"{file.name} 归档失败：{exc}"
                errors.append(message)
                if message not in self.inbox_errors:
                    logging.warning("%s", message)
        with self.lock:
            self.inbox_errors = errors
