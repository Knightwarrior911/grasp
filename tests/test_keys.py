"""Unit tests for the keystroke chord parser (grasp/keys.py).

No display or pyautogui backend needed — the parser is pure string → string
mapping.  All tests run headless.
"""

import pyautogui
import pytest

from grasp.keys import (
    _ALIAS_TABLE,
    _VALID_KEYS,
    UnknownKeyError,
    compact,
    parse_chord,
    parse_keys,
    resolve,
)


# ---------------------------------------------------------------------------
# compact()
# ---------------------------------------------------------------------------
class TestCompact:
    def test_already_compact(self):
        assert compact("ctrl+shift+a") == "ctrl+shift+a"

    def test_lowercased(self):
        assert compact("Ctrl+Shift+A") == "ctrl+shift+a"
        assert compact("CTRL+S") == "ctrl+s"

    def test_spaces_around_plus_removed(self):
        assert compact("ctrl + shift + a") == "ctrl+shift+a"
        assert compact("ctrl  +  s") == "ctrl+s"

    def test_leading_trailing_whitespace_stripped(self):
        assert compact("  ctrl+s  ") == "ctrl+s"

    def test_underscores_removed_within_parts(self):
        assert compact("ctrl_shift") == "ctrlshift"  # single key, not a chord

    def test_hyphens_removed_within_parts(self):
        assert compact("ctrl-shift") == "ctrlshift"  # single key, not a chord

    def test_plus_preserved_as_separator(self):
        assert compact("ctrl+s") == "ctrl+s"


# ---------------------------------------------------------------------------
# resolve() — basic keys
# ---------------------------------------------------------------------------
class TestResolveBasic:
    def test_single_letter(self):
        assert resolve("a") == "a"
        assert resolve("z") == "z"

    def test_single_digit(self):
        assert resolve("0") == "0"
        assert resolve("9") == "9"

    def test_space_character(self):
        assert resolve(" ") == " "

    def test_already_canonical_modifier(self):
        assert resolve("ctrl") == "ctrl"
        assert resolve("alt") == "alt"
        assert resolve("shift") == "shift"
        assert resolve("win") == "win"

    def test_already_canonical_special(self):
        assert resolve("enter") == "enter"
        assert resolve("tab") == "tab"
        assert resolve("backspace") == "backspace"
        assert resolve("left") == "left"
        assert resolve("f1") == "f1"
        assert resolve("f20") == "f20"
        assert resolve("f24") == "f24"

    def test_case_insensitive_canonical(self):
        assert resolve("CTRL") == "ctrl"
        assert resolve("Enter") == "enter"
        assert resolve("F1") == "f1"
        assert resolve("LEFT") == "left"


# ---------------------------------------------------------------------------
# resolve() — modifier aliases
# ---------------------------------------------------------------------------
class TestResolveModifierAliases:
    @pytest.mark.parametrize("alias,expected", [
        ("cmd", "win"),
        ("command", "win"),
        ("meta", "win"),
        ("super", "win"),
        ("ctrl", "ctrl"),
        ("control", "ctrl"),
        ("option", "alt"),
        ("opt", "alt"),
        ("alt", "alt"),
        ("shift", "shift"),
        ("fn", "fn"),
        ("function", "fn"),
    ])
    def test_modifier_alias(self, alias, expected):
        assert resolve(alias) == expected

    @pytest.mark.parametrize("alias,expected", [
        ("Cmd", "win"),
        ("COMMAND", "win"),
        ("Control", "ctrl"),
        ("Option", "alt"),
        ("Alt", "alt"),
        ("Shift", "shift"),
        ("Fn", "fn"),
        ("Function", "fn"),
    ])
    def test_modifier_alias_case_insensitive(self, alias, expected):
        assert resolve(alias) == expected


# ---------------------------------------------------------------------------
# resolve() — named key aliases
# ---------------------------------------------------------------------------
class TestResolveNamedKeyAliases:
    @pytest.mark.parametrize("alias,expected", [
        ("return", "enter"),
        ("enter", "enter"),
        ("escape", "esc"),
        ("esc", "esc"),
        ("space", "space"),
        ("spacebar", "space"),
        ("backspace", "backspace"),
        ("bksp", "backspace"),
        ("delete", "backspace"),       # common-speech "delete" = backspace
        ("forwarddelete", "delete"),   # actual forward-delete key
        ("del", "delete"),
        ("tab", "tab"),
        ("leftarrow", "left"),
        ("arrowleft", "left"),
        ("rightarrow", "right"),
        ("arrowright", "right"),
        ("uparrow", "up"),
        ("arrowup", "up"),
        ("downarrow", "down"),
        ("arrowdown", "down"),
        ("home", "home"),
        ("end", "end"),
        ("pageup", "pageup"),
        ("pgup", "pageup"),
        ("pagedown", "pagedown"),
        ("pgdn", "pagedown"),
        ("insert", "insert"),
        ("ins", "insert"),
        ("printscreen", "printscreen"),
        ("prtsc", "printscreen"),
        ("prtscr", "printscreen"),
        ("prntscrn", "printscreen"),
        ("capslock", "capslock"),
        ("caps", "capslock"),
        ("numlock", "numlock"),
        ("scrolllock", "scrolllock"),
        ("pause", "pause"),
        ("menu", "apps"),
        ("apps", "apps"),
    ])
    def test_named_key_alias(self, alias, expected):
        assert resolve(alias) == expected

    @pytest.mark.parametrize("alias,expected", [
        ("Return", "enter"),
        ("Escape", "esc"),
        ("ESC", "esc"),
        ("Space", "space"),
        ("SpaceBar", "space"),
        ("BackSpace", "backspace"),
        ("Delete", "backspace"),
        ("ForwardDelete", "delete"),
        ("DEL", "delete"),
        ("PgUp", "pageup"),
        ("PgDn", "pagedown"),
        ("Insert", "insert"),
        ("CapsLock", "capslock"),
    ])
    def test_named_key_alias_case_insensitive(self, alias, expected):
        assert resolve(alias) == expected


