"""Mouse and keyboard injection into the X display via XTEST."""

from __future__ import annotations

import threading

from Xlib import X, XK
from Xlib import display as xdisplay
from Xlib.ext import xtest

from .log import log

# Friendly names -> X11 keysym names.
KEY_ALIASES = {
    "ctrl": "Control_L",
    "control": "Control_L",
    "shift": "Shift_L",
    "alt": "Alt_L",
    "option": "Alt_L",
    "super": "Super_L",
    "meta": "Super_L",
    "cmd": "Super_L",
    "win": "Super_L",
    "enter": "Return",
    "return": "Return",
    "escape": "Escape",
    "esc": "Escape",
    "backspace": "BackSpace",
    "delete": "Delete",
    "del": "Delete",
    "tab": "Tab",
    "space": "space",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "home": "Home",
    "end": "End",
    "pageup": "Prior",
    "pagedown": "Next",
    "insert": "Insert",
    "capslock": "Caps_Lock",
    "print": "Print",
}

WHEEL_UP, WHEEL_DOWN, WHEEL_LEFT, WHEEL_RIGHT = 4, 5, 6, 7


class Input:
    """Translates browser events into X11 pointer/keyboard events."""

    def __init__(self, display_name: str, width: int, height: int) -> None:
        self.display_name = display_name
        self.width = width
        self.height = height
        self._lock = threading.Lock()
        self.d = xdisplay.Display(display_name)
        self.root = self.d.screen().root

        # keysym -> (keycode, shift level)
        self._keysyms: dict[int, tuple[int, int]] = {}
        for keycode in range(8, 256):
            for level in range(4):
                keysym = self.d.keycode_to_keysym(keycode, level)
                if keysym and keysym not in self._keysyms:
                    self._keysyms[keysym] = (keycode, level)

        self.shift_keycode = self._lookup_keycode(XK.XK_Shift_L)

    # -- resolution -----------------------------------------------------
    def _lookup_keycode(self, keysym: int) -> int | None:
        entry = self._keysyms.get(keysym)
        return entry[0] if entry else None

    def _name_keysym(self, name: str) -> int | None:
        if len(name) == 1:
            code = ord(name)
            if 0x20 <= code <= 0xFF:
                return code
        alias = KEY_ALIASES.get(name.lower(), name)
        keysym = XK.string_to_keysym(alias)
        return keysym or None

    def _char_keysym(self, char: str) -> int | None:
        code = ord(char)
        if code in (0x0A, 0x0D):
            return XK.XK_Return
        if code == 0x09:
            return XK.XK_Tab
        if 0x20 <= code <= 0xFF:
            return code
        return None

    # -- events ---------------------------------------------------------
    def move(self, x: int, y: int) -> None:
        x = max(0, min(self.width - 1, int(x)))
        y = max(0, min(self.height - 1, int(y)))
        with self._lock:
            xtest.fake_input(self.d, X.MotionNotify, x=x, y=y, root=self.root)
            self.d.flush()

    def button(self, button: int, pressed: bool) -> None:
        if not 1 <= button <= 7:
            return
        with self._lock:
            event = X.ButtonPress if pressed else X.ButtonRelease
            xtest.fake_input(self.d, event, button)
            self.d.flush()

    def scroll(self, dx: float, dy: float, step: int = 40) -> None:
        clicks_x = min(6, int(abs(dx) / step)) if dx else 0
        clicks_y = min(6, int(abs(dy) / step)) if dy else 0
        events: list[tuple[int, bool]] = []
        for _ in range(clicks_y):
            events.append((WHEEL_DOWN if dy > 0 else WHEEL_UP, True))
            events.append((WHEEL_DOWN if dy > 0 else WHEEL_UP, False))
        for _ in range(clicks_x):
            events.append((WHEEL_RIGHT if dx > 0 else WHEEL_LEFT, True))
            events.append((WHEEL_RIGHT if dx > 0 else WHEEL_LEFT, False))
        if not events:
            return
        with self._lock:
            for button, pressed in events:
                event = X.ButtonPress if pressed else X.ButtonRelease
                xtest.fake_input(self.d, event, button)
            self.d.flush()

    def type_text(self, text: str) -> None:
        """Type a string character by character."""
        if not text:
            return
        with self._lock:
            for char in text[:4096]:
                keysym = self._char_keysym(char)
                if keysym is None:
                    log.debug("skipping unsupported character %r", char)
                    continue
                self._tap_locked(keysym)
            self.d.flush()

    def combo(self, keys: list[str]) -> None:
        """Press a key combination, e.g. ["ctrl", "shift", "t"]."""
        if not keys:
            return

        sequence: list[tuple[int, int]] = []
        for name in keys[:6]:
            keysym = self._name_keysym(name)
            if keysym is None:
                continue
            entry = self._keysyms.get(keysym)
            if entry is None:
                log.debug("no keycode for %r", name)
                continue
            sequence.append(entry)

        if not sequence:
            return

        keycodes = [keycode for keycode, _ in sequence]
        needs_shift = any(level in (1, 3) for _, level in sequence)
        shift_needed = needs_shift and self.shift_keycode not in keycodes

        with self._lock:
            if shift_needed and self.shift_keycode:
                xtest.fake_input(self.d, X.KeyPress, self.shift_keycode)
            for keycode in keycodes:
                xtest.fake_input(self.d, X.KeyPress, keycode)
            for keycode in reversed(keycodes):
                xtest.fake_input(self.d, X.KeyRelease, keycode)
            if shift_needed and self.shift_keycode:
                xtest.fake_input(self.d, X.KeyRelease, self.shift_keycode)
            self.d.flush()

    def _tap_locked(self, keysym: int) -> None:
        entry = self._keysyms.get(keysym)
        if entry is None:
            log.debug("no keycode for keysym 0x%X", keysym)
            return
        keycode, level = entry
        shift = level in (1, 3) and self.shift_keycode is not None
        if shift:
            xtest.fake_input(self.d, X.KeyPress, self.shift_keycode)
        xtest.fake_input(self.d, X.KeyPress, keycode)
        xtest.fake_input(self.d, X.KeyRelease, keycode)
        if shift:
            xtest.fake_input(self.d, X.KeyRelease, self.shift_keycode)

    def close(self) -> None:
        try:
            self.d.close()
        except Exception:
            pass
