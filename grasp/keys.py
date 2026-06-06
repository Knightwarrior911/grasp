"""Keystroke chord parser for Grasp.

Parses human-readable key chord strings like ``ctrl+shift+a`` into canonical
pyautogui key names.  Handles modifier aliases (cmd, opt, fn, …), named-key
aliases (escape→esc, spacebar→space, …), case insensitivity, and ignores
arbitrary whitespace / ``_`` / ``-`` within key parts.

Pure and dependency-light: only needs ``pyautogui.KEYBOARD_KEYS`` (already a
project dep) and ``difflib`` (stdlib).  No display required — safe to import in
unit tests.
"""

from __future__ import annotations

import difflib

import pyautogui

# ---------------------------------------------------------------------------
# Valid key set (canonical pyautogui names + single-char keys)
# ---------------------------------------------------------------------------
_VALID_KEYS: frozenset[str] = frozenset(pyautogui.KEYBOARD_KEYS)

# ---------------------------------------------------------------------------
# Alias table  →  canonical pyautogui key name
# ---------------------------------------------------------------------------
_ALIAS_TABLE: dict[str, str] = {
    # --- modifiers ---
    "cmd": "win",
    "command": "win",
    "meta": "win",
    "super": "win",
    "ctrl": "ctrl",
    "control": "ctrl",
    "option": "alt",
    "opt": "alt",
    "alt": "alt",
    "shift": "shift",
    "fn": "fn",
    "function": "fn",
    # --- enter / return ---
    "return": "enter",
    "enter": "enter",
    # --- escape ---
    "escape": "esc",
    "esc": "esc",
    # --- space ---
    "space": "space",
    "spacebar": "space",
    # --- backspace / delete ---
    "backspace": "backspace",
    "bksp": "backspace",
    "delete": "backspace",      # common-speech "delete" = backspace
    "forwarddelete": "delete",  # the actual forward-delete key
    "del": "delete",
    # --- tab ---
    "tab": "tab",
    # --- arrow keys ---
    "leftarrow": "left",
    "arrowleft": "left",
    "rightarrow": "right",
    "arrowright": "right",
    "uparrow": "up",
    "arrowup": "up",
    "downarrow": "down",
    "arrowdown": "down",
    # --- navigation ---
    "home": "home",
    "end": "end",
    "pageup": "pageup",
    "pgup": "pageup",
    "pagedown": "pagedown",
    "pgdn": "pagedown",
    # --- insert ---
    "insert": "insert",
    "ins": "insert",
    # --- print screen aliases ---
    "printscreen": "printscreen",
    "prtsc": "printscreen",
    "prtscr": "printscreen",
    "prntscrn": "printscreen",
    # --- lock keys ---
    "capslock": "capslock",
    "caps": "capslock",
    "numlock": "numlock",
    "scrolllock": "scrolllock",
    # --- misc named keys ---
    "pause": "pause",
    "menu": "apps",
    "apps": "apps",
    # --- left / right modifier variants ---
    "ctrlleft": "ctrlleft",
    "ctrlright": "ctrlright",
    "lctrl": "ctrlleft",
    "rctrl": "ctrlright",
    "shiftleft": "shiftleft",
    "shiftright": "shiftright",
    "lshift": "shiftleft",
    "rshift": "shiftright",
    "altleft": "altleft",
    "altright": "altright",
    "lalt": "altleft",
    "ralt": "altright",
    "winleft": "winleft",
    "winright": "winright",
    "lwin": "winleft",
    "rwin": "winright",
}

# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------
class UnknownKeyError(KeyError):
    """Raised when a key name cannot be resolved to a valid pyautogui key.

    Attributes:
        key: the unrecognized key string (before canonicalisation).
        suggestions: close matches from the alias / valid-key vocabulary.
    """

    def __init__(self, key: str, suggestions: list[str] | None = None) -> None:
        self.key: str = key
        self.suggestions: list[str] = suggestions or []
        msg = f"Unknown key {key!r}."
        if self.suggestions:
            msg += f" Did you mean {', '.join(repr(s) for s in self.suggestions)}?"
        super().__init__(msg)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def compact(raw: str) -> str:
    """Normalize a chord string: lowercase, strip, remove spaces/underscores/hyphens
    within each key part (``+`` is the chord separator and is preserved).

    >>> compact("Ctrl + Shift + A")
    'ctrl+shift+a'
    >>> compact("cmd-space")
    'cmdspace'
    """
    parts = raw.lower().strip().split("+")
    return "+".join(p.replace(" ", "").replace("_", "").replace("-", "") for p in parts)


def _build_vocabulary() -> list[str]:
    """Return every string that ``resolve()`` can recognise — alias keys + valid
    pyautogui keys — for use with ``difflib.get_close_matches``."""
    return sorted(set(_ALIAS_TABLE.keys()) | _VALID_KEYS)


def resolve(key: str) -> str:
    """Resolve *key* to a canonical pyautogui key name.

    Lookup order:
    1. Alias table (case-insensitive).
    2. Single-character keys that exist in ``pyautogui.KEYBOARD_KEYS``.
    3. Already-canonical key (case-insensitive match in ``_VALID_KEYS``).

    Raises :class:`UnknownKeyError` with suggestions if the key is not recognised.
    """
    low = key.strip().lower()

    # 1. Alias table
    if low in _ALIAS_TABLE:
        return _ALIAS_TABLE[low]

    # 2. Single-char keys — pass through as-is
    if len(low) == 1 and low in _VALID_KEYS:
        return low

    # 3. Already canonical (or close enough)
    if low in _VALID_KEYS:
        return low

    # 4. Not found — suggest closest matches
    vocab = _build_vocabulary()
    suggestions = difflib.get_close_matches(low, vocab, n=3, cutoff=0.5)
    raise UnknownKeyError(key, suggestions)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def parse_chord(chord: str) -> list[str]:
    """Parse a single chord string into a list of canonical pyautogui key names.

    Args:
        chord: A key combination string like ``"ctrl+s"``, ``"Cmd+Shift+A"``,
               ``"escape"``, or ``"PgUp"``.  Whitespace around ``+`` is ignored.

    Returns:
        One canonical key name per part, e.g. ``["ctrl", "s"]``.

    Raises:
        UnknownKeyError: If any part does not resolve to a valid key name.

    Examples::

        >>> parse_chord("ctrl+s")
        ['ctrl', 's']
        >>> parse_chord("Ctrl+Shift+A")
        ['ctrl', 'shift', 'a']
        >>> parse_chord("enter")
        ['enter']
        >>> parse_chord("cmd+space")
        ['win', 'space']
        >>> parse_chord("ctrl + shift + a")
        ['ctrl', 'shift', 'a']
        >>> parse_chord("PgUp")
        ['pageup']
        >>> parse_chord("f1")
        ['f1']
    """
    if not isinstance(chord, str) or not chord.strip():
        raise UnknownKeyError(chord if isinstance(chord, str) else str(chord))

    compacted = compact(chord)
    parts = compacted.split("+")
    return [resolve(p) for p in parts if p]  # skip empty parts from "++" etc.


def parse_keys(chord: str | list[str]) -> list[str]:
    """Like :func:`parse_chord` but also accepts a *list* of chord strings,
    merging the results into one flat list.

    >>> parse_keys("ctrl+s")
    ['ctrl', 's']
    >>> parse_keys(["ctrl+a", "delete"])
    ['ctrl', 'a', 'backspace']
    """
    if isinstance(chord, str):
        return parse_chord(chord)
    result: list[str] = []
    for c in chord:
        result.extend(parse_chord(c))
    return result
