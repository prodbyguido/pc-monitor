"""System-wide hotkeys: they work even while a game or another window is focused."""
import ctypes
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QTimer

WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0001, 0x0002, 0x4000
user32 = ctypes.windll.user32


class GlobalHotkeys(QAbstractNativeEventFilter):
    def __init__(self):
        super().__init__()
        self.callbacks = {}
        self.failed: list[str] = []

    def register(self, hotkey_id: int, key: str, callback, label: str):
        """Ctrl+Alt+<key>. If another app already uses it, it's listed in self.failed."""
        if user32.RegisterHotKey(None, hotkey_id, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, ord(key)):
            self.callbacks[hotkey_id] = callback
        else:
            self.failed.append(label)

    def unregister_all(self):
        for hotkey_id in self.callbacks:
            user32.UnregisterHotKey(None, hotkey_id)
        self.callbacks.clear()
        self.failed.clear()

    def nativeEventFilter(self, event_type, message):
        if bytes(event_type) in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and msg.wParam in self.callbacks:
                QTimer.singleShot(0, self.callbacks[msg.wParam])  # run outside the native handler
                return True, 0
        return False, 0
