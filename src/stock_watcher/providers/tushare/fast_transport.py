from __future__ import annotations

from .http_transport import BaseHttpTransport
from .transport_protocol import TransportRequest


class FastTransport(BaseHttpTransport):
    def _request_parts(
        self, request: TransportRequest, secret: str
    ) -> tuple[dict[str, str], dict[str, object] | None]:
        if not request.api_name:
            raise ValueError("fast transport requires api_name")
        return (
            {
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "Content-Type": "application/json",
            },
            {
                "api_name": request.api_name,
                "token": secret,
                "params": request.params,
                "fields": ",".join(request.fields),
            },
        )
