"""Pytest configuration for the mouse_touch_inputs.py gesture-grammar test suite.

mouse_touch_inputs.py is written to run as a standalone script (real X server, real
xinput/dbus subprocesses at __main__ time), but the gesture-string grammar (GestureCommand,
RangeNumber) and command whitelist/blacklist enforcement it defines are pure and safe to
import and exercise directly - none of the hardware-facing behavior runs unless main() is
actually called (guarded by `if __name__ == "__main__":` at the bottom of the file). The one
module-level statement that touches anything external, DeviceSpec.init_screen_dim()'s Xlib
connection attempt, already catches its own exception and no-ops when no X server is
reachable, so it's safe in a headless CI/test environment too.

Requires: pytest, python-xlib, aiohttp (the last two are mouse_touch_inputs.py's own runtime
dependencies - see haoskiosk/Dockerfile's py3-xlib/py3-aiohttp packages).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# parse_args() runs at module import time and parses real sys.argv - pytest's own CLI args
# aren't valid options for this script's parser, so pin argv to just a program name before
# mouse_touch_inputs.py is ever imported (by this file or by test modules).
sys.argv = ["mouse_touch_inputs.py"]

import mouse_touch_inputs as mti  # noqa: E402  pylint: disable=wrong-import-position,import-error


@pytest.fixture(autouse=True)
def _reset_gesture_cmds_list():
    """GestureCommand.GESTURE_CMDS_LIST is a ClassVar shared across every GestureCommand
    instance/test - snapshot and restore it around every test so tests that load gesture
    command files/dicts can't leak state into unrelated tests."""
    saved = list(mti.GestureCommand.GESTURE_CMDS_LIST)
    yield
    mti.GestureCommand.GESTURE_CMDS_LIST = saved
