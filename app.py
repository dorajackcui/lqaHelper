import argparse
import csv
import hmac
import io
import json
import logging
import os
import socket
import subprocess
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from lqa.agent import Agent, HIDDEN
from lqa.capture import Capture
from lqa.hotkeys import KEYS
from lqa.store import MAX_CAPTURE_BYTES, REASONING_EFFORTS, Conflict, Store, decode64
from vendor.formdata.multipart import MultipartError, MultipartSegment, PushMultipartParser


ROOT = Path(__file__).resolve().parent
FILES = {"/": ("index.html", "text/html; charset=utf-8"),
         "/app.js": ("app.js", "text/javascript; charset=utf-8"),
         "/style.css": ("style.css", "text/css; charset=utf-8"),
         "/ui/inter-latin.woff2": ("ui/inter-latin.woff2", "font/woff2")}


def choose_folder():
    command = ("Add-Type -AssemblyName System.Windows.Forms; "
               "$d=New-Object Windows.Forms.FolderBrowserDialog; "
               "$w=New-Object Windows.Forms.Form; $w.TopMost=$true; "
               "if($d.ShowDialog($w) -eq 'OK'){[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
               "[Console]::Write($d.SelectedPath)}; $d.Dispose(); $w.Dispose()")
    result = subprocess.run(["powershell.exe", "-NoProfile", "-STA", "-Command", command],
                            capture_output=True, creationflags=HIDDEN, timeout=180, check=True)
    return result.stdout.decode("utf-8-sig").strip()


def write_csv_row(writer, cells):
    writer.writerow("'" + value if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@"))
                    else value for value in cells)


def csv_report(store, pid):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["状态", "严重度", "类别", "问题", "画面文本", "修改建议", "参考词条", "标签", "备注", "截图路径"])
    with store.lock:
        store.project(pid)
        for shot in store.shots.values():
            if shot["project"] != pid or shot["status"] != "done":
                continue
            for issue in shot["issues"]:
                cells = ["已处理" if issue["resolved"] else "待处理", issue["severity"], issue["category"],
                         issue["description"], issue["sourceText"], issue["suggestion"], issue["reference"],
                         ", ".join(shot["tags"]), shot["note"], str(store.folder(shot) / (shot["marked"] or shot["original"]))]
                write_csv_row(writer, cells)
    return stream.getvalue().encode("utf-8-sig")


def related_csv_report(store, pid, *, include_resolved=False):
    catalog, shots = store.related_entry_snapshot(pid)
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["Key", "中文原文", "关联问题数", "待处理数", "关联问题", "截图路径", "语言包", "来源行"])
    for entry in catalog["entries"]:
        problems, screenshots = [], {}
        open_count = 0
        for link in entry["issues"]:
            shot = shots[link["shot"]]
            issue = shot["issues"][link["index"]]
            if issue["resolved"] and not include_resolved:
                continue
            if not issue["resolved"]:
                open_count += 1
            path = str(store.folder(shot) / (shot["marked"] or shot["original"]))
            number = screenshots.setdefault(path, len(screenshots) + 1)
            status = "已处理" if issue["resolved"] else "待处理"
            detail = f"截图 {number} · {status} · {issue['description']}"
            if issue["suggestion"]:
                detail += "\n修改建议　" + issue["suggestion"]
            problems.append(detail)
        if problems:
            locations = [" · ".join(filter(None, (loc["source"], loc["sheet"], f"第 {loc['row']} 行")))
                         for loc in entry["locations"]]
            write_csv_row(writer, [entry["key"], entry["chinese"], len(problems), open_count,
                                   "\n".join(problems), "\n".join(screenshots), catalog["name"], "\n".join(locations)])
    return stream.getvalue().encode("utf-8-sig")


def save_project_upload(store, stream, boundary, length):
    """A browser FormData request carries project settings first, then the reference file."""
    if not boundary or length < 0:
        raise ValueError("参考文件上传请求缺少边界或长度。")
    try:
        with PushMultipartParser(boundary, content_length=length, max_segment_count=2) as parser:
            events = parser.parse_blocking(stream.read, chunk_size=1024 * 1024)
            settings = next(events, None)
            if not isinstance(settings, MultipartSegment) or settings.name != "project" or settings.filename:
                raise ValueError("上传请求缺少项目设置。")
            metadata = bytearray()
            for chunk in events:
                if chunk is None:
                    break
                metadata.extend(chunk)
                if len(metadata) > 256 * 1024:  # Only the JSON form fields are buffered.
                    raise ValueError("项目设置内容过长。")
            form = json.loads(metadata)
            file = next(events, None)
            if not isinstance(form, dict) or not isinstance(file, MultipartSegment) or file.name != "reference" or not file.filename:
                raise ValueError("请选择一个参考文件。")

            def file_chunks():
                for chunk in events:
                    if isinstance(chunk, MultipartSegment):
                        raise ValueError("每次请选择一个参考文件。")
                    if chunk is not None:
                        yield chunk

            return store.save_project(form, reference_upload=(file.filename, file_chunks()))
    except MultipartError as exc:
        raise ValueError("参考文件传输不完整，请重新保存。") from exc