# ---------------------------------------------------------------------------
# resolve() — left/right modifier variants
# ---------------------------------------------------------------------------
class TestResolveLeftRightVariants:
    @pytest.mark.parametrize("alias,expected", [
        ("ctrlleft", "ctrlleft"),
        ("ctrlright", "ctrlright"),
        ("lctrl", "ctrlleft"),
        ("rctrl", "ctrlright"),
        ("shiftleft", "shiftleft"),
        ("shiftright", "shiftright"),
        ("lshift", "shiftleft"),
        ("rshift", "shiftright"),
        ("altleft", "altleft"),
        ("altright", "altright"),
        ("lalt", "altleft"),
        ("ralt", "altright"),
        ("winleft", "winleft"),
        ("winright", "winright"),
        ("lwin", "winleft"),
        ("rwin", "winright"),
    ])
    def test_left_right_variant(self, alias, expected):
        assert resolve(alias) == expected


# ---------------------------------------------------------------------------
# resolve() — function keys
# ---------------------------------------------------------------------------
class TestResolveFunctionKeys:
    @pytest.mark.parametrize("n", range(1, 21))
    def test_f1_through_f20(self, n):
        key = f"f{n}"
        assert resolve(key) == key

    @pytest.mark.parametrize("n", [21, 22, 23, 24])
    def test_f21_through_f24(self, n):
        key = f"f{n}"
        assert resolve(key) == key


# ---------------------------------------------------------------------------
# resolve() — error handling
# ---------------------------------------------------------------------------
class TestResolveErrors:
    def test_unknown_key_raises(self):
        with pytest.raises(UnknownKeyError):
            resolve("xyz")

    def test_unknown_key_has_suggestions(self):
        with pytest.raises(UnknownKeyError) as exc_info:
            resolve("ctrol")
        err = exc_info.value
        assert err.key == "ctrol"
        assert "ctrl" in err.suggestions

    def test_unknown_key_suggests_shift(self):
        with pytest.raises(UnknownKeyError) as exc_info:
            resolve("shft")
        assert "shift" in exc_info.value.suggestions

    def test_unknown_key_suggests_alt(self):
        with pytest.raises(UnknownKeyError) as exc_info:
            resolve("altl")
        assert "alt" in exc_info.value.suggestions

    def test_empty_string_raises(self):
        with pytest.raises(UnknownKeyError):
            resolve("")

    def test_unknown_key_message_format(self):
        with pytest.raises(UnknownKeyError, match="Unknown key 'ctrol'"):
            resolve("ctrol")

    def test_unknown_key_message_with_suggestion(self):
        with pytest.raises(UnknownKeyError, match="Did you mean"):
            resolve("ctrol")


