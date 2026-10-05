"""A tiny stdlib-only HTTP client for exercising the running service in tests."""
from __future__ import annotations

import http.client
import json
import urllib.parse


class Response:
    def __init__(self, status: int, body):
        self.status = status
        self.body = body

    def __repr__(self) -> str:
        return f"Response({self.status}, {self.body!r})"

    @property
    def error_code(self) -> str | None:
        if isinstance(self.body, dict):
            return self.body.get("error", {}).get("code")
        return None


class Client:
    def __init__(self, port: int, token: str | None = None):
        self.port = port
        self.token = token

    def with_token(self, token: str) -> "Client":
        return Client(self.port, token)

    def _headers(self, extra: dict | None, has_body: bool) -> dict:
        headers = {}
        if has_body:
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if extra:
            headers.update(extra)
        return headers

    def request(self, method: str, path: str, *, json_body=None, headers=None,
                params=None, idempotency_key=None) -> Response:
        if params:
            path = f"{path}?{urllib.parse.urlencode(params, doseq=True)}"
        extra = dict(headers or {})
        if idempotency_key is not None:
            extra["Idempotency-Key"] = idempotency_key
        data = None
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
        elif method in ("POST", "PATCH", "PUT") and "raw_body" not in (headers or {}):
            data = None
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request(method, path, body=data, headers=self._headers(extra, data is not None))
            resp = conn.getresponse()
            raw = resp.read()
        finally:
            conn.close()
        parsed = None
        if raw:
            parsed = json.loads(raw.decode("utf-8"))
        return Response(resp.status, parsed)

    def get(self, path: str, **kw) -> Response:
        return self.request("GET", path, **kw)

    def post(self, path: str, **kw) -> Response:
        return self.request("POST", path, **kw)

    def patch(self, path: str, **kw) -> Response:
        return self.request("PATCH", path, **kw)

    def raw(self, method: str, path: str, body_bytes: bytes, headers: dict | None = None) -> Response:
        """Send a request with a literal body, bypassing JSON encoding (for malformed-body tests)."""
        extra = dict(headers or {})
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            all_headers = {"Content-Type": "application/json"}
            if self.token:
                all_headers["Authorization"] = f"Bearer {self.token}"
            all_headers.update(extra)
            conn.request(method, path, body=body_bytes, headers=all_headers)
            resp = conn.getresponse()
            raw = resp.read()
        finally:
            conn.close()
        parsed = None
        if raw:
            parsed = json.loads(raw.decode("utf-8"))
        return Response(resp.status, parsed)
