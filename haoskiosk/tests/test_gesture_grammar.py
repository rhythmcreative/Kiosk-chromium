"""Tests for the gesture-string grammar (RangeNumber, GestureCommand) and the command
whitelist/blacklist/redirection security model in mouse_touch_inputs.py.

This logic has a fairly involved, hand-written grammar (see the README's "Gesture String Keys"
section) and, separately, is the enforcement point for which shell commands a configured
gesture is allowed to run - both are pure and easy to get subtly wrong in ways a user report
("gesture X doesn't work anymore" / a security bypass) would be very hard to trace back to a
specific parsing bug. There was previously no test coverage for any of it in the repo.
"""
import pytest

import mouse_touch_inputs as mti

RangeNumber = mti.RangeNumber
GestureCommand = mti.GestureCommand
DeviceType = mti.DeviceType


# --------------------------------------------------------------------------- #
# RangeNumber
# --------------------------------------------------------------------------- #

class TestRangeNumberParsing:
    @pytest.mark.parametrize("raw,expected_number,expected_range", [
        ("5", 5, None),
        ("5+", 5, "+"),
        ("5-", 5, "-"),
        (" 5+ ", 5, "+"),  # Leading/trailing whitespace tolerated
        (0, 0, None),      # Plain int input
    ])
    def test_valid(self, raw, expected_number, expected_range):
        rn = RangeNumber(raw)
        assert rn.number == expected_number
        assert rn.range == expected_range

    @pytest.mark.parametrize("raw", ["", "abc", "5++", "5.0", None, 1.5, object()])
    def test_invalid_raises(self, raw):
        with pytest.raises((ValueError, TypeError)):
            RangeNumber(raw)

    def test_sign_prefix_is_not_range_notation(self):
        # Only a TRAILING '+'/'-' means range semantics (e.g. "5+"); a leading sign is just part
        # of a plain (signed) integer literal, same as Python's own int("-5")/int("+5").
        assert RangeNumber("-5").number == -5 and RangeNumber("-5").range is None
        assert RangeNumber("+5").number == 5 and RangeNumber("+5").range is None

    def test_is_range_number_classmethod(self):
        assert RangeNumber.is_range_number("3+")
        assert not RangeNumber.is_range_number("abc")

    def test_str_and_repr(self):
        assert str(RangeNumber("5+")) == "5+"
        assert str(RangeNumber("5")) == "5"
        assert repr(RangeNumber("5-")) == "RangeNumber('5-')"


class TestRangeNumberEquality:
    def test_exact_equals_int(self):
        assert RangeNumber("5") == 5
        assert RangeNumber("5") != 6

    def test_plus_range_equals_int(self):
        assert RangeNumber("5+") == 5
        assert RangeNumber("5+") == 100
        assert RangeNumber("5+") != 4

    def test_minus_range_equals_int(self):
        assert RangeNumber("5-") == 5
        assert RangeNumber("5-") == 0
        assert RangeNumber("5-") != 6

    def test_rangenumber_vs_rangenumber_requires_same_sign_and_value(self):
        assert RangeNumber("5+") == RangeNumber("5+")
        assert RangeNumber("5+") != RangeNumber("5-")
        assert RangeNumber("5+") != RangeNumber("5")
        assert RangeNumber("5") != RangeNumber("6")


