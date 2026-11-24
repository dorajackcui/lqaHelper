import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lqa.capture import Capture, write_profile
from lqa.hotkeys import DEFAULT_HOTKEYS, LEGACY_HOTKEYS, sharex_info, validate_hotkeys
from lqa.store import MAX_CAPTURE_BYTES, Store, write_json


class HotkeySettings(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.store = Store(self.root)
        self.capture = Capture(self.store, "http://127.0.0.1:47628", enabled=False)

    def test_new_legacy_and_custom_keys_survive_restart(self):
        self.assertEqual(self.store.config["hotkeys"], DEFAULT_HOTKEYS)
        self.store.save_project({"name": "升级验收"})
        legacy = self.store.config.copy()
        del legacy["hotkeys"]
        write_json(self.store.config_path, legacy)
        upgraded = Store(self.root)
        self.assertEqual(upgraded.config, dict(legacy, hotkeys=LEGACY_HOTKEYS))
        capture = Capture(upgraded, "", enabled=False)
        capture.set_settings(5, {"window": " F6 ", "region": " shift + ctrl + q "})
        capture.set_duration(6)
        saved = Store(self.root).config
        self.assertEqual(saved["hotkeys"], {"window": "F6", "region": "Ctrl+Shift+Q"})
        self.assertEqual(saved["seconds"], 6)
        self.assertEqual(saved["active"], legacy["active"])

    def test_sharex_receives_the_selected_actions_and_modifiers(self):
        bindings = validate_hotkeys({"window": "prtSc", "region": "win+alt+ctrl+shift+Numpad3"})
        write_profile(self.root, self.store.inbox, self.capture.base, "test", 3, bindings)
        config = json.loads((self.root / "HotkeysConfig.json").read_text("utf-8"))
        shortcuts = {s["TaskSettings"]["Job"]: s["HotkeyInfo"] for s in config["Hotkeys"]}
        self.assertEqual(shortcuts, {"ActiveWindow": {"Hotkey": 0x2C, "Win": False},
                                     "RectangleLight": {"Hotkey": 0x70063, "Win": True}})
        app_config = json.loads((self.root / "ApplicationConfig.json").read_text("utf-8"))
        self.assertEqual(app_config["TrayLeftClickAction"], "RectangleLight")
        image = app_config["DefaultTaskSettings"]["ImageSettings"]
        self.assertEqual(image["ImageFormat"], "PNG")
        self.assertTrue(image["ImageAutoUseJPEG"])
        self.assertLess(MAX_CAPTURE_BYTES - image["ImageAutoUseJPEGSize"] * 1000, 1000)
        self.assertLessEqual(image["ImageAutoUseJPEGSize"] * 1000, MAX_CAPTURE_BYTES)
        self.assertEqual(sharex_info("F8"), {"Hotkey": 0x77, "Win": False})

    def test_invalid_or_occupied_keys_preserve_all_settings(self):
        for value in ["CTRL+ALT+F9", "Ctrl+", "Ctrl+Ctrl+Q", "F12", "ignored"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.capture.set_settings(9, {"window": value, "region": "Alt+Ctrl+F9"})
        self.capture.enabled = True
        with patch("lqa.capture.check_available", side_effect=ValueError("占用")), self.assertRaises(ValueError):
            self.capture.set_settings(12, {"window": "F6", "region": "F7"})
        self.assertEqual(Store(self.root).config["hotkeys"], DEFAULT_HOTKEYS)
        self.assertEqual(Store(self.root).config["seconds"], 3)

    def test_native_registration_failure_restores_previous_profile(self):
        self.store.save_project({"name": "回退"})
        self.capture.enabled = True
        selected = {"window": "F7", "region": "F8"}
        with patch("lqa.capture.check_available"), patch.object(self.capture, "_launch",
                side_effect=[ValueError("无法注册"), None]) as launch, self.assertRaises(ValueError):
            self.capture.set_settings(5, selected)
        self.assertEqual([call.args for call in launch.call_args_list], [(5, selected), (3, DEFAULT_HOTKEYS)])
        self.assertEqual(Store(self.root).config["hotkeys"], DEFAULT_HOTKEYS)