class WorkspaceServer(ThreadingHTTPServer):
    allow_reuse_port = False
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def reply(self, data, mime="application/json; charset=utf-8", status=200, headers=None):
        if not isinstance(data, bytes):
            data = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; object-src 'none'")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def body(self, limit):
        size = int(self.headers.get("Content-Length", "0"))
        if not 0 <= size <= limit:
            raise ValueError("文件或请求体过大。")
        return self.rfile.read(size)

    def route(self, method):
        srv = self.server
        url = urlsplit(self.path)
        path, query = url.path, parse_qs(url.query)
        if self.headers.get("Host") != srv.base.split("//")[1] or self.headers.get("Origin", srv.base) != srv.base:
            return self.reply({"error": "仅接受本机工作台请求。"}, status=403)
        store, agent, capture = srv.store, srv.agent, srv.capture
        if method == "GET":
            if path in FILES:
                name, mime = FILES[path]
                return self.reply((ROOT / "web" / name).read_bytes(), mime)
            if path == "/health":
                return self.reply({"app": "lqa-helper"})
            if path == "/api/hotkey-keys":
                return self.reply(KEYS)
            if path == "/api/related-entries":
                return self.reply(store.related_entries(query["project"][0]))
            if path == "/api/state":
                with store.lock:
                    cfg = store.config
                    shots = sorted((s for s in store.shots.values() if s["project"] == cfg["active"]),
                                   key=lambda s: s["created"], reverse=True)
                    return self.reply(dict(projects=cfg["projects"], active=cfg["active"], shots=shots,
                                           seconds=cfg["seconds"], hotkeys=cfg["hotkeys"],
                                           connected=cfg["agent"], agent=agent.status,
                                           reasoningEffort=cfg["reasoningEffort"], reasoningEfforts=REASONING_EFFORTS,
                                           capture=capture.ready, captureError=capture.error,
                                           warnings=store.load_errors + store.inbox_errors))
            parts = path.strip("/").split("/")
            if len(parts) == 3 and parts[:2] == ["api", "shot"]:
                with store.lock:
                    shot = store.shots[parts[2]]
                    return self.reply(dict(shot=shot, project=store.project(shot["project"])["name"]))
            if len(parts) == 3 and parts[0] == "image" and parts[2] in ("original", "marked"):
                with store.lock:
                    shot = store.shots.get(parts[1])
                    if not shot or not shot[parts[2]]:
                        raise KeyError("截图已删除或不存在。")
                    file = store.folder(shot) / shot[parts[2]]
                    image = file.read_bytes()
                return self.reply(image, "image/jpeg" if file.suffix == ".jpg" else "image/png")
            if path == "/api/export":
                return self.reply(csv_report(store, query["project"][0]), "text/csv; charset=utf-8",
                                  headers={"Content-Disposition": 'attachment; filename="LQA-issues.csv"'})
            if path == "/api/export-entries":
                report = related_csv_report(store, query["project"][0], include_resolved=query.get("includeResolved") == ["1"])
                return self.reply(report, "text/csv; charset=utf-8",
                                  headers={"Content-Disposition": 'attachment; filename="LQA-related-entries.csv"'})
        if method == "POST":
            if path == "/capture":
                if not hmac.compare_digest(self.headers.get("X-LQA-Key", ""), store.config["secret"]):
                    return self.reply({"error": "连接未授权。"}, status=403)
                name = query.get("name", [""])[0]
                if Path(name).name != name:
                    raise ValueError("无效的截图名称。")
                raw = self.body(MAX_CAPTURE_BYTES)
                local = store.inbox / name
                created = local.stat().st_ctime if local.is_file() else time.time()
                key = store.add(raw, name, sharex=True, captured=created)
                agent.wake.set()
                link = srv.base + ("/#shot=" + key if key else "/")
                return self.reply(link.encode(), "text/plain; charset=utf-8")
            if not path.startswith("/api/") or self.headers.get("X-LQA-UI") != "1":
                return self.reply({"error": "无效的工作台请求。"}, status=403)
            mime = self.headers.get_content_type()
            if path == "/api/project" and mime == "multipart/form-data":
                key = save_project_upload(store, self.rfile, self.headers.get_param("boundary"),
                                          int(self.headers.get("Content-Length", "-1")))
                capture.start()
                return self.reply({"id": key})
            if mime != "application/json":
                return self.reply({"error": "无效的工作台请求。"}, status=403)
            form = json.loads(self.body(68 * 1024 * 1024) or b"{}")
            if not isinstance(form, dict):
                raise ValueError("工作台请求需要 JSON 对象。")
            if path == "/api/project":
                key = store.save_project(form)
                capture.start()
                return self.reply({"id": key})
            if path == "/api/activate":
                store.activate(form["id"])
                capture.start()
            elif path == "/api/import":
                key = store.add(decode64(form["data"]), form["name"], pid=form["project"])
                agent.wake.set()
                return self.reply({"id": key})
            elif path == "/api/edit":
                store.edit(form["id"], form)
                agent.wake.set()
            elif path == "/api/editor/open":
                return self.reply(store.open_editor(form["id"], form["token"]))
            elif path == "/api/editor/close":
                store.close_editor(form["id"], form["token"])
                agent.wake.set()
            elif path == "/api/delete":
                cleaned = store.delete(form["id"], form["revision"])
                agent.wake.set()
                return self.reply({"ok": True, "cleaned": cleaned})
            elif path == "/api/retry":
                store.retry(form["id"])
                agent.wake.set()
            elif path == "/api/resolve":
                store.resolve(form["id"], form["index"], form["revision"], form["resolved"])
            elif path == "/api/duration":
                capture.set_duration(form["seconds"])
            elif path == "/api/capture-settings":
                capture.set_settings(form["seconds"], form["hotkeys"])
            elif path == "/api/reasoning-effort":
                store.set_reasoning_effort(form.get("reasoningEffort"))
            elif path == "/api/connect":
                agent.connect()
            elif path == "/api/disconnect":
                store.save_config(agent=False)
                agent.wake.set()
            elif path == "/api/folder-picker":
                return self.reply({"folder": choose_folder()})
            elif path == "/api/open-folder":
                folder = Path(store.project(form["project"])["folder"])
                os.startfile(str(folder))
            elif path == "/api/quit":
                self.reply({"ok": True})
                threading.Thread(target=srv.shutdown, daemon=True).start()
                return
            else:
                return self.reply({"error": "页面不存在。"}, status=404)
            return self.reply({"ok": True})
        self.reply({"error": "页面不存在。"}, status=404)

    def handle_route(self, method):
        try:
            self.route(method)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Conflict as exc:
            self.reply({"error": str(exc)}, status=409)
        except KeyError as exc:
            self.reply({"error": str(exc.args[0])}, status=404)
        except (ValueError, TypeError, UnicodeError) as exc:
            self.reply({"error": str(exc)}, status=400)
        except Exception as exc:
            logging.exception("工作台请求失败")
            self.reply({"error": "操作失败：" + str(exc)}, status=500)

    def do_GET(self):
        self.handle_route("GET")

    def do_POST(self):
        self.handle_route("POST")