class TestRangeNumberSubsetOrdering:
    """<=, <, >=, > between two RangeNumbers is subset/superset of the represented integer sets,
    not a comparison of the 'number' field - e.g. RangeNumber('2') <= RangeNumber('1+') because
    {2} is a subset of {1,2,3,...}."""

    def test_exact_is_subset_of_plus_range_covering_it(self):
        assert RangeNumber("2") <= RangeNumber("1+")
        assert RangeNumber("2") < RangeNumber("1+")
        assert RangeNumber("1+") >= RangeNumber("2")
        assert RangeNumber("1+") > RangeNumber("2")

    def test_exact_is_subset_of_minus_range_covering_it(self):
        assert RangeNumber("2") <= RangeNumber("3-")
        assert RangeNumber("3-") >= RangeNumber("2")

    def test_not_subset_when_ranges_disjoint(self):
        assert not (RangeNumber("5") <= RangeNumber("1-"))
        assert not (RangeNumber("1-") <= RangeNumber("5"))

    def test_equal_ranges_are_subset_but_not_proper_subset(self):
        a, b = RangeNumber("3+"), RangeNumber("3+")
        assert a <= b
        assert not (a < b)  # Equal, so not a *proper* subset

    def test_plus_range_not_subset_of_narrower_plus_range(self):
        # {1,2,3,...} is NOT a subset of {3,4,5,...}
        assert not (RangeNumber("1+") <= RangeNumber("3+"))

    def test_comparison_with_plain_int_uses_bounds(self):
        assert RangeNumber("5-") <= 5
        assert RangeNumber("5-") <= 10
        assert not (RangeNumber("5-") <= 4)
        assert RangeNumber("5+") >= 5
        assert not (RangeNumber("5+") >= 6)


# --------------------------------------------------------------------------- #
# GestureCommand._parse_gesture_key - README-documented examples
# --------------------------------------------------------------------------- #

VALID_GESTURE_KEYS = [
    "[Left, Right]_MOUSE_3_CLICKTAP",
    "[1,2,3]_MOUSE_1+_CORNER_TOPRIGHT",
    "2_Button_1_Long Click",     # "Mechanism" alias for MOUSE (contact_type, not device name)
    "3_Finger_2_Tap",            # "Mechanism" alias for TOUCH
    "2+_Finger_1_Swipe_down",
    "1_ANY_2-_CLICKTAP",
    "1+_ANY_1+_ANY",
]

INVALID_GESTURE_KEYS = [
    "1_Mouse_3-Tap",    # Malformed: missing a '_' separator before the gesture field
    "1-Touch_2-Long",   # Malformed: missing '_' separators entirely
    "0_ANY_1_ANY",      # Number of contacts must be positive
    "1_ANY_0_ANY",      # Number of clicks must be >= 1 (see _parse_gesture_key's own check)
    "1_XBOX_1_ANY",     # Unknown device type
    "1_ANY_1_TELEPORT", # Unknown gesture name
    "2_TOUCH_2_LONG",   # LONG is a single-click-only gesture (README: "always single-click gestures")
    "[Foo]_MOUSE_1_ANY",  # Not a valid button name or number
]


@pytest.mark.parametrize("key", VALID_GESTURE_KEYS)
def test_documented_valid_gesture_keys_parse(key):
    GestureCommand._parse_gesture_key(key)  # Must not raise


@pytest.mark.parametrize("key", INVALID_GESTURE_KEYS)
def test_invalid_gesture_keys_raise(key):
    with pytest.raises(ValueError):
        GestureCommand._parse_gesture_key(key)


@pytest.mark.xfail(strict=True, reason=(
    "README documents '2_TOUCH_1_DRAG_LEFT' as a valid example ('Both DRAG and SWIPE may "
    "include suffixes _LEFT/_RIGHT/_UP/_DOWN'), but TOUCH's DeviceSpec.gestures dict (unlike "
    "SWIPE) has no DRAG_LEFT/DRAG_RIGHT/DRAG_UP/DRAG_DOWN entries, and MOUSE's doesn't either - "
    "so no directional DRAG gesture string can currently parse for any device. Flagged here "
    "rather than silently skipped so this is caught if the underlying grammar changes; fixing "
    "it for real means adding those gesture-name entries to the relevant DeviceSpec(s) and "
    "verifying gesture classification agrees, which needs owner/hardware sign-off."
))
def test_documented_directional_drag_currently_unsupported():
    GestureCommand._parse_gesture_key("2_TOUCH_1_DRAG_LEFT")


