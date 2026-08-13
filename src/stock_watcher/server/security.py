"""Web trust-boundary helpers with fail-closed defaults."""
from __future__ import annotations

import json
from collections import deque
from ipaddress import ip_address, ip_network
from typing import Any
from urllib.parse import urlparse

from starlette.types import ASGIApp, Message, Receive, Scope, Send


def canonical_origin(value: str) -> tuple[str, str, int] | None:
    """Return a strict HTTP(S) origin tuple, rejecting paths and credentials."""
    try:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            return None
        if parsed.path not in {"", "/"}:
            return None
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return None
    return parsed.scheme, parsed.hostname.casefold(), port


def origin_matches(value: str, public_origin: str) -> bool:
    supplied = canonical_origin(value)
    expected = canonical_origin(public_origin)
    return supplied is not None and expected is not None and supplied == expected


def request_origin_matches(headers: Any, public_origin: str) -> bool:
    origin = str(headers.get("origin") or "").strip()
    if origin:
        return origin_matches(origin, public_origin)
    referer = str(headers.get("referer") or "").strip()
    if not referer:
        return False
    try:
        parsed = urlparse(referer)
        referer_origin = f"{parsed.scheme}://{parsed.netloc}"
    except ValueError:
        return False
    return origin_matches(referer_origin, public_origin)


def trusted_client_ip(
    headers: Any,
    peer_host: str | None,
    trusted_proxy_cidrs: tuple[str, ...],
) -> str | None:
    """Use a forwarded address only when the socket peer is a configured proxy."""
    if not peer_host:
        return None
    try:
        peer = ip_address(peer_host)
    except ValueError:
        return peer_host
    trusted = any(peer in ip_network(cidr, strict=False) for cidr in trusted_proxy_cidrs)
    if not trusted:
        return str(peer)
    forwarded = str(headers.get("x-forwarded-for") or "")
    if not forwarded:
        return str(peer)
    candidate = forwarded.split(",", 1)[0].strip()
    try:
        return str(ip_address(candidate))
    except ValueError:
        return str(peer)


class RequestBodyLimitMiddleware:
    """Reject declared or streamed HTTP request bodies above the application limit."""

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = {key.lower(): value for key, value in scope.get("headers", [])}
            raw_length = headers.get(b"content-length")
            try:
                length = int(raw_length) if raw_length is not None else 0
            except ValueError:
                length = self.max_bytes + 1
            if length < 0 or length > self.max_bytes:
                await self._reject(send)
                return
            buffered: deque[Message] = deque()
            received = 0
            more_body = True
            while more_body:
                message = await receive()
                if message.get("type") == "http.disconnect":
                    return
                if message.get("type") != "http.request":
                    continue
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    await self._reject(send)
                    return
                buffered.append(message)
                more_body = bool(message.get("more_body", False))

            async def limited_receive() -> Message:
                if buffered:
                    return buffered.popleft()
                return await receive()

            await self.app(scope, limited_receive, send)
            return
        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(send: Send) -> None:
        payload = json.dumps(
            {"error": {"code": "request_too_large", "message": "请求内容过大"}},
            ensure_ascii=False,
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(payload)).encode("ascii")),
                    (b"cache-control", b"private, no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": payload})
