"""-------------------------------------------------------------------------------
# Add-on: HAOS Kiosk Display (haoskiosk)
# File: cdp_client.py
# Version: 1.4.10
# Copyright Jeff Kosowsky
# Date: July 2026

Minimal Chrome DevTools Protocol (CDP) client helpers shared by 'rest_server.py'
(long-lived connection via ChromiumKiosk) and 'mouse_touch_inputs.py' (short-lived,
fire-and-forget calls for gesture-triggered actions).

Chromium (unlike Luakit) has no in-process scripting hook, so all kiosk behavior
(navigation, reload, JS injection) is driven externally over CDP, which Chromium
exposes as a local websocket once launched with '--remote-debugging-port'.
#-------------------------------------------------------------------------------"""
from __future__ import annotations
import asyncio
import itertools
import json
import logging
from typing import Any

from aiohttp import ClientSession, ClientTimeout  # type: ignore[import-not-found] # pylint: disable=import-error

logger = logging.getLogger(__name__)

DEFAULT_CDP_HOST = "127.0.0.1"
DEFAULT_CDP_PORT = 9222
_HTTP_TIMEOUT = ClientTimeout(total=5)


async def get_page_target(host: str = DEFAULT_CDP_HOST, port: int = DEFAULT_CDP_PORT) -> dict[str, Any] | None:
    """Return the CDP target dict for the (single) kiosk 'page' target, or None if unavailable."""
    url = f"http://{host}:{port}/json/list"
    async with ClientSession(timeout=_HTTP_TIMEOUT) as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                return None
            targets = await resp.json()
    for target in targets:
        if target.get("type") == "page":
            return target
    return None


async def get_browser_websocket_url(host: str = DEFAULT_CDP_HOST, port: int = DEFAULT_CDP_PORT) -> str | None:
    """
    Return the websocket URL for the browser-level CDP target (distinct from any page target).
    Some domains - notably SystemInfo, used for real GPU status - are only available on this
    target: connecting to a page target and calling them fails with "... is only supported on
    the browser target".
    """
    url = f"http://{host}:{port}/json/version"
    async with ClientSession(timeout=_HTTP_TIMEOUT) as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                return None
            info = await resp.json()
    return info.get("webSocketDebuggerUrl")


async def cdp_navigate(url: str, host: str = DEFAULT_CDP_HOST, port: int = DEFAULT_CDP_PORT, timeout: float = 5.0) -> bool:
    """
    Open a short-lived CDP connection to the kiosk's single page target and navigate it to 'url'.
    Returns True on success. Used for one-off navigations (REST launch_url, gesture launch_url)
    where holding a persistent connection isn't needed.
    """
    target = await get_page_target(host, port)
    if target is None:
        logger.error("[cdp_navigate] No page target found on %s:%s", host, port)
        return False

    ws_url = target.get("webSocketDebuggerUrl")
    if not ws_url:
        logger.error("[cdp_navigate] Page target has no webSocketDebuggerUrl")
        return False

    try:
        async with ClientSession() as session:
            async with session.ws_connect(ws_url, timeout=timeout) as ws:
                await ws.send_json({"id": 1, "method": "Page.navigate", "params": {"url": url}})
                async with asyncio.timeout(timeout):
                    async for msg in ws:
                        data = json.loads(msg.data)
                        if data.get("id") == 1:
                            if "error" in data:
                                logger.error("[cdp_navigate] Page.navigate error: %s", data["error"])
                                return False
                            return True
    except (asyncio.TimeoutError, ConnectionError, OSError) as e:
        logger.error("[cdp_navigate] Failed to navigate to %s: %s", url, e)
        return False
    return False


def cdp_navigate_sync(url: str, host: str = DEFAULT_CDP_HOST, port: int = DEFAULT_CDP_PORT, timeout: float = 5.0) -> bool:
    """Synchronous wrapper around 'cdp_navigate' for callers (e.g. mouse_touch_inputs.py) without an event loop."""
    try:
        return asyncio.run(cdp_navigate(url, host, port, timeout))
    except Exception as e:  # pylint: disable=broad-except
        logger.error("[cdp_navigate_sync] Failed to navigate to %s: %s", url, e)
        return False