class TestGestureKeyDetails:
    def test_case_insensitive(self):
        lower = GestureCommand._parse_gesture_key("1_any_1_any")
        upper = GestureCommand._parse_gesture_key("1_ANY_1_ANY")
        assert lower == upper

    def test_mechanism_alias_resolves_to_same_device_as_canonical_name(self):
        via_alias = GestureCommand._parse_gesture_key("2_Button_1_Long Click")
        via_name = GestureCommand._parse_gesture_key("2_MOUSE_1_Long Click")
        assert via_alias[0] is DeviceType.MOUSE
        assert via_alias[0] is via_name[0]

    def test_touch_finger_alias_resolves_to_touch(self):
        dev_type, *_ = GestureCommand._parse_gesture_key("3_Finger_2_Tap")
        assert dev_type is DeviceType.TOUCH

    def test_mouse_button_list_by_number_and_name_equivalent(self):
        by_number = GestureCommand._parse_gesture_key("[1,3]_MOUSE_1_CLICKTAP")
        by_name = GestureCommand._parse_gesture_key("[Left,Right]_MOUSE_1_CLICKTAP")
        assert by_number[2] == by_name[2]  # contacts_members set should match

    def test_corner_gesture_parses_for_touch_and_mouse(self):
        GestureCommand._parse_gesture_key("1_TOUCH_1_CORNER_TOPLEFT")
        GestureCommand._parse_gesture_key("1_MOUSE_1_CORNER_TOPLEFT")


# --------------------------------------------------------------------------- #
# GestureCommand._parse_command_value - command whitelist/blacklist/redirection security model
# --------------------------------------------------------------------------- #

class TestCommandValueWhitelist:
    """Exercises the same is_command_allowed() logic (and its SEPARATORS/SAFE_REDIRECT_REGEX
    helpers) that rest_server.py's REST API also uses - these tests double as regression tests
    for the command-whitelist-bypass-via-embedded-newline and unsafe-redirection fixes."""

    def test_whitelisted_string_command_allowed(self):
        result = GestureCommand._parse_command_value("echo hello")
        assert len(result["execs"]) == 1

    def test_non_whitelisted_command_blocked(self):
        with pytest.raises(ValueError):
            GestureCommand._parse_command_value("rm -rf /")

    def test_embedded_newline_cannot_smuggle_a_blocked_command(self):
        # Regression test: previously only the first line's program ("echo") was checked against
        # the whitelist, while the *entire* string (including the newline) was still handed to
        # the shell - where a literal newline behaves like ';' - letting an unvetted (here,
        # blacklisted) second command slip through unchecked.
        with pytest.raises(ValueError):
            GestureCommand._parse_command_value("echo hi\nrm -rf /media")

    def test_unsafe_redirection_blocked_even_for_whitelisted_program(self):
        with pytest.raises(ValueError):
            GestureCommand._parse_command_value("echo pwned > /etc/some_file")

    def test_safe_redirection_to_dev_null_allowed(self):
        result = GestureCommand._parse_command_value("echo ok > /dev/null")
        assert len(result["execs"]) == 1

    def test_fd_merge_redirection_allowed_and_not_misparsed_as_separate_program(self):
        # Regression test: SEP_REGEX previously split on a bare '&' even inside "2>&1", causing
        # the trailing '1' to be mis-tokenized as its own "program" and rejected as not found.
        result = GestureCommand._parse_command_value("date 2>&1")
        assert len(result["execs"]) == 1

    def test_list_form_command_allowed(self):
        # A bare ["echo", "hello"] means "run two separate commands" (each list element is its
        # own command) - an argv-style single command must be *nested*: [["echo", "hello"]].
        result = GestureCommand._parse_command_value([["echo", "hello"]])
        assert len(result["execs"]) == 1

    def test_list_of_commands_form_runs_each_element_as_its_own_command(self):
        result = GestureCommand._parse_command_value(["echo one", "echo two"])
        assert len(result["execs"]) == 2

    def test_internal_kiosk_command_string_recognized(self):
        result = GestureCommand._parse_command_value("kiosk.refresh_browser")
        assert len(result["execs"]) == 1

    def test_internal_kiosk_command_list_recognized(self):
        result = GestureCommand._parse_command_value([["kiosk.launch_url", "http://example.invalid"]])
        assert len(result["execs"]) == 1

    def test_empty_string_is_a_documented_no_op(self):
        result = GestureCommand._parse_command_value("")
        assert result["execs"] == []

    def test_command_dict_form_with_msg_and_timeout(self):
        result = GestureCommand._parse_command_value({"cmds": "echo hi", "msg": "hello", "timeout": 5})
        assert result["msg"] == "hello"
        assert result["timeout"] == 5
        assert len(result["execs"]) == 1


