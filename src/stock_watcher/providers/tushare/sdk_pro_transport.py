from __future__ import annotations

import re
from dataclasses import replace

from .http_transport import BaseHttpTransport
from .models import TransportResult
from .transport_protocol import TransportRequest


class TushareSdkProTransport(BaseHttpTransport):
    """Safe implementation of the supplier's documented Tushare SDK route.

    The supplier documentation configures ``ts.pro_api()`` with
    ``_DataApi__http_url = https://fastapic.stockai888.top``. Tushare 1.4.29
    then POSTs to ``/<api_name>`` and includes ``ts_type_name`` in ``params``.

    Calling ``ts.set_token()`` as shown in the vendor quick-start writes
    ``~/tk.csv``. StockWatcher keeps the credential exclusively in the
    platform keychain, so this transport reproduces that SDK wire contract
    within the existing redacted, proxy-explicit HTTP session instead of using
    Tushare's global token persistence.
    """

    version = "tushare-sdk-pro-route-v1"
    _API_NAME = re.compile(r"^[a-z][a-z0-9_]*$")

    def execute(self, request: TransportRequest) -> TransportResult:
        api_name = request.api_name
        if not isinstance(api_name, str) or not self._API_NAME.fullmatch(api_name):
            raise ValueError("documented SDK transport requires a safe api_name")
        # The SDK derives this path from the API method. Ignore the legacy
        # root-path marker carried by callers so it cannot alter the route.
        return super().execute(
            replace(
                request,
                endpoint=f"/{api_name}",
                method="POST",
            )
        )

    def _request_parts(
        self,
        request: TransportRequest,
        secret: str,
    ) -> tuple[dict[str, str], dict[str, object] | None]:
        api_name = request.api_name
        if not isinstance(api_name, str):
            raise ValueError("documented SDK transport requires api_name")
        params = dict(request.params)
        params.setdefault("ts_type_name", str(self.profile.base_url).rstrip("/"))
        return (
            {
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "Content-Type": "application/json",
            },
            {
                "api_name": api_name,
                "token": secret,
                "params": params,
                "fields": ",".join(request.fields),
            },
        )