class CDPConnection:
    """
    A persistent CDP websocket connection to a single page target, with request/response
    correlation and event dispatch. Used by ChromiumKiosk for the long-lived controller session.
    """

    def __init__(self, ws: Any) -> None:
        self._ws = ws
        self._id_counter = itertools.count(1)
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._event_handlers: dict[str, list[Any]] = {}
        self._reader_task: asyncio.Task[None] | None = None

    @classmethod
    async def connect(cls, host: str = DEFAULT_CDP_HOST, port: int = DEFAULT_CDP_PORT,
                       session: ClientSession | None = None) -> "CDPConnection":
        """Discover the page target and open a persistent websocket connection to it."""
        target = await get_page_target(host, port)
        if target is None or not target.get("webSocketDebuggerUrl"):
            raise ConnectionError(f"No CDP page target available on {host}:{port}")
        return await cls._connect_ws(target["webSocketDebuggerUrl"], session)

    @classmethod
    async def connect_browser(cls, host: str = DEFAULT_CDP_HOST, port: int = DEFAULT_CDP_PORT,
                               session: ClientSession | None = None) -> "CDPConnection":
        """Open a connection to the browser-level CDP target (needed for e.g. SystemInfo.getInfo,
        which isn't available on a page target)."""
        ws_url = await get_browser_websocket_url(host, port)
        if not ws_url:
            raise ConnectionError(f"No CDP browser target available on {host}:{port}")
        return await cls._connect_ws(ws_url, session)

    @classmethod
    async def _connect_ws(cls, ws_url: str, session: ClientSession | None = None) -> "CDPConnection":
        owns_session = session is None
        session = session or ClientSession()
        ws = await session.ws_connect(ws_url, timeout=10, max_msg_size=0)
        conn = cls(ws)
        conn._session = session  # type: ignore[attr-defined]
        conn._owns_session = owns_session  # type: ignore[attr-defined]
        conn._reader_task = asyncio.create_task(conn._read_loop())
        return conn

    async def _read_loop(self) -> None:
        try:
            async for msg in self._ws:
                if msg.type != 1:  # aiohttp.WSMsgType.TEXT == 1
                    continue
                data = json.loads(msg.data)
                if "id" in data:
                    fut = self._pending.pop(data["id"], None)
                    if fut and not fut.done():
                        fut.set_result(data)
                elif "method" in data:
                    for handler in self._event_handlers.get(data["method"], []):
                        try:
                            handler(data.get("params", {}))
                        except Exception:  # pylint: disable=broad-except
                            logger.exception("[CDPConnection] Event handler for %s raised", data["method"])
        except asyncio.CancelledError:
            raise
        except Exception as e:  # pylint: disable=broad-except
            logger.warning("[CDPConnection] Read loop terminated: %s", e)

    def on(self, method: str, handler: Any) -> None:
        """Register a callback for a CDP event (e.g. 'Page.frameNavigated')."""
        self._event_handlers.setdefault(method, []).append(handler)

    async def send(self, method: str, params: dict[str, Any] | None = None, timeout: float = 10.0) -> dict[str, Any]:
        """Send a CDP command and wait for its matching response."""
        msg_id = next(self._id_counter)
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_event_loop().create_future()
        self._pending[msg_id] = fut
        await self._ws.send_json({"id": msg_id, "method": method, "params": params or {}})
        try:
            async with asyncio.timeout(timeout):
                result = await fut
        finally:
            self._pending.pop(msg_id, None)
        if "error" in result:
            raise RuntimeError(f"CDP error for {method}: {result['error']}")
        return result.get("result", {})

    async def close(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
        await self._ws.close()
        if getattr(self, "_owns_session", False):
            await self._session.close()
