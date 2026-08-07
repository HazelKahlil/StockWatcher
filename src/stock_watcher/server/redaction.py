"""Global redaction filter for logs, errors and structured output.

No credential material may ever appear in logs, events, API/WS responses or
the delivery package. Patterns are applied at the last possible boundary so
providers and services can keep working with the real values in memory.
"""
from __future__ import annotations

import re
from typing import Any

_REDACTED = "[REDACTED]"

_HEADER_PATTERNS = (
    re.compile(r"(?i)(authorization|proxy-authorization|cookie|x-csrf-token)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"(?i)(sw_session|csrf_token)\s*=\s*[^\s;,]+"),
)
_SECRET_FIELD_PATTERNS = (
    re.compile(r"(?i)([\"']?(?:token|password|secret|api[_-]?key|master[_-]?key)[\"']?\s*[:=]\s*)([^,\s}\"']+)"),
    re.compile(r"(?i)\b(token|secret|password|master[_-]?key)\b\s*=\s*\S+"),
)
_URL_PATTERN = re.compile(r"(?i)(https?://[^\s/]+@)[^\s/]+")


class RedactionFilter:
    """Stateful redactor; call ``add_known_secret`` for live token values."""

    def __init__(self) -> None:
        self._known: list[re.Pattern[str]] = []
        self._known_plain: set[str] = set()

    def add_known_secret(self, value: str) -> None:
        if not value:
            return
        self._known_plain.add(value)
        self._known.append(re.compile(re.escape(value)))
        self._known.append(
            re.compile(re.escape(value.replace("+", "%2B").replace("/", "%2F")))
        )
        quoted = __import__("urllib.parse", fromlist=["quote"]).quote(value, safe="")
        self._known.append(re.compile(re.escape(quoted)))

    def redact(self, text: str) -> str:
        output = text
        for pattern in _HEADER_PATTERNS:
            output = pattern.sub(_REDACTED, output)
        for pattern in _SECRET_FIELD_PATTERNS:
            output = pattern.sub(lambda match: f"{match.group(1)}{_REDACTED}", output)
        output = _URL_PATTERN.sub(r"\1[REDACTED]", output)
        for pattern in self._known:
            output = pattern.sub(_REDACTED, output)
        return output

    def redact_value(self, value: Any) -> Any:
        """Recursively redact string leaves of JSON-able structures."""
        if isinstance(value, str):
            return self.redact(value)
        if isinstance(value, dict):
            return {key: self.redact_value(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.redact_value(item) for item in value]
        return value


_default_filter = RedactionFilter()


def redact(text: str) -> str:
    return _default_filter.redact(text)


def redact_value(value: Any) -> Any:
    return _default_filter.redact_value(value)


def register_known_secret(value: str) -> None:
    _default_filter.add_known_secret(value)