# ---------------------------------------------------------------------------
# parse_chord()
# ---------------------------------------------------------------------------
class TestParseChord:
    def test_single_key(self):
        assert parse_chord("enter") == ["enter"]

    def test_simple_chord(self):
        assert parse_chord("ctrl+s") == ["ctrl", "s"]

    def test_three_modifiers(self):
        assert parse_chord("ctrl+shift+a") == ["ctrl", "shift", "a"]

    def test_case_insensitive(self):
        assert parse_chord("Ctrl+Shift+A") == ["ctrl", "shift", "a"]
        assert parse_chord("CTRL+S") == ["ctrl", "s"]

    def test_spaces_around_plus(self):
        assert parse_chord("ctrl + shift + a") == ["ctrl", "shift", "a"]

    def test_cmd_maps_to_win(self):
        assert parse_chord("cmd+s") == ["win", "s"]

    def test_command_maps_to_win(self):
        assert parse_chord("command+s") == ["win", "s"]

    def test_escape_maps_to_esc(self):
        assert parse_chord("escape") == ["esc"]
        assert parse_chord("esc") == ["esc"]

    def test_spacebar_maps_to_space(self):
        assert parse_chord("spacebar") == ["space"]

    def test_delete_maps_to_backspace(self):
        assert parse_chord("delete") == ["backspace"]

    def test_del_maps_to_delete(self):
        assert parse_chord("del") == ["delete"]

    def test_forwarddelete_maps_to_delete(self):
        assert parse_chord("forwarddelete") == ["delete"]

    def test_arrow_aliases(self):
        assert parse_chord("leftarrow") == ["left"]
        assert parse_chord("arrowleft") == ["left"]
        assert parse_chord("rightarrow") == ["right"]
        assert parse_chord("arrowright") == ["right"]
        assert parse_chord("uparrow") == ["up"]
        assert parse_chord("arrowup") == ["up"]
        assert parse_chord("downarrow") == ["down"]
        assert parse_chord("arrowdown") == ["down"]

    def test_pageup_aliases(self):
        assert parse_chord("pageup") == ["pageup"]
        assert parse_chord("pgup") == ["pageup"]

    def test_pagedown_aliases(self):
        assert parse_chord("pagedown") == ["pagedown"]
        assert parse_chord("pgdn") == ["pagedown"]

    def test_function_keys(self):
        assert parse_chord("f1") == ["f1"]
        assert parse_chord("f12") == ["f12"]
        assert parse_chord("f20") == ["f20"]

    def test_fn_modifier(self):
        assert parse_chord("fn+f1") == ["fn", "f1"]

    def test_option_maps_to_alt(self):
        assert parse_chord("option+s") == ["alt", "s"]
        assert parse_chord("opt+s") == ["alt", "s"]

    def test_control_maps_to_ctrl(self):
        assert parse_chord("control+s") == ["ctrl", "s"]

    def test_meta_maps_to_win(self):
        assert parse_chord("meta+s") == ["win", "s"]

    def test_super_maps_to_win(self):
        assert parse_chord("super+s") == ["win", "s"]

    def test_insert_aliases(self):
        assert parse_chord("insert") == ["insert"]
        assert parse_chord("ins") == ["insert"]

    def test_capslock_aliases(self):
        assert parse_chord("capslock") == ["capslock"]
        assert parse_chord("caps") == ["capslock"]

    def test_left_right_variants(self):
        assert parse_chord("lctrl+a") == ["ctrlleft", "a"]
        assert parse_chord("rshift+b") == ["shiftright", "b"]
        assert parse_chord("lalt+c") == ["altleft", "c"]
        assert parse_chord("rwin+d") == ["winright", "d"]

    def test_unknown_key_raises(self):
        with pytest.raises(UnknownKeyError):
            parse_chord("ctrol+s")

    def test_unknown_key_raises_for_single(self):
        with pytest.raises(UnknownKeyError):
            parse_chord("xyz")

    def test_empty_string_raises(self):
        with pytest.raises(UnknownKeyError):
            parse_chord("")

    def test_whitespace_only_raises(self):
        with pytest.raises(UnknownKeyError):
            parse_chord("   ")

    def test_non_string_raises(self):
        with pytest.raises(UnknownKeyError):
            parse_chord(123)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# parse_keys() — list input
# ---------------------------------------------------------------------------
class TestParseKeys:
    def test_string_input(self):
        assert parse_keys("ctrl+s") == ["ctrl", "s"]

    def test_list_input(self):
        assert parse_keys(["ctrl+a", "delete"]) == ["ctrl", "a", "backspace"]

    def test_list_with_aliases(self):
        assert parse_keys(["cmd+s", "esc"]) == ["win", "s", "esc"]

    def test_single_element_list(self):
        assert parse_keys(["ctrl+s"]) == ["ctrl", "s"]


# ---------------------------------------------------------------------------
# Roundtrip: every alias output is a valid pyautogui KEYBOARD_KEYS entry
# ---------------------------------------------------------------------------
class TestAliasRoundtrip:
    @pytest.mark.parametrize("alias,canonical", _ALIAS_TABLE.items())
    def test_alias_maps_to_valid_pyautogui_key(self, alias, canonical):
        assert canonical in pyautogui.KEYBOARD_KEYS, (
            f"Alias {alias!r} maps to {canonical!r} which is not in "
            f"pyautogui.KEYBOARD_KEYS"
        )


# ---------------------------------------------------------------------------
# UnknownKeyError attributes
# ---------------------------------------------------------------------------
class TestUnknownKeyError:
    def test_key_attribute(self):
        err = UnknownKeyError("ctrol", ["ctrl"])
        assert err.key == "ctrol"

    def test_suggestions_attribute(self):
        err = UnknownKeyError("ctrol", ["ctrl", "alt"])
        assert err.suggestions == ["ctrl", "alt"]

    def test_str_without_suggestions(self):
        err = UnknownKeyError("xyz")
        assert str(err) == "Unknown key 'xyz'."

    def test_str_with_suggestions(self):
        err = UnknownKeyError("ctrol", ["ctrl"])
        assert "Did you mean 'ctrl'" in str(err)

    def test_str_with_multiple_suggestions(self):
        err = UnknownKeyError("ctrol", ["ctrl", "alt"])
        assert "Did you mean" in str(err)
        assert "'ctrl'" in str(err)
        assert "'alt'" in str(err)

    def test_is_keyerror_subclass(self):
        assert issubclass(UnknownKeyError, KeyError)