def recover_loop(store, stop):
    while not stop.is_set():
        try:
            store.recover_inbox()
        except OSError:
            logging.exception("读取截图收件箱失败")
        stop.wait(8)


def main():
    parser = argparse.ArgumentParser(description="LQA Helper")
    parser.add_argument("--data", type=Path, default=ROOT / ".lqa")
    parser.add_argument("--port", type=int, default=47628)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--no-sharex", action="store_true")
    args = parser.parse_args()
    base = f"http://127.0.0.1:{args.port}"
    try:
        srv = WorkspaceServer(("127.0.0.1", args.port), Handler)
    except OSError:
        try:
            client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with client.open(base + "/health", timeout=15) as response:
                if json.load(response).get("app") == "lqa-helper":
                    if not args.no_browser:
                        webbrowser.open(base)
                    return
        except (OSError, ValueError):
            pass
        raise
    with srv:
        srv.base = base
        srv.store = Store(args.data)
        srv.agent = Agent(srv.store)
        srv.capture = Capture(srv.store, base, enabled=not args.no_sharex)
        srv.capture.start()
        worker = threading.Thread(target=srv.agent.run, daemon=True)
        worker.start()
        threading.Thread(target=recover_loop, args=(srv.store, srv.agent.stop), daemon=True).start()
        print(f"LQA Helper  {base}\nCtrl+C to quit.", flush=True)
        if not args.no_browser:
            webbrowser.open(base)
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            srv.agent.stop.set()
            srv.agent.wake.set()
            srv.capture.close()
            worker.join()


if __name__ == "__main__":
    main()
