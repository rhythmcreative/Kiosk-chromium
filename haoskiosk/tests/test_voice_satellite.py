"""Tests for the Voice Satellite auto-start feature (voice_satellite option):
- _build_args: --unsafely-treat-insecure-origin-as-secure only for http + enabled
- _grant_microphone_permission: CDP Browser.grantPermissions(audioCapture) for the HA origin
- _voice_satellite_autostart: taps the floating start button via trusted input events, stops
  when it disappears, caps attempts
"""
import asyncio
import json
from unittest.mock import AsyncMock, patch

import chromium_kiosk as ck


def _kiosk(**attrs):
    kiosk = ck.ChromiumKiosk.__new__(ck.ChromiumKiosk)  # Skip __init__ (env/X11-adjacent setup)
    kiosk.zoom_level = 100
    kiosk.browser_language = ""
    kiosk.onscreen_keyboard = False
    kiosk.voice_satellite = True
    kiosk.ha_url_base = "http://localhost:8123"
    kiosk._current_url = "http://localhost:8123/"
    for name, value in attrs.items():
        setattr(kiosk, name, value)
    return kiosk


def _fast_autostart_timing(monkeypatch):
    monkeypatch.setattr(ck, "VS_AUTOSTART_POLL_INTERVAL", 0.01)
    monkeypatch.setattr(ck, "VS_AUTOSTART_MAX_WAIT", 5.0)
    monkeypatch.setattr(ck, "VS_AUTOSTART_RECHECK_DELAY", 0.01)


# --------------------------------------------------------------------------- #
# _build_args - insecure-origin flag gating
# --------------------------------------------------------------------------- #

def test_insecure_origin_flag_added_for_http_when_enabled():
    args = _kiosk()._build_args("hardware")
    assert "--unsafely-treat-insecure-origin-as-secure=http://localhost:8123" in args


def test_no_insecure_origin_flag_for_https():
    kiosk = _kiosk(ha_url_base="https://ha.example.com")
    assert not any(a.startswith("--unsafely-treat-insecure-origin-as-secure") for a in kiosk._build_args("hardware"))


def test_no_insecure_origin_flag_when_disabled():
    kiosk = _kiosk(voice_satellite=False)
    assert not any(a.startswith("--unsafely-treat-insecure-origin-as-secure") for a in kiosk._build_args("hardware"))


# --------------------------------------------------------------------------- #
# _grant_microphone_permission - Browser.grantPermissions on the browser target
# --------------------------------------------------------------------------- #

def test_grants_audio_capture_for_ha_origin():
    kiosk = _kiosk()
    browser_conn = AsyncMock()
    with patch.object(ck.CDPConnection, "connect_browser", AsyncMock(return_value=browser_conn)):
        asyncio.run(kiosk._grant_microphone_permission())
    browser_conn.send.assert_awaited_once_with("Browser.grantPermissions", {
        "permissions": ["audioCapture"],
        "origin": "http://localhost:8123",
    })
    browser_conn.close.assert_awaited_once()


def test_grant_failure_is_swallowed():
    # Must never destabilize startup - a failed grant just means the fallback tap path runs.
    kiosk = _kiosk()
    browser_conn = AsyncMock()
    browser_conn.send.side_effect = RuntimeError("CDP unavailable")
    with patch.object(ck.CDPConnection, "connect_browser", AsyncMock(return_value=browser_conn)):
        asyncio.run(kiosk._grant_microphone_permission())  # Must not raise


# --------------------------------------------------------------------------- #
# _voice_satellite_autostart - the floating-start-button watcher/tapper
# --------------------------------------------------------------------------- #

_BUTTON_POS = json.dumps({"x": 640.0, "y": 700.0})


def _autostart_kiosk(eval_values):
    """A kiosk whose conn is an AsyncMock and whose Runtime.evaluate results are scripted:
    each _eval_js_value call consumes the next entry of eval_values ('sentinel' after end)."""
    kiosk = _kiosk()
    kiosk.conn = AsyncMock()
    kiosk._stopping = False
    kiosk._page_frozen = False
    kiosk._restart_lock = asyncio.Lock()

    async def fake_eval(_js):
        if not eval_values:
            return None  # Button never seen again (engine running / nothing to do)
        value = eval_values.pop(0)
        if value == "raise":
            raise RuntimeError("CDP connection dropped")
        return value

    kiosk._eval_js_value = fake_eval
    return kiosk


def test_taps_button_then_stops_when_it_disappears(monkeypatch):
    _fast_autostart_timing(monkeypatch)
    kiosk = _autostart_kiosk([_BUTTON_POS, None])
    asyncio.run(kiosk._voice_satellite_autostart())
    types = [c.args[1]["type"] for c in kiosk.conn.send.await_args_list]
    assert types == ["mousePressed", "mouseReleased"]  # One trusted click, then done
    coords = {k: v for k, v in kiosk.conn.send.await_args_list[0].args[1].items() if k in ("x", "y")}
    assert coords == {"x": 640.0, "y": 700.0}


def test_retries_up_to_max_taps_while_button_persists(monkeypatch):
    _fast_autostart_timing(monkeypatch)
    kiosk = _autostart_kiosk([_BUTTON_POS] * 10)
    asyncio.run(kiosk._voice_satellite_autostart())
    presses = [c for c in kiosk.conn.send.await_args_list if c.args[1].get("type") == "mousePressed"]
    assert len(presses) == ck.VS_AUTOSTART_MAX_TAPS  # Bounded, no infinite tapping


def test_does_nothing_when_button_never_shows(monkeypatch):
    _fast_autostart_timing(monkeypatch)
    kiosk = _autostart_kiosk([])
    asyncio.run(kiosk._voice_satellite_autostart())
    kiosk.conn.send.assert_not_awaited()


def test_stops_when_stopping_flag_set(monkeypatch):
    _fast_autostart_timing(monkeypatch)
    kiosk = _autostart_kiosk([_BUTTON_POS] * 10)
    kiosk._stopping = True
    asyncio.run(kiosk._voice_satellite_autostart())
    kiosk.conn.send.assert_not_awaited()


def test_click_cdp_error_keeps_polling(monkeypatch):
    _fast_autostart_timing(monkeypatch)
    kiosk = _autostart_kiosk([_BUTTON_POS, None])

    real_send = kiosk.conn.send

    async def failing_first_send(method, params=None, timeout=10.0):
        if method == "Input.dispatchMouseEvent" and params["type"] == "mousePressed":
            raise RuntimeError("transient")
        return await real_send(method, params, timeout)

    kiosk.conn.send = failing_first_send
    asyncio.run(kiosk._voice_satellite_autostart())  # Must not raise; retries until button gone