# --------------------------------------------------------------------------- #
# GestureCommand.parse_and_load_file - the hand-rolled comment-stripping /
# brace-balancing JSON-ish file preprocessor (README formats #1-#3)
# --------------------------------------------------------------------------- #

class TestParseAndLoadFile:
    def _load(self, tmp_path, text):
        GestureCommand.GESTURE_CMDS_LIST = []
        path = tmp_path / "gestures.json"
        path.write_text(text)
        GestureCommand.parse_and_load_file(str(path))
        return GestureCommand.GESTURE_CMDS_LIST

    def test_full_json_dict_with_braces(self, tmp_path):
        loaded = self._load(tmp_path, '{"1_ANY_1_ANY": "echo hi"}')
        assert len(loaded) == 1

    def test_missing_enclosing_braces_allowed(self, tmp_path):
        # README format #2: same as #1 but without the enclosing '{' '}'
        loaded = self._load(tmp_path, '"1_ANY_1_ANY": "echo hi"')
        assert len(loaded) == 1

    def test_trailing_comma_allowed(self, tmp_path):
        loaded = self._load(tmp_path, '"1_ANY_1_ANY": "echo hi",')
        assert len(loaded) == 1

    def test_hash_comment_stripped(self, tmp_path):
        loaded = self._load(tmp_path, '"1_ANY_1_ANY": "echo hi",  # this is a comment\n')
        assert len(loaded) == 1

    def test_escaped_hash_preserved_in_value(self, tmp_path):
        loaded = self._load(tmp_path, r'"1_ANY_1_ANY": "echo \#hashtag"')
        assert len(loaded) == 1
        assert loaded[0].cmds == "echo #hashtag"

    def test_gestures_wrapper_key_used_for_ha_options_json_style(self, tmp_path):
        # README format #3: top-level dict with a "gestures" key wrapping the actual entries -
        # used to parse HA add-on's own /data/options.json directly.
        loaded = self._load(tmp_path, '''
            {
                "ha_username": "someone",
                "gestures": {
                    "1_ANY_1_ANY": "echo hi"
                }
            }
        ''')
        assert len(loaded) == 1

    def test_duplicate_keys_keep_first_instance(self, tmp_path):
        loaded = self._load(tmp_path, '''
            {
                "1_ANY_1_ANY": "echo first",
                "1_ANY_1_ANY": "echo second"
            }
        ''')
        assert len(loaded) == 1
        assert loaded[0].cmds == "echo first"

    def test_missing_file_does_not_raise(self, tmp_path):
        GestureCommand.GESTURE_CMDS_LIST = []
        GestureCommand.parse_and_load_file(str(tmp_path / "does-not-exist.json"))
        assert GestureCommand.GESTURE_CMDS_LIST == []

    def test_malformed_json_does_not_raise(self, tmp_path):
        GestureCommand.GESTURE_CMDS_LIST = []
        loaded = self._load(tmp_path, '"1_ANY_1_ANY": "echo hi"  not valid json at all }}}')
        assert loaded == []
