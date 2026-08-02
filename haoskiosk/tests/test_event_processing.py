"""Tests for process_PRESS/process_RELEASE/process_MOTION - the per-input-event hot path.

These previously had zero test coverage (unlike the pure grammar/whitelist logic covered in
test_gesture_grammar.py) since they normally only run driven by real xinput/X11 events. Built
here instead using synthetic XInputEvent objects, which is all these functions actually need.

Also locks in that guarding debug()'s f-string calls behind an explicit `if DEBUG_LEVEL >= N:`
check (done for performance - avoids building and immediately discarding a formatted debug
string, including a ContactGroup registry lookup for TOUCH events, on every single MOTION event
at the default DEBUG_LEVEL=0) doesn't change the actual gesture-tracking outcome.
"""
import pytest

import mouse_touch_inputs as mti

XInputEvent = mti.XInputEvent
XEvent = mti.XEvent
ContactGroup = mti.ContactGroup
GestureSequence = mti.GestureSequence


@pytest.fixture(autouse=True)
def _reset_event_state():
    """ContactGroup/GestureSequence registries are shared module-level state - clean up
    whatever device_ids a test touches so tests can't leak state into each other."""
    yield
    for device_id in (98, 99, 100):
        ContactGroup.unregister_all(device_id)
        ContactGroup._last_group_added.pop(device_id, None)  # pylint: disable=protected-access
        ContactGroup._prev_group_added.pop(device_id, None)  # pylint: disable=protected-access
        GestureSequence.unregister(device_id)


@pytest.fixture(autouse=True)
def _restore_debug_level():
    saved = mti.DEBUG_LEVEL
    yield
    mti.DEBUG_LEVEL = saved


def _mouse_click(device_id, detail=1, pos=(10, 20)):
    press = XInputEvent(xevent=XEvent.RawButtonPress, device_id=device_id, time=1.0, detail=detail, position=pos)
    mti.process_PRESS(press)
    release = XInputEvent(xevent=XEvent.RawButtonRelease, device_id=device_id, time=1.1, detail=detail, position=pos)
    mti.process_RELEASE(release)


class TestProcessPressRelease:
    def test_press_creates_an_active_group(self):
        press = XInputEvent(xevent=XEvent.RawButtonPress, device_id=99, time=1.0, detail=1, position=(0, 0))
        mti.process_PRESS(press)
        group = ContactGroup.last_group_added(99)
        assert group is not None
        assert not group.is_complete
        assert group.current_pressed == [1]

    def test_release_completes_the_group_and_starts_a_sequence(self):
        _mouse_click(99)
        group = ContactGroup.last_group_added(99)
        assert group.is_complete
        seq = GestureSequence.get(99)
        assert seq is not None
        assert len(seq.groups) == 1

    def test_release_of_untracked_contact_is_ignored(self):
        press = XInputEvent(xevent=XEvent.RawButtonPress, device_id=99, time=1.0, detail=1, position=(0, 0))
        mti.process_PRESS(press)
        # Release a *different* button that was never pressed - must not raise or corrupt state
        stray_release = XInputEvent(xevent=XEvent.RawButtonRelease, device_id=99, time=1.05, detail=99, position=(0, 0))
        mti.process_RELEASE(stray_release)
        group = ContactGroup.last_group_added(99)
        assert not group.is_complete  # Original press is still open


class TestProcessMotion:
    def test_motion_updates_position_of_active_group(self):
        press = XInputEvent(xevent=XEvent.RawButtonPress, device_id=99, time=1.0, detail=1, position=(0, 0))
        mti.process_PRESS(press)
        motion = XInputEvent(xevent=XEvent.RawMotion, device_id=99, time=1.05, detail=1, position=(50, 60))
        mti.process_MOTION(motion)
        group = ContactGroup.last_group_added(99)
        assert group.num_events == 2  # PRESS + MOTION

    def test_motion_with_no_active_group_is_a_no_op(self):
        motion = XInputEvent(xevent=XEvent.RawMotion, device_id=99, time=1.0, detail=1, position=(5, 5))
        mti.process_MOTION(motion)  # Must not raise
        assert ContactGroup.last_group_added(99) is None


class TestDebugLevelDoesNotAffectOutcome:
    """Regression test for the debug()-guard performance change: DEBUG_LEVEL must only affect
    whether debug lines print, never the resulting gesture-tracking state."""

    @pytest.mark.parametrize("debug_level", [0, 2, 4, 5])
    def test_click_outcome_identical_across_debug_levels(self, debug_level, capsys):
        mti.DEBUG_LEVEL = debug_level
        device_id = 100
        _mouse_click(device_id)

        group = ContactGroup.last_group_added(device_id)
        seq = GestureSequence.get(device_id)
        assert group.is_complete
        assert seq is not None and len(seq.groups) == 1

        captured = capsys.readouterr()
        if debug_level == 0:
            assert captured.out == ""
        else:
            assert captured.out != ""  # Guards must not accidentally suppress debug output too
