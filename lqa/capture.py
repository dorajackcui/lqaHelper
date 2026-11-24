import subprocess
import threading
import ctypes
import time
from ctypes import wintypes
from pathlib import Path

from .hotkeys import JOBS, LABELS, check_available, sharex_info, validate_hotkeys
from .store import MAX_CAPTURE_BYTES, PREVIEW_FADE_SECONDS, write_json


ROOT = Path(__file__).resolve().parent.parent
SHAREX = ROOT / "vendor" / "ShareX" / "ShareX.exe"
VERSION = "17.1.0"


def tie_to_launcher(process):
    """Close this private capture engine if Windows closes the launch console."""
    class BasicLimits(ctypes.Structure):
        _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                    ("flags", wintypes.DWORD), ("working_min", ctypes.c_size_t),
                    ("working_max", ctypes.c_size_t), ("active", wintypes.DWORD),
                    ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                    ("scheduling", wintypes.DWORD)]

    class Limits(ctypes.Structure):
        _fields_ = [("basic", BasicLimits), ("io_counters", ctypes.c_uint64 * 6),
                    ("memory_limits", ctypes.c_size_t * 4)]

    kernel = ctypes.windll.kernel32
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        return None
    handle = wintypes.HANDLE(job)
    limits = Limits()
    # Kill only the engine. Browsers it opens may outlive the launcher.
    limits.basic.flags = 0x2000 | 0x1000
    if (kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits))
            and kernel.AssignProcessToJobObject(handle, wintypes.HANDLE(int(process._handle)))):
        return handle
    kernel.CloseHandle(handle)
    return None


