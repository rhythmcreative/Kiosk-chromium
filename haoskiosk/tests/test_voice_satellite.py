"""Tests for the Voice Satellite auto-start feature (voice_satellite option):
- _build_args: --unsafely-treat-insecure-origin-as-secure only for http + enabled
- _grant_microphone_permission: CDP Browser.grantPermissions(audioCapture) for the HA origin
- _voice_satellite_autostart: taps the floating start button via trusted input events, stops
  when it disappears, caps attempts
- voice_satellite_entity: env parsing/validation + _vs_entity_seed_js localStorage seeding
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
    kiosk.voice_satellite_entity = ""
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
# _seed_profile_preferences - race-free prompt suppression via profile seed
# --------------------------------------------------------------------------- #

def test_seeds_password_off_and_mic_allow(tmp_path, monkeypatch):
    monkeypatch.setattr(ck, "PROFILE_DIR", str(tmp_path))
    _kiosk()._seed_profile_preferences()
    data = json.loads((tmp_path / "Default" / "Preferences").read_text())
    assert data["credentials_enable_service"] is False
    assert data["credentials_enable_autosignin"] is False
    assert data["profile"]["password_manager_enabled"] is False
    mic = data["profile"]["content_settings"]["exceptions"]["media_stream_mic"]
    assert mic["http://localhost:8123,*"]["setting"] == 1  # CONTENT_SETTING_ALLOW


def test_seeds_no_mic_exception_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(ck, "PROFILE_DIR", str(tmp_path))
    _kiosk(voice_satellite=False)._seed_profile_preferences()
    data = json.loads((tmp_path / "Default" / "Preferences").read_text())
    assert data["profile"]["password_manager_enabled"] is False  # Password off always
    assert "content_settings" not in data["profile"]


# --------------------------------------------------------------------------- #
# _voice_satellite_autostart - the floating-start-button watcher/tapper
# --------------------------------------------------------------------------- #

_BUTTON_POS = json.dumps({"x": 640.0, "y": 700.0})


def _autostart_kiosk(eval_values, **attrs):
    """A kiosk whose conn is an AsyncMock and whose Runtime.evaluate results are scripted:
    each _eval_js_value call consumes the next entry of eval_values ('sentinel' after end)."""
    kiosk = _kiosk(**attrs)
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


def test_autostart_late_seeds_configured_entity(caplog):
    # First document loaded before injection registration: stored pick missing -> seed runs
    # post-load, then the normal button flow proceeds (tap once, button gone, done).
    import pytest

    kiosk = _autostart_kiosk(
        [None, True, _BUTTON_POS, None],  # stored=None -> seed -> button visible -> gone
        voice_satellite_entity="assist_satellite.sat_office",
    )
    with pytest.MonkeyPatch.context() as mp:
        _fast_autostart_timing(mp)
        with caplog.at_level("INFO"):
            asyncio.run(kiosk._voice_satellite_autostart())
    assert any("pre-selected" in r.message for r in caplog.records)


def test_autostart_warns_when_card_rejects_entity(caplog):
    # Entity seeded but resolveEntity() keeps clearing it (no such assist_satellite in HA):
    # button persists through every tap, final check sees the key cleared -> loud warning.
    import pytest

    seq = [None, True] + [_BUTTON_POS] * (2 * ck.VS_AUTOSTART_MAX_TAPS) + [None]
    kiosk = _autostart_kiosk(seq, voice_satellite_entity="assist_satellite.sat_office")
    with pytest.MonkeyPatch.context() as mp:
        _fast_autostart_timing(mp)
        with caplog.at_level("WARNING"):
            asyncio.run(kiosk._voice_satellite_autostart())
    assert any("cleared/rejected" in r.message for r in caplog.records)


# --------------------------------------------------------------------------- #
# voice_satellite_entity - pre-selecting the assist satellite in the panel
# --------------------------------------------------------------------------- #

def test_entity_id_validation():
    assert ck.ChromiumKiosk._is_assist_satellite_id("assist_satellite.sat_office")
    assert ck.ChromiumKiosk._is_assist_satellite_id("assist_satellite.01JABCDEFGH")
    # Malformed ids would be silently cleared by the card's own resolveEntity() - reject up front
    assert not ck.ChromiumKiosk._is_assist_satellite_id("")
    assert not ck.ChromiumKiosk._is_assist_satellite_id("not-an-entity")
    assert not ck.ChromiumKiosk._is_assist_satellite_id("media_player.office")
    assert not ck.ChromiumKiosk._is_assist_satellite_id("assist_satellite.foo.bar")


def test_entity_seed_js_sets_local_storage_key():
    js = _kiosk(voice_satellite_entity="assist_satellite.sat_office")._vs_entity_seed_js()
    assert "vs-satellite-entity" in js
    assert '"assist_satellite.sat_office"' in js  # JSON-quoted entity id embedded
    assert "getItem" in js and "setItem" in js  # Set-if-absent semantics


# --------------------------------------------------------------------------- #
# _resolve_voice_satellite_entity - friendly names / 'auto' -> concrete id
# --------------------------------------------------------------------------- #

_STATES = [
    {"entity_id": "assist_satellite.kitchen", "attributes": {"friendly_name": "Kitchen"}},
    {"entity_id": "assist_satellite.abc123", "attributes": {"friendly_name": "Home Assistant"}},
    {"entity_id": "media_player.office", "attributes": {"friendly_name": "Office"}},
    {"entity_id": "assist_satellite.zzz", "attributes": {}},
]


def test_pick_assist_entity_auto_first_sorted():
    assert ck.ChromiumKiosk._pick_assist_entity(_STATES, "auto") == "assist_satellite.abc123"


def test_pick_assist_entity_by_friendly_name_case_insensitive():
    pick = ck.ChromiumKiosk._pick_assist_entity(_STATES, "home assistant")
    assert pick == "assist_satellite.abc123"


def test_pick_assist_entity_by_entity_id():
    assert ck.ChromiumKiosk._pick_assist_entity(_STATES, "ASSIST_SATELLITE.KITCHEN") == "assist_satellite.kitchen"


def test_pick_assist_entity_none_on_missing_or_ambiguous():
    assert ck.ChromiumKiosk._pick_assist_entity(_STATES, "No existe") is None
    ambiguous = _STATES + [{"entity_id": "assist_satellite.dup", "attributes": {"friendly_name": "Kitchen"}}]
    assert ck.ChromiumKiosk._pick_assist_entity(ambiguous, "kitchen") is None
    assert ck.ChromiumKiosk._pick_assist_entity([], "auto") is None


def test_resolve_keeps_well_formed_id_without_api(monkeypatch):
    async def fail_fetch():  # Must never be called for a full entity id
        raise AssertionError("API fetch not needed for a well-formed id")

    kiosk = _kiosk(voice_satellite_entity="assist_satellite.sat_office")
    monkeypatch.setattr(kiosk, "_fetch_ha_states", fail_fetch)
    asyncio.run(kiosk._resolve_voice_satellite_entity())
    assert kiosk.voice_satellite_entity == "assist_satellite.sat_office"


def test_resolve_friendly_name_via_ha_states(monkeypatch):
    async def fake_fetch():
        return _STATES

    kiosk = _kiosk(voice_satellite_entity="Home Assistant")
    monkeypatch.setattr(kiosk, "_fetch_ha_states", fake_fetch)
    asyncio.run(kiosk._resolve_voice_satellite_entity())
    assert kiosk.voice_satellite_entity == "assist_satellite.abc123"


def test_resolve_unmatched_name_clears_to_manual(monkeypatch, caplog):
    async def fake_fetch():
        return _STATES

    kiosk = _kiosk(voice_satellite_entity="No existe")
    monkeypatch.setattr(kiosk, "_fetch_ha_states", fake_fetch)
    with caplog.at_level("WARNING"):
        asyncio.run(kiosk._resolve_voice_satellite_entity())
    assert kiosk.voice_satellite_entity == ""  # Falls back to manual picker
    assert any("no unique assist_satellite" in r.message for r in caplog.records)


def test_resolve_api_failure_clears_to_manual():
    kiosk = _kiosk(voice_satellite_entity="Home Assistant")

    async def none_fetch():
        return None

    kiosk._fetch_ha_states = none_fetch
    asyncio.run(kiosk._resolve_voice_satellite_entity())  # Must not raise
    assert kiosk.voice_satellite_entity == ""


def test_connect_cdp_registers_entity_seed_when_configured(monkeypatch):
    kiosk = _kiosk(dark_mode=True, voice_satellite_entity="assist_satellite.sat_office")
    kiosk.conn = AsyncMock()
    monkeypatch.setattr(ck.CDPConnection, "connect", AsyncMock(return_value=kiosk.conn))
    monkeypatch.setattr(ck.CDPConnection, "connect_browser", AsyncMock())
    monkeypatch.setattr(ck.ChromiumKiosk, "get_gpu_info", AsyncMock(return_value=None))

    asyncio.run(kiosk._connect_cdp())

    sources = [c.args[1]["source"] for c in kiosk.conn.send.await_args_list
               if c.args[0] == "Page.addScriptToEvaluateOnNewDocument"]
    assert any("vs-satellite-entity" in s for s in sources)


def test_connect_cdp_skips_entity_seed_when_unconfigured(monkeypatch):
    kiosk = _kiosk(dark_mode=True)
    kiosk.voice_satellite_entity = ""
    kiosk.conn = AsyncMock()
    monkeypatch.setattr(ck.CDPConnection, "connect", AsyncMock(return_value=kiosk.conn))
    monkeypatch.setattr(ck.CDPConnection, "connect_browser", AsyncMock())
    monkeypatch.setattr(ck.ChromiumKiosk, "get_gpu_info", AsyncMock(return_value=None))

    asyncio.run(kiosk._connect_cdp())

    sources = [c.args[1]["source"] for c in kiosk.conn.send.await_args_list
               if c.args[0] == "Page.addScriptToEvaluateOnNewDocument"]
    assert all("vs-satellite-entity" not in s for s in sources)
