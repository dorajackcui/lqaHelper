import base64
import csv
import io
import json
import struct
import sys
import tempfile
import threading
import unittest
import urllib.request
import uuid
import zipfile
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from app import Handler, WorkspaceServer, csv_report, save_project_upload
from lqa.capture import Capture
from lqa.agent import Agent
from lqa.agent import analyze, run_review_cli, validate_report
from lqa.store import Conflict, EDITOR_LEASE_SECONDS, PREVIEW_FADE_SECONDS, REASONING_EFFORTS, Store


def png(color, width=1, height=1):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress((b"\0" + bytes(color)*width)*height)) + chunk(b"IEND", b""))


ISSUE = {"category": "Overflow", "severity": "major", "description": "按钮文字超框",
         "sourceText": "开始任务", "suggestion": "增加按钮宽度", "reference": "menu.start"}


class Workflow(unittest.TestCase):
    def setUp(self):
        clock = patch("lqa.store.time.time", return_value=1000)
        self.clock = clock.start()
        self.addCleanup(clock.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root)
        self.proj = self.store.save_project({"name": "菜单"})
        self.image = png([0, 128, 128])

    def claim_after_preview(self):
        self.clock.return_value += self.store.config["seconds"] + 1
        return self.store.claim()

    def test_burst_duplicate_callback_and_late_project_switch(self):
        born = self.store.config["sessions"][0][0] + .001
        with patch("lqa.store.time.time", return_value=born + 30):
            second = self.store.save_project({"name": "道具"})
        a, b = [self.store.add(self.image, n, sharex=True, captured=born)
                for n in ["LQA_alpha1.png", "LQA_alpha2.png"]]
        self.assertNotEqual(a, b)
        self.assertEqual(self.store.add(self.image, "LQA_alpha1.png", sharex=True, captured=born+40), a)
        c = self.store.add(self.image, "LQA_alpha3.png", sharex=True, captured=born+33)
        self.assertEqual([self.store.shots[k]["project"] for k in (a, b, c)], [self.proj, self.proj, second])

    def test_stranded_jpeg_returns_to_its_original_project_and_stays_deleted(self):
        raw = (Path(__file__).parent / "fixtures/capture.jpg").read_bytes()
        stranded = self.store.inbox / "LQA_failed1.JPG"
        stranded.write_bytes(raw[:-2])
        born = stranded.stat().st_ctime
        self.clock.return_value = born + 20
        self.store.save_project({"name": "之后切换的项目"})
        with self.assertLogs(level="WARNING"):
            self.store.recover_inbox()
        self.assertIn(stranded.name, self.store.inbox_errors[0])
        self.assertIn("JPG", self.store.inbox_errors[0])
        self.assertFalse(self.store.shots)
        stranded.write_bytes(raw)
        with patch.object(self.store, "add", side_effect=FileNotFoundError("归档目录不可用")), self.assertLogs(level="WARNING"):
            self.store.recover_inbox()
        self.assertIn("归档目录不可用", self.store.inbox_errors[0])
        self.store.recover_inbox()
        self.assertFalse(self.store.inbox_errors)
        shot, = self.store.shots.values()
        key = shot["id"]
        self.assertEqual((shot["project"], shot["size"], shot["original"]), (self.proj, [13, 9], "original.jpg"))
        self.assertEqual(shot["created"], stranded.stat().st_ctime)
        folder = self.store.folder(shot)
        self.assertEqual((folder / "original.jpg").read_bytes(), raw)
        self.assertEqual(self.store.add(raw, stranded.name, sharex=True), key)
        self.store.recover_inbox()
        self.assertEqual(len(self.store.shots), 1)
        project = self.store.project(self.proj)
        def inspect(args, **options):
            from subprocess import CompletedProcess
            attached = Path(args[args.index("--image") + 1])
            self.assertEqual((attached.name, attached.read_bytes()), ("original.jpg", raw))
            (options["cwd"] / "result.json").write_text('{"summary":"OK","issues":[]}', encoding="utf-8")
            return CompletedProcess(args, 0, None, b"")
        with patch("lqa.agent.run_review_cli", side_effect=inspect):
            self.assertEqual(analyze("codex", shot, project, folder)["summary"], "OK")
        self.assertTrue(self.store.delete(key, 1))
        self.assertFalse(folder.exists() or stranded.exists())
        restored = Store(self.root)
        self.assertIsNone(restored.add(raw, stranded.name, sharex=True))

    def test_native_jpeg_callback_preview_and_png_annotations(self):
        raw = (Path(__file__).parent / "fixtures/capture.jpg").read_bytes()
        client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with WorkspaceServer(("127.0.0.1", 0), Handler) as srv:
            srv.base = f"http://127.0.0.1:{srv.server_port}"
            srv.store, srv.agent = self.store, Agent(self.store)
            srv.capture = Capture(self.store, srv.base, enabled=False)
            with self.assertRaises(OSError):
                with WorkspaceServer(srv.server_address, Handler):
                    pass
            serving = threading.Thread(target=srv.serve_forever, daemon=True)
            serving.start()
            try:
                req = urllib.request.Request(srv.base + "/capture?name=LQA_sample1.jpeg", data=raw,
                                             headers={"X-LQA-Key": self.store.config["secret"]})
                with client.open(req, timeout=3) as response:
                    key = response.read().decode().split("/#shot=")[1]
                with client.open(srv.base + "/image/" + key + "/original", timeout=3) as image:
                    self.assertEqual((image.headers.get_content_type(), image.read()), ("image/jpeg", raw))
                cut = png([250, 20, 20], width=5, height=3)
                self.store.edit(key, dict(revision=1, crop=dict(x=0, y=0, width=5, height=3),
                                          rendered=base64.b64encode(cut).decode()))
                with client.open(srv.base + "/image/" + key + "/marked", timeout=3) as image:
                    self.assertEqual((image.headers.get_content_type(), image.read()), ("image/png", cut))
            finally:
                srv.shutdown()
                serving.join(3)

    def test_inflight_annotations_restart_retry_and_csv(self):
        key = self.store.add(self.image, "截图.png", self.proj)
        old, _ = self.claim_after_preview()
        marked = png([238, 32, 32])
        marks = [{"type":"rect", "x1":.1, "y1":.2, "x2":.9, "y2":.8}]
        edit = dict(revision=1, tags=["超框"], note="需要处理", marks=marks,
                    rendered=base64.b64encode(marked).decode())
        self.store.edit(key, edit)
        self.store.complete(key, old["revision"], {"summary":"旧结果", "issues":[ISSUE]})
        self.assertEqual(self.store.shots[key]["status"], "queued")
        with self.assertRaises(Conflict):
            self.store.edit(key, edit)
        self.store.claim()
        self.store = Store(self.root)
        current, _ = self.store.claim()
        folder = self.store.folder(current)
        self.assertEqual((folder / current["original"]).read_bytes(), self.image)
        self.assertEqual((folder / current["marked"]).read_bytes(), marked)
        self.store.complete(key, current["revision"], error="离线")
        self.assertEqual(self.store.shots[key]["issues"], [])
        self.store.retry(key)
        current, _ = self.store.claim()
        self.store.complete(key, current["revision"], {"summary":"1 个问题", "issues":[ISSUE]})
        self.store.resolve(key, 0, 2, True)
        self.store.retry(key)
        current, _ = self.store.claim()
        self.store.complete(key, current["revision"], {"summary":"1 个问题", "issues":[ISSUE]})
        report = list(csv.reader(io.StringIO(csv_report(self.store, self.proj).decode("utf-8-sig"))))
        self.assertEqual(len(report), 2)
        self.assertEqual(report[1][0], "已处理")
        self.assertEqual(report[1][7:9], ["超框", "需要处理"])
        self.assertTrue(Path(report[1][-1]).is_file())
        archive = json.loads((folder / "capture.json").read_text("utf-8"))
        self.assertEqual(archive["analyzedRevision"], archive["revision"])
        self.assertEqual(archive["marks"], marks)

    def test_preview_and_each_open_editor_defer_only_their_own_shot(self):
        self.store.save_config(seconds=6)
        key = self.store.add(self.image, "LQA_preview.png", sharex=True)
        other = self.store.add(self.image, "other.png", self.proj)
        self.store = Store(self.root)
        self.clock.return_value += 6
        self.assertIsNone(self.store.claim())
        a, b = str(uuid.uuid4()), str(uuid.uuid4())
        self.store.open_editor(key, a)
        self.store.open_editor(key, b)
        self.store = Store(self.root)
        self.clock.return_value += PREVIEW_FADE_SECONDS + 1
        self.assertEqual(self.store.claim()[0]["id"], other)
        self.store.edit(key, {"revision": 1, "note": "最终备注"})
        self.store.close_editor(key, a)
        self.assertIsNone(self.store.claim())
        self.store.close_editor(key, b)
        shot, _ = self.store.claim()
        self.assertEqual((shot["id"], shot["revision"], shot["note"]), (key, 2, "最终备注"))
        self.store.open_editor(key, a)
        self.assertFalse(self.store.is_running(key, 2))
        self.store.complete(key, 2, {"summary": "过期结果", "issues": [ISSUE]})
        self.assertFalse(self.store.shots[key]["issues"])
        self.assertIsNone(self.store.claim())
        self.store.close_editor(key, a)
        self.assertEqual(self.store.claim()[0]["id"], key)

    def test_abandoned_editor_expires_after_its_last_heartbeat(self):
        key = self.store.add(self.image, "menu.png", self.proj)
        token = str(uuid.uuid4())
        self.clock.return_value += 10
        self.store.open_editor(key, token)
        self.clock.return_value += EDITOR_LEASE_SECONDS - 1
        self.store.open_editor(key, token)
        self.clock.return_value += 2
        self.assertIsNone(self.store.claim())
        self.clock.return_value += EDITOR_LEASE_SECONDS
        self.assertEqual(self.store.claim()[0]["id"], key)

    def test_deleted_native_capture_stays_deleted_after_upload_replay_and_restart(self):
        name = "LQA_mistake.png"
        inbox = self.store.inbox / name
        inbox.write_bytes(self.image)
        key = self.store.add(self.image, name, sharex=True)
        shot, _ = self.claim_after_preview()
        folder = self.store.folder(shot)
        self.store.complete(key, 1, {"summary": "问题", "issues": [ISSUE]})
        other = self.store.add(self.image, name, self.proj)
        self.assertIn(b"menu.start", csv_report(self.store, self.proj))
        self.assertTrue(self.store.delete(other, 1))
        self.assertTrue(inbox.is_file())
        (folder / "marked-2.png").write_bytes(self.image)
        self.store.open_editor(key, str(uuid.uuid4()))
        self.assertTrue(self.store.delete(key, 1))
        self.assertFalse(inbox.exists())
        self.assertFalse(folder.exists())
        self.assertTrue(self.store.delete(key, 1))
        self.assertNotIn(b"menu.start", csv_report(self.store, self.proj))
        self.store = Store(self.root)
        inbox.write_bytes(self.image)
        self.store.recover_inbox()
        self.assertFalse(inbox.exists())
        self.assertIsNone(self.store.add(self.image, name, sharex=True))
        self.assertIsNone(self.store.claim())

    def test_delete_ignores_late_results_and_recovers_interrupted_cleanup(self):
        key = self.store.add(self.image, "menu.png", self.proj)
        self.store.edit(key, {"revision": 1, "note": "另一个窗口已保存"})
        with self.assertRaises(Conflict):
            self.store.delete(key, 1)
        shot, _ = self.claim_after_preview()
        folder = self.store.folder(shot)
        keep = folder / "user-file.txt"
        keep.write_text("keep")
        with patch("lqa.store.finish_deletion", side_effect=PermissionError("busy")), self.assertLogs(level="WARNING"):
            self.assertFalse(self.store.delete(key, 2))
        self.store.complete(key, 2, {"summary": "迟到的结果", "issues": [ISSUE]})
        self.store.complete(key, 2, error="已退出")
        self.assertNotIn(key, self.store.shots)
        self.store = Store(self.root)
        self.assertEqual(list(folder.iterdir()), [keep])
        self.assertIsNone(self.store.claim())

    def test_cancel_running_cli_and_accept_a_following_job(self):
        marker = self.root / "started"
        slow = "import pathlib, sys, time; pathlib.Path('started').write_bytes(sys.stdin.buffer.read()); time.sleep(20)"
        result = run_review_cli([sys.executable, "-c", slow], input=b"test", cwd=self.root,
                                current=lambda: not marker.is_file())
        self.assertIsNone(result)
        self.assertEqual(marker.read_bytes(), b"test")
        normal = "import sys, time; time.sleep(.7); sys.stderr.buffer.write(sys.stdin.buffer.read())"
        result = run_review_cli([sys.executable, "-c", normal], input=b"next", cwd=self.root, current=lambda: True)
        self.assertEqual((result.returncode, result.stderr), (0, b"next"))

    def test_full_reference_and_image_are_given_to_cli(self):
        raw = ("id,source,target\n" + "\n".join(f"line{i},Start,开始" for i in range(2500))).encode()
        self.store.save_project(dict(id=self.proj, name="菜单", prompt="检查术语",
            reference=dict(name="简体中文.csv", data=base64.b64encode(raw).decode())))
        key = self.store.add(self.image, "fixture.png", self.proj)
        shot, project = self.claim_after_preview()
        def fake_cli(args, **kwargs):
            self.assertEqual(args[args.index("--model") + 1], "gpt-6.2-sol")
            self.assertEqual(args[args.index("--config") + 1], 'model_reasoning_effort="xhigh"')
            self.assertIn("read-only", args)
            self.assertIn("--image", args)
            self.assertEqual(Path(args[args.index("--image")+1]).read_bytes(), self.image)
            self.assertIn("检查术语", kwargs["input"].decode())
            self.assertIn("line2499", (kwargs["cwd"] / "reference.csv").read_text("utf-8"))
            Path(args[args.index("--output-last-message")+1]).write_text(
                json.dumps({"summary":"复核完毕", "issues":[ISSUE]}), encoding="utf-8")
            import subprocess
            return subprocess.CompletedProcess(args, 0, None, b"")
        with patch("lqa.agent.run_review_cli", side_effect=fake_cli):
            result = analyze("codex", shot, project, self.store.folder(shot))
        self.store.complete(key, shot["revision"], result)
        self.assertEqual(len(self.store.shots[key]["issues"]), 1)

    def test_ephemeral_review_uses_the_effort_selected_when_it_starts(self):
        legacy = json.loads(self.store.config_path.read_text("utf-8"))
        del legacy["reasoningEffort"]
        self.store.config_path.write_text(json.dumps(legacy), encoding="utf-8")
        self.store = Store(self.root)
        self.assertEqual(self.store.config["reasoningEffort"], "xhigh")
        first = self.store.add(self.image, "first.png", self.proj)
        second = self.store.add(self.image, "second.png", self.proj)
        self.store.set_reasoning_effort("low")
        shot, project = self.claim_after_preview()
        self.store.set_reasoning_effort("high")
        def inspect(args, **kwargs):
            from subprocess import CompletedProcess
            self.assertIn("--ephemeral", args)
            self.assertEqual(args[args.index("--model") + 1], "gpt-6.2-sol")
            self.assertEqual(args[args.index("--config") + 1], 'model_reasoning_effort="low"')
            (kwargs["cwd"] / "result.json").write_text('{"summary":"OK","issues":[]}', encoding="utf-8")
            return CompletedProcess(args, 0, None, b"")
        with patch("lqa.agent.run_review_cli", side_effect=inspect):
            report = analyze("codex", shot, project, self.store.folder(shot))
        self.store.complete(first, shot["revision"], report)
        saved = json.loads((self.store.folder(shot) / "capture.json").read_text("utf-8"))
        self.assertEqual((saved["id"], saved["reasoningEffort"]), (first, "low"))
        following, _ = self.store.claim()
        self.assertEqual((following["id"], following["reasoningEffort"]), (second, "high"))
        self.store.complete(second, following["revision"], report)
        for level in REASONING_EFFORTS:
            with self.subTest(level=level):
                self.store.set_reasoning_effort(level)
                self.store.retry(first)
                retry, _ = self.store.claim()
                self.assertEqual((retry["id"], retry["reasoningEffort"]), (first, level))
                self.store.complete(first, retry["revision"], report)
        self.store.set_reasoning_effort("medium")
        for invalid in ("unknown", None, []):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "推理强度"):
                self.store.set_reasoning_effort(invalid)
        self.assertEqual(Store(self.root).config["reasoningEffort"], "medium")

    def test_excel_glossaries_reach_the_agent_with_sheet_and_original_row(self):
        fixtures = Path(__file__).parent / "fixtures"
        xlsx = (fixtures / "glossary.xlsx").read_bytes()
        xlsm = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(xlsx)) as source, zipfile.ZipFile(xlsm, "w") as target:
            for member in source.infolist():
                data = source.read(member)
                if member.filename == "[Content_Types].xml":
                    data = data.replace(b"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
                                        b"application/vnd.ms-excel.sheet.macroEnabled.main+xml")
                target.writestr(member, data)
        for ext, raw in (("XLSX", xlsx), ("xls", (fixtures / "glossary.xls").read_bytes()), ("xlsm", xlsm.getvalue())):
            with self.subTest(ext=ext):
                name = "游戏 术语." + ext
                self.store.save_project(dict(id=self.proj, name="菜单", reference={
                    "name": name, "data": base64.b64encode(raw).decode()}))
                self.store = Store(self.root)
                self.store.add(self.image, "菜单.png", self.proj)
                shot, project = self.claim_after_preview()
                ref = project["reference"]
                directory = Path(project["folder"]) / "references"
                self.assertEqual((directory / ref["file"]).read_bytes(), raw)
                with (directory / ref["textFile"]).open(encoding="utf-8", newline="") as index:
                    rows = list(csv.reader(index, dialect="excel-tab"))
                self.assertIn(["设置", "2", "menu.settings", "Settings", "游戏设置"], rows)
                self.assertIn(["菜单词条", "5", "", "menu.new_game", "Start a new game", "开始新的游戏",
                               "按钮\t备注\n第二行", "Start a new game!"], rows)
                self.assertIn(["菜单词条", "7", "", "00042", "00009", "FALSE", "保留首尾空格 "], rows)

                def inspect(args, **options):
                    import subprocess
                    context = json.loads(options["input"].decode().splitlines()[-1])["reference"]
                    self.assertEqual((context["name"], context["path"]), (name, "reference.tsv"))
                    self.assertIn("menu.new_game", context["text"])
                    self.assertEqual((options["cwd"] / context["path"]).read_bytes(),
                                     (directory / ref["textFile"]).read_bytes())
                    (options["cwd"] / "result.json").write_text('{"summary":"OK","issues":[]}', encoding="utf-8")
                    return subprocess.CompletedProcess(args, 0, None, b"")
                with patch("lqa.agent.run_review_cli", side_effect=inspect):
                    report = analyze("codex", shot, project, self.store.folder(shot))
                self.store.complete(shot["id"], shot["revision"], report)

    def test_invalid_excel_replacement_keeps_the_existing_reference(self):
        raw = (Path(__file__).parent / "fixtures/glossary.xlsx").read_bytes()
        form = dict(id=self.proj, name="菜单", reference={"name": "菜单.xlsx", "data": base64.b64encode(raw).decode()})
        self.store.save_project(form)
        project = self.store.project(self.proj)
        directory = Path(project["folder"]) / "references"
        before = {p.name: p.read_bytes() for p in directory.iterdir()}
        for name in ("invalid.xlsx", "invalid.xls"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "无法读取 Excel"):
                self.store.save_project(dict(form, reference={"name": name, "data": base64.b64encode(b"bad").decode()}))
        self.assertEqual(Store(self.root).project(self.proj)["reference"], project["reference"])
        self.assertEqual({p.name: p.read_bytes() for p in directory.iterdir()}, before)

    def test_multipart_archive_across_short_reads_and_interrupted_replacement(self):
        folder = self.root / "手选项目"
        meta = {"name": "菜单", "folder": str(folder)}
        raw = (Path(__file__).parent / "fixtures/glossary.xlsx").read_bytes()
        boundary = "abc-123"

        def request(form):
            return (b'--abc-123\r\nContent-Disposition: form-data; name="project"\r\n\r\n'
                    + json.dumps(form).encode() +
                    '\r\n--abc-123\r\nContent-Disposition: form-data; name="reference"; filename="本地化.xlsx"\r\n'
                    'Content-Type: application/octet-stream\r\n\r\n'.encode() + raw + b'\r\n--abc-123--\r\n')

        class ShortReads(io.BytesIO):
            def read(self, size=-1):
                if not 0 <= size <= 1024 * 1024:
                    raise AssertionError("A bounded chunk must be requested")
                return super().read(min(size, 47))

        body = request(meta)
        pid = save_project_upload(self.store, ShortReads(body), boundary, len(body))
        ref = Store(self.root).project(pid)["reference"]
        self.assertEqual(ref["name"], "本地化.xlsx")
        self.assertEqual({p.name for p in folder.iterdir()}, {"captures", "references"})
        references = folder / "references"
        self.assertEqual((references / ref["file"]).read_bytes(), raw)
        self.assertIn("开始新的游戏", (references / ref["textFile"]).read_text("utf-8"))

        before = self.store.config_path.read_bytes()
        replacement = request({"id": pid, "name": "被中断的修改"})
        with self.assertRaisesRegex(ValueError, "传输不完整"):
            save_project_upload(self.store, ShortReads(replacement[:-16]), boundary, len(replacement))
        self.assertEqual(self.store.config_path.read_bytes(), before)
        self.assertEqual({p.name for p in references.iterdir()}, {ref["file"], ref["textFile"]})

    def test_screenshot_can_be_archived_during_reference_upload(self):
        folder = Path(self.store.project(self.proj)["folder"])
        self.assertTrue((folder / "captures").is_dir() and (folder / "references").is_dir())
        transferring, resume = threading.Event(), threading.Event()

        def chunks():
            yield b"id,source,target\r\n"
            transferring.set()
            if not resume.wait(8):
                raise TimeoutError("The test did not release the upload")
            yield "menu,Start,开始\r\n".encode()

        with ThreadPoolExecutor(max_workers=2) as workers:
            saving = workers.submit(self.store.save_project, {"id": self.proj, "name": "菜单"},
                                    reference_upload=("菜单.csv", chunks()))
            try:
                self.assertTrue(transferring.wait(3))
                key = workers.submit(self.store.add, self.image, "during-upload.png", self.proj).result(timeout=3)
                self.assertTrue((folder / "captures" / key / "capture.json").is_file())
                self.assertIsNone(self.store.project(self.proj)["reference"])
            finally:
                resume.set()
            self.assertEqual(saving.result(timeout=4), self.proj)
        self.assertEqual(self.store.project(self.proj)["reference"]["name"], "菜单.csv")

    def test_loading_a_legacy_project_creates_its_missing_reference_directory(self):
        folder = Path(self.store.project(self.proj)["folder"])
        (folder / "references").rmdir()
        key = self.store.add(self.image, "existing.png", self.proj)
        restored = Store(self.root)
        self.assertTrue((folder / "references").is_dir())
        self.assertEqual(restored.folder(restored.shots[key]), folder / "captures" / key)

    def test_streamed_text_preserves_newlines_and_falls_back_to_gb18030(self):
        text = "id\t原文\r\n" + ("x\t1\r\n" * 80000) + "last\t术语𠀀\r\n"
        for encoding in ("gb18030", "utf-8-sig"):
            with self.subTest(encoding=encoding):
                # The first 300 KiB are ASCII, including part of the second decoding buffer.
                contents = "x" * (300 * 1024) + text
                raw = contents.encode(encoding)
                blocks = (raw[i:i + 37] for i in range(0, len(raw), 37))
                self.store.save_project({"id": self.proj, "name": "菜单"}, reference_upload=("词条.tsv", blocks))
                project = self.store.project(self.proj)
                stored = Path(project["folder"]) / "references" / project["reference"]["file"]
                self.assertEqual(stored.read_bytes(), contents.encode("utf-8"))

    def test_corrupt_capture_and_invalid_report(self):
        with self.assertRaises(ValueError):
            self.store.add(self.image[:-3], "partial.png", self.proj)
        self.assertFalse(self.store.shots)
        self.assertEqual(validate_report({"summary":"正常", "issues":[]})["issues"], [])
        with self.assertRaises(ValueError):
            validate_report({"summary":"失败", "issues":[{}]})

    def test_crop_evidence_reopen_agent_csv_and_restore(self):
        import subprocess
        original = png([0, 80, 60], 12, 8)
        cut = png([25, 80, 70], 6, 4)
        key = self.store.add(original, "已裁剪.png", self.proj)
        shot, project = self.claim_after_preview()
        crop = {"x": 2, "y": 1, "width": 6, "height": 4}
        change = dict(revision=shot["revision"], crop=crop, tags=["超框"], note="检查按钮",
                      marks=[], rendered=base64.b64encode(cut).decode())
        for bad_crop in [dict(crop, x=-1), dict(crop, width=0), dict(crop, x=7), dict(crop, y=True)]:
            with self.subTest(bad_crop), self.assertRaises(ValueError):
                self.store.edit(key, dict(change, crop=bad_crop))
        with self.assertRaises(ValueError):
            self.store.edit(key, dict(change, rendered=base64.b64encode(original).decode()))
        self.store.edit(key, change)
        self.store.complete(key, shot["revision"], {"summary":"旧图", "issues":[]})
        self.store = Store(self.root)
        updated, project = self.store.claim()
        folder = self.store.folder(updated)
        self.assertEqual(updated["crop"], crop)
        self.assertEqual((folder / updated["original"]).read_bytes(), original)

        def inspect_cli(args, **kwargs):
            attached = [Path(args[i+1]) for i, opt in enumerate(args) if opt == "--image"]
            self.assertEqual([p.read_bytes() for p in attached], [cut])
            self.assertFalse((kwargs["cwd"] / "original.png").exists())
            self.assertIn("检查按钮", kwargs["input"].decode())
            Path(args[args.index("--output-last-message")+1]).write_text(
                json.dumps({"summary":"复核", "issues":[ISSUE]}), encoding="utf-8")
            return subprocess.CompletedProcess(args, 0, None, b"")
        with patch("lqa.agent.run_review_cli", side_effect=inspect_cli):
            report = analyze("codex", updated, project, folder)
        self.store.complete(key, updated["revision"], report)
        rows = list(csv.reader(io.StringIO(csv_report(self.store, self.proj).decode("utf-8-sig"))))
        self.assertEqual(Path(rows[1][-1]).read_bytes(), cut)

        self.store.edit(key, {"revision":2, "marks":[], "tags":[], "note":"补备注"})
        self.assertEqual(self.store.shots[key]["marked"], updated["marked"])
        self.assertEqual(self.store.shots[key]["crop"], crop)
        self.store.edit(key, {"revision":3, "crop":None, "marks":[], "tags":[], "note":"补备注"})
        self.assertIsNone(self.store.shots[key]["marked"])
        self.assertEqual((folder / "original.png").read_bytes(), original)

    def test_existing_capture_without_crop_opens_at_full_size(self):
        key = self.store.add(self.image, "旧截图.png", self.proj)
        folder = self.store.folder(self.store.shots[key])
        sidecar = folder / "capture.json"
        legacy = json.loads(sidecar.read_text("utf-8"))
        del legacy["crop"]
        sidecar.write_text(json.dumps(legacy), encoding="utf-8")
        restored = Store(self.root)
        self.assertIsNone(restored.shots[key]["crop"])
        restored.edit(key, {"revision":1, "note":"旧截图的新备注"})
        self.assertEqual(restored.shots[key]["note"], "旧截图的新备注")


if __name__ == "__main__":
    unittest.main()
