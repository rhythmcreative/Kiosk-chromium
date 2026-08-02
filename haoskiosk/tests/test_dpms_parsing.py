"""Tests for parse_dpms_monitor_on() - the pure 'xset -q' output parser used by
ChromiumKiosk._dpms_watch_loop to detect screen on/off transitions and freeze/unfreeze the
kiosk page accordingly (see chromium_kiosk.py for the full feature) - plus the
freeze/unfreeze reaction itself (_handle_dpms_transition), against a fake CDP connection."""
import asyncio
from unittest.mock import AsyncMock

import chromium_kiosk as ck

REAL_ON_OUTPUT = """Keyboard Control:
  auto repeat:  on    key click percent:  0    LED mask:  00000000
  XKB indicators:
  DPMS (Display Power Management Signaling):
  Standby: 0    Suspend: 0    Off: 0
  DPMS is Enabled
  Monitor is On
"""

REAL_OFF_OUTPUT = """DPMS (Display Power Management Signaling):
  Standby: 600    Suspend: 600    Off: 600
  DPMS is Enabled
  Monitor is Off
"""


def test_parses_monitor_on():
    assert ck.parse_dpms_monitor_on(REAL_ON_OUTPUT) is True


def test_parses_monitor_off():
    assert ck.parse_dpms_monitor_on(REAL_OFF_OUTPUT) is False


def test_parses_monitor_standby_and_suspend_as_not_on():
    assert ck.parse_dpms_monitor_on("  Monitor is Standby\n") is False
    assert ck.parse_dpms_monitor_on("  Monitor is Suspend\n") is False


def test_missing_line_returns_none_not_off():
    # DPMS disabled, or unexpected xset output - callers must treat this as "unknown" and not
    # act on it, rather than treating it the same as a real "screen is off" transition.
    assert ck.parse_dpms_monitor_on("Keyboard Control:\n  auto repeat: on\n") is None
    assert ck.parse_dpms_monitor_on("") is None


def test_tolerates_leading_whitespace():
    assert ck.parse_dpms_monitor_on("      Monitor is On   \n") is True


# --------------------------------------------------------------------------- #
# _handle_dpms_transition - the freeze/unfreeze reaction, against a fake CDP connection
# (no asyncio test plugin in this project's test deps - asyncio.run() drives it directly)
# --------------------------------------------------------------------------- #

def _kiosk_with_fake_conn():
    kiosk = ck.ChromiumKiosk.__new__(ck.ChromiumKiosk)  # Skip __init__ (env/X11-adjacent setup)
    kiosk.conn = AsyncMock()
    kiosk._current_url = "http://localhost:8123/"
    kiosk._settings_applied = True
    kiosk._refresh_deadline = 0.0
    kiosk.browser_refresh = 0
    kiosk._page_frozen = False
    return kiosk


def test_screen_off_freezes_the_page():
    kiosk = _kiosk_with_fake_conn()
    asyncio.run(kiosk._handle_dpms_transition(False))
    kiosk.conn.send.assert_awaited_once_with("Page.setWebLifecycleState", {"state": "frozen"})
    assert kiosk._page_frozen is True


def test_screen_on_unfreezes_and_forces_a_reload():
    kiosk = _kiosk_with_fake_conn()
    kiosk._page_frozen = True
    asyncio.run(kiosk._handle_dpms_transition(True))
    # Two CDP calls expected: unfreeze, then Page.reload (via self.reload()) - in that order.
    calls = [c.args for c in kiosk.conn.send.await_args_list]
    assert calls[0] == ("Page.setWebLifecycleState", {"state": "active"})
    assert calls[1][0] == "Page.reload"
    assert kiosk._page_frozen is False


def test_cdp_failure_is_swallowed_and_clears_frozen_flag():
    kiosk = _kiosk_with_fake_conn()
    kiosk.conn.send.side_effect = RuntimeError("CDP connection dropped")
    kiosk._page_frozen = True
    asyncio.run(kiosk._handle_dpms_transition(False))  # Must not raise
    assert kiosk._page_frozen is False
