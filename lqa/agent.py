import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from .store import DEFAULT_REASONING_EFFORT, read_json


ASSETS = Path(__file__).resolve().parent.parent / "resources"
SCHEMA = ASSETS / "report.schema.json"
HIDDEN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def codex_executable():
    folder = Path(os.environ.get("LOCALAPPDATA", "")) / "OpenAI" / "Codex" / "bin"
    choices = sorted(folder.glob("*/codex.exe"), key=lambda p: p.stat().st_mtime, reverse=True)
    return str(choices[0]) if choices else shutil.which("codex.exe") or shutil.which("codex")


def check_login(exe):
    if not exe:
        return "missing"
    try:
        result = subprocess.run([exe, "login", "status"], capture_output=True, timeout=12,
                                creationflags=HIDDEN)
        message = (result.stdout + result.stderr).decode("utf-8", "replace").lower()
        if result.returncode == 0 and "logged in" in message and "not logged in" not in message:
            return "ready"
        return "signed_out" if "not logged in" in message else "unavailable"
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"


def validate_report(report):
    schema = read_json(SCHEMA)["properties"]["issues"]["items"]["properties"]
    if not isinstance(report, dict) or not isinstance(report.get("summary"), str) or not isinstance(report.get("issues"), list):
        raise ValueError("Codex 返回的问题集格式无效。")
    for issue in report["issues"]:
        if not isinstance(issue, dict) or any(not isinstance(issue.get(k), str)
                                              for k, field in schema.items() if field["type"] == "string"):
            raise ValueError("Codex 返回了不完整的问题字段。")
        keys = issue.get("referenceKeys", [])
        if not isinstance(keys, list) or any(not isinstance(k, str) or not k.strip() for k in keys):
            raise ValueError("Codex 返回的词条 Key 列表格式无效。")
        if any(issue[k] not in schema[k]["enum"] for k in ("category", "severity")) or not issue["description"].strip():
            raise ValueError("Codex 返回了无效的问题类别、严重度或描述。")
    return report


def run_review_cli(args, *, input, cwd, current):
    if not current():
        return None
    with subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                          cwd=cwd, creationflags=HIDDEN) as process:
        deadline = time.monotonic() + 240
        try:
            while current():
                try:
                    _, stderr = process.communicate(input, timeout=0.5)
                    return subprocess.CompletedProcess(args, process.returncode, stderr=stderr)
                except subprocess.TimeoutExpired:
                    input = None  # communicate resumes reading the same pipes on the next call.
                    if time.monotonic() >= deadline:
                        raise subprocess.TimeoutExpired(args, 240)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
    return None


def analyze(exe, shot, project, folder, current=lambda: True):
    with tempfile.TemporaryDirectory(prefix="lqa-review-") as temp:
        work = Path(temp)
        crop = shot.get("crop")
        if crop:
            images = [(shot["marked"], "cropped.png")]
        else:
            images = [(shot["original"], "original" + Path(shot["original"]).suffix)]
            if shot["marked"]:
                images.append((shot["marked"], "annotated.png"))
        effort = shot.get("reasoningEffort", DEFAULT_REASONING_EFFORT)
        args = [
            exe, "exec", "--model", "gpt-6.2-sol", "--config", f'model_reasoning_effort="{effort}"',
            "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only",
            "--output-schema", str(SCHEMA), "--output-last-message", str(work / "result.json"),
        ]
        for source, name in images:
            attachment = work / name
            shutil.copyfile(folder / source, attachment)
            args.extend(["--image", str(attachment)])
        context = dict(project=project["name"], description=project["description"],
                       reviewInstructions=project["prompt"], tags=shot["tags"], note=shot["note"],
                       marks=shot["marks"], crop=crop, originalSize=shot["size"], reference=None)
        if project["reference"]:
            ref = project["reference"]
            text_file = ref.get("textFile", ref["file"])
            local = work / ("reference" + Path(text_file).suffix)
            shutil.copyfile(Path(project["folder"]) / "references" / text_file, local)
            context["reference"] = {"name": ref["name"], "path": local.name}
            if local.stat().st_size <= 24000:
                context["reference"]["text"] = local.read_text(encoding="utf-8")
        prompt = (ASSETS / "review.txt").read_text(encoding="utf-8") + "\n" + json.dumps(context, ensure_ascii=False)
        result = run_review_cli(args + ["-"], input=prompt.encode("utf-8"), cwd=work, current=current)
        if result is None:
            return None
        if result.returncode:
            lines = result.stderr.decode("utf-8", "replace").splitlines()
            detail = next((l for l in reversed(lines) if "error" in l.lower()), "")
            raise RuntimeError(f"Codex 退出码 {result.returncode}。" + detail[:260])
        return validate_report(read_json(work / "result.json"))


class Agent:
    def __init__(self, store):
        self.store = store
        self.exe = codex_executable()
        self.status = "checking"
        self.login_process = None
        self.wake = threading.Event()
        self.stop = threading.Event()
        self.next_check = 0
        self.lock = threading.Lock()

    def connect(self):
        with self.lock:
            self.exe = codex_executable()
            if not self.exe:
                self.status = "missing"
                raise ValueError("没有找到 Codex CLI。安装 Codex 后重新连接。")
            self.store.save_config(agent=True)
            self.status = check_login(self.exe)
            if self.status != "ready" and (not self.login_process or self.login_process.poll() is not None):
                self.login_process = subprocess.Popen([self.exe, "login"],
                    creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
            self.next_check = 0
            self.wake.set()

    def run(self):
        while not self.stop.is_set():
            try:
                if not self.store.config["agent"]:
                    self.status = "disconnected"
                else:
                    if time.monotonic() >= self.next_check or self.status == "disconnected":
                        self.exe = codex_executable()
                        self.status = check_login(self.exe)
                        self.next_check = time.monotonic() + (25 if self.status == "ready" else 4)
                    if self.status == "ready":
                        job = self.store.claim()
                        if job:
                            shot, project = job
                            try:
                                report = analyze(self.exe, shot, project, self.store.folder(shot),
                                                 lambda: self.store.is_running(shot["id"], shot["revision"]))
                                if report is not None:
                                    self.store.complete(shot["id"], shot["revision"], report)
                            except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
                                self.store.complete(shot["id"], shot["revision"], error=str(exc)[:450])
                                self.next_check = 0
                            continue
            except Exception:
                logging.exception("后台分析暂时中断")
            self.wake.wait(1)
            self.wake.clear()
