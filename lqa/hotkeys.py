import ctypes


DEFAULT_HOTKEYS = {"window": "F8", "region": "F9"}
LEGACY_HOTKEYS = {"window": "Ctrl+Alt+F8", "region": "Ctrl+Alt+F9"}
JOBS = {"window": "ActiveWindow", "region": "RectangleLight"}
LABELS = {"window": "截取当前窗口", "region": "框选区域"}

# Windows RegisterHotKey flags and the corresponding Windows Forms Keys flags.
MODIFIERS = {"Ctrl": (2, 0x20000), "Alt": (1, 0x40000), "Shift": (4, 0x10000), "Win": (8, 0)}
KEYS = {
    **{f"F{i}": 0x6F + i for i in range(1, 25) if i != 12},
    "PrintScreen": 0x2C, "ScrollLock": 0x91, "Pause": 0x13,
    "Insert": 0x2D, "Delete": 0x2E, "Home": 0x24, "End": 0x23, "PageUp": 0x21, "PageDown": 0x22,
    "Left": 0x25, "Up": 0x26, "Right": 0x27, "Down": 0x28, "Space": 0x20, "Tab": 0x09,
    "Enter": 0x0D, "Escape": 0x1B, "Backspace": 0x08, "CapsLock": 0x14, "NumLock": 0x90,
    **{chr(i): i for i in range(65, 91)}, **{str(i): 0x30 + i for i in range(10)},
    **{f"Numpad{i}": 0x60 + i for i in range(10)},
    "NumpadMultiply": 0x6A, "NumpadAdd": 0x6B, "NumpadSubtract": 0x6D,
    "NumpadDecimal": 0x6E, "NumpadDivide": 0x6F,
    ";": 0xBA, "=": 0xBB, ",": 0xBC, "-": 0xBD, ".": 0xBE, "/": 0xBF, "`": 0xC0,
    "[": 0xDB, "\\": 0xDC, "]": 0xDD, "'": 0xDE,
}
NAMES = {k.casefold(): k for k in (*KEYS, *MODIFIERS)}
NAMES.update({"prtsc": "PrintScreen", "prtscn": "PrintScreen", "control": "Ctrl", "esc": "Escape", "del": "Delete"})


def normalize(value):
    if not isinstance(value, str) or len(value) > 75:
        raise ValueError("请输入快捷键，例如 F8 或 Ctrl+Q。")
    tokens = [part.strip().replace(" ", "").casefold() for part in value.split("+")]
    if "f12" in tokens:
        raise ValueError("F12 是 Windows 的调试保留键，请换一个键。")
    *mods, key = [NAMES.get(part) for part in tokens]
    if key not in KEYS or any(mod not in MODIFIERS for mod in mods) or len(set(mods)) != len(mods):
        raise ValueError("请输入一个主键，可搭配 Ctrl、Alt、Shift 或 Win，例如 F8 或 Ctrl+Q。")
    return "+".join([*(m for m in MODIFIERS if m in mods), key])


def validate_hotkeys(values):
    if not isinstance(values, dict) or values.keys() != JOBS.keys():
        raise ValueError("请分别设置截取当前窗口和框选区域的快捷键。")
    keys = {action: normalize(values[action]) for action in JOBS}
    if keys["window"] == keys["region"]:
        raise ValueError("两个截屏动作使用了同一个快捷键，请改其中一个。")
    return keys


def sharex_info(shortcut):
    *mods, key = shortcut.split("+")
    return {"Hotkey": KEYS[key] | sum(MODIFIERS[m][1] for m in mods), "Win": "Win" in mods}


def check_available(hotkeys, already_registered=()):
    # Probe only new chords so editing can exchange bindings owned by our running ShareX.
    user32 = ctypes.windll.user32
    for shortcut in hotkeys.values():
        if shortcut in already_registered:
            continue
        *mods, key = shortcut.split("+")
        if not user32.RegisterHotKey(None, 1, sum(MODIFIERS[m][0] for m in mods), KEYS[key]):
            raise ValueError(f"{shortcut} 无法注册，可能已被系统或其他应用占用。请换一个键。")
        user32.UnregisterHotKey(None, 1)