def write_profile(directory, inbox, base, secret, seconds, hotkeys):
    # This belongs to the bundled portable copy. The user's installed ShareX has its own profile.
    pattern = "LQA_%y%mo%d_%h%mi%s_%ra{12}"
    write_json(directory / "ApplicationConfig.json", {
        "ApplicationVersion": VERSION, "ShowUploadWarning": False,
        "AutoCheckUpdate": False, "ShowLargeFileSizeWarning": 0, "ShowTray": True,
        "SilentRun": True, "FirstTimeMinimizeToTray": False, "UseCustomScreenshotsPath": True,
        "CustomScreenshotsPath": str(inbox), "SaveImageSubFolderPattern": "",
        "SaveImageSubFolderPatternWindow": "", "TrayLeftClickAction": JOBS["region"],
        "TrayMiddleClickAction": "None",
        "DefaultTaskSettings": {
            "AfterCaptureJob": "SaveImageToFile, UploadImageToHost", "AfterUploadJob": "None",
            "ImageDestination": "CustomImageUploader",
            # ShareX measures the encoded PNG in decimal kB before switching to JPEG.
            "ImageSettings": {"ImageFormat": "PNG", "ImageAutoUseJPEG": True,
                              "ImageAutoUseJPEGSize": MAX_CAPTURE_BYTES // 1000},
            "UploadSettings": {"NameFormatPattern": pattern, "NameFormatPatternActiveWindow": pattern},
            "GeneralSettings": {
                "ShowToastNotificationAfterTaskCompleted": True, "ToastWindowPlacement": "TopRight",
                "ToastWindowDuration": seconds, "ToastWindowFadeDuration": PREVIEW_FADE_SECONDS,
                "ToastWindowLeftClickAction": "OpenUrl", "ToastWindowRightClickAction": "CloseNotification",
                "ToastWindowMiddleClickAction": "OpenUrl", "DisableNotificationsOnFullscreen": False,
                "PlaySoundAfterCapture": False, "PlaySoundAfterUpload": False,
            },
        },
    })
    write_json(directory / "HotkeysConfig.json", {
        "ApplicationVersion": VERSION,
        "Hotkeys": [{"HotkeyInfo": sharex_info(hotkeys[action]),
                     "TaskSettings": {"Job": job, "Description": "LQA · " + LABELS[action]}}
                    for action, job in JOBS.items()],
    })
    write_json(directory / "UploadersConfig.json", {
        "ApplicationVersion": VERSION, "CustomImageUploaderSelected": 0,
        "CustomUploadersList": [{
            "Version": VERSION, "Name": "LQA Helper (localhost)", "DestinationType": "ImageUploader",
            "RequestMethod": "POST", "RequestURL": base + "/capture", "Body": "Binary",
            "Headers": {"X-LQA-Key": secret}, "Parameters": {"name": "{filename}"}, "URL": "{response}",
        }],
    })


def verify_registration(process, log_dir, offsets, hotkeys):
    # The bundled version logs each RegisterHotKey result before its startup marker.
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise OSError("ShareX 在注册快捷键前已退出。")
        for path in log_dir.glob("ShareX-Log-*.txt"):
            offset = offsets.get(path, 0)
            try:
                if path.stat().st_size <= offset:
                    continue
                with path.open("rb") as log:
                    log.seek(offset)
                    lines = log.read().decode("utf-8-sig", errors="replace").splitlines()
            except (FileNotFoundError, PermissionError):
                continue
            if not any(line.endswith("HotkeyManager started.") for line in lines):
                continue
            for action, job in JOBS.items():
                if not any(" - Hotkey registered: " in line and line.endswith(", Job: " + job) for line in lines):
                    raise ValueError(f"{LABELS[action]}的 {hotkeys[action]} 未能注册，请换一个快捷键。")
            return
        time.sleep(0.1)
    raise OSError("等待 ShareX 注册快捷键超时，请重新保存截屏设置。")


class Capture:
    def __init__(self, store, base, enabled=True):
        self.store, self.base, self.enabled = store, base, enabled
        self.process = None
        self.job = None
        self.lock = threading.RLock()
        self.error = ""

    @property
    def ready(self):
        return self.process is not None and self.process.poll() is None

    def _launch(self, seconds, hotkeys):
        if not SHAREX.exists():
            raise OSError("未找到 ShareX 便携版，请运行启动脚本完成安装。")
        directory = SHAREX.parent / "ShareX"
        write_profile(directory, self.store.inbox, self.base, self.store.config["secret"], seconds, hotkeys)
        logs = directory / "Logs"
        offsets = {path: path.stat().st_size for path in logs.glob("ShareX-Log-*.txt")}
        # In 17.1 -NoHotkeys suppresses only the modal registration warning.
        self.process = subprocess.Popen([str(SHAREX), "-portable", "-multi", "-silent", "-NoHotkeys"],
                                        cwd=SHAREX.parent)
        self.job = tie_to_launcher(self.process)
        verify_registration(self.process, logs, offsets, hotkeys)
        self.error = ""

    def start(self):
        with self.lock:
            if self.ready or not self.enabled or not self.store.config["active"]:
                return
            try:
                self._launch(self.store.config["seconds"], self.store.config["hotkeys"])
            except (OSError, ValueError) as exc:
                self.error = str(exc)

    def close(self):
        with self.lock:
            if self.ready:
                self.process.terminate()
                self.process.wait(timeout=8)
            if self.job:
                ctypes.windll.kernel32.CloseHandle(self.job)
                self.job = None

    def set_duration(self, seconds):
        with self.lock:
            self.set_settings(seconds, self.store.config["hotkeys"])

    def set_settings(self, seconds, hotkeys):
        if type(seconds) is not int or not 1 <= seconds <= 30:
            raise ValueError("缩略图停留时间为 1 到 30 秒。")
        hotkeys = validate_hotkeys(hotkeys)
        with self.lock:
            cfg = self.store.config
            if cfg["seconds"] == seconds and cfg["hotkeys"] == hotkeys and not self.error:
                if self.ready or not self.enabled or not cfg["active"]:
                    return
            if self.enabled:
                check_available(hotkeys, cfg["hotkeys"].values() if self.ready else ())
            if not self.enabled or not cfg["active"]:
                self.store.save_config(seconds=seconds, hotkeys=hotkeys)
                return
            self.close()
            try:
                self._launch(seconds, hotkeys)
                self.store.save_config(seconds=seconds, hotkeys=hotkeys)
            except (OSError, ValueError):
                self.close()
                self.start()
                raise
