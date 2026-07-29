from __future__ import annotations

from .http_transport import BaseHttpTransport
from .transport_protocol import TransportRequest


class SuperTransport(BaseHttpTransport):
    def _request_parts(
        self, request: TransportRequest, secret: str
    ) -> tuple[dict[str, str], dict[str, object] | None]:
        body: dict[str, object] | None = None
        if request.method.upper() != "GET":
            body = {"params": request.params, "fields": ",".join(request.fields)}
        return {"X-API-Key": secret, "Accept": "application/json"}, body
