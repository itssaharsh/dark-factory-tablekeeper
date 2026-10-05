"""HTTP entry point: stdlib-only routing onto `app.handlers` (spec §2, §3)."""
from __future__ import annotations

import json
import os
import pathlib
import re
import sys
import urllib.parse
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import errors, handlers, validation
from .store import Store

WEB_DIR = pathlib.Path(__file__).resolve().parent.parent / "web"
STATIC_DIR = WEB_DIR / "static"
PAGE_FILES = {
    "/": "index.html",
    "/signup": "signup.html",
    "/login": "login.html",
    "/lookup": "lookup.html",
}
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".woff2": "font/woff2",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


@dataclass
class Context:
    store: Store
    headers: object
    query: dict
    raw_body: bytes
    params: dict = field(default_factory=dict)


# -- route handler adapters -------------------------------------------------

def _h_health(ctx: Context):
    return 200, {"status": "ok"}


def _h_reset(ctx: Context):
    body = validation.parse_json_object(ctx.raw_body)
    return handlers.test_reset(ctx.store, body)


def _h_export(ctx: Context):
    return handlers.test_export(ctx.store)


def _h_import(ctx: Context):
    body = validation.parse_json_object(ctx.raw_body)
    return handlers.test_import(ctx.store, body)


def _h_signup(ctx: Context):
    body = validation.parse_json_object(ctx.raw_body)
    return handlers.signup(ctx.store, body)


def _h_login(ctx: Context):
    body = validation.parse_json_object(ctx.raw_body)
    return handlers.login(ctx.store, body)


def _h_list_restaurants(ctx: Context):
    return handlers.list_restaurants(ctx.store)


def _h_get_restaurant(ctx: Context):
    return handlers.get_restaurant(ctx.store, ctx.params["id"])


def _h_availability(ctx: Context):
    return handlers.availability(ctx.store, ctx.query)


def _h_create_reservation(ctx: Context):
    body = validation.parse_json_object(ctx.raw_body)
    user = handlers.authenticate(ctx.store, ctx.headers)
    return handlers.create_reservation(ctx.store, user, ctx.headers, body)


def _h_list_reservations(ctx: Context):
    user = handlers.authenticate(ctx.store, ctx.headers)
    return handlers.list_reservations(ctx.store, user)


def _h_get_reservation(ctx: Context):
    user = handlers.authenticate(ctx.store, ctx.headers)
    return handlers.get_reservation(ctx.store, user, ctx.params["reference"])


def _h_cancel_reservation(ctx: Context):
    user = handlers.authenticate(ctx.store, ctx.headers)
    return handlers.cancel_reservation(ctx.store, user, ctx.params["reference"])


def _h_patch_reservation(ctx: Context):
    body = validation.parse_json_object(ctx.raw_body)
    user = handlers.authenticate(ctx.store, ctx.headers)
    return handlers.patch_reservation(ctx.store, user, ctx.params["reference"], body)


def _h_reservation_moves(ctx: Context):
    body = validation.parse_json_object(ctx.raw_body)
    user = handlers.authenticate(ctx.store, ctx.headers)
    return handlers.reservation_moves(ctx.store, user, ctx.headers, body)


def _h_get_history(ctx: Context):
    viewer = handlers.authenticate_optional(ctx.store, ctx.headers)
    return handlers.get_history(ctx.store, viewer, ctx.params["reference"])


def _h_get_decision(ctx: Context):
    viewer = handlers.authenticate_optional(ctx.store, ctx.headers)
    return handlers.get_decision(ctx.store, viewer, ctx.params["reference"])


def _h_list_policies(ctx: Context):
    return handlers.list_policies(ctx.store, ctx.params["id"])


def _h_publish_policy(ctx: Context):
    body = validation.parse_json_object(ctx.raw_body)
    user = handlers.authenticate(ctx.store, ctx.headers)
    return handlers.publish_policy(ctx.store, user, ctx.headers, ctx.params["id"], body)


def _h_create_series(ctx: Context):
    body = validation.parse_json_object(ctx.raw_body)
    user = handlers.authenticate(ctx.store, ctx.headers)
    return handlers.create_series(ctx.store, user, ctx.headers, body)


def _h_get_series(ctx: Context):
    viewer = handlers.authenticate_optional(ctx.store, ctx.headers)
    return handlers.get_series(ctx.store, viewer, ctx.params["series_id"])


ROUTES = [
    ("GET", re.compile(r"^/health$"), _h_health),
    ("POST", re.compile(r"^/_test/reset$"), _h_reset),
    ("GET", re.compile(r"^/_test/export$"), _h_export),
    ("POST", re.compile(r"^/_test/import$"), _h_import),
    ("POST", re.compile(r"^/auth/signup$"), _h_signup),
    ("POST", re.compile(r"^/auth/login$"), _h_login),
    ("GET", re.compile(r"^/restaurants$"), _h_list_restaurants),
    ("GET", re.compile(r"^/restaurants/(?P<id>[^/]+)/policies$"), _h_list_policies),
    ("POST", re.compile(r"^/restaurants/(?P<id>[^/]+)/policies$"), _h_publish_policy),
    ("GET", re.compile(r"^/restaurants/(?P<id>[^/]+)$"), _h_get_restaurant),
    ("GET", re.compile(r"^/availability$"), _h_availability),
    ("POST", re.compile(r"^/reservations$"), _h_create_reservation),
    ("GET", re.compile(r"^/reservations$"), _h_list_reservations),
    ("POST", re.compile(r"^/reservations/(?P<reference>[^/]+)/cancel$"), _h_cancel_reservation),
    ("GET", re.compile(r"^/reservations/(?P<reference>[^/]+)/history$"), _h_get_history),
    ("GET", re.compile(r"^/reservations/(?P<reference>[^/]+)/decision$"), _h_get_decision),
    ("GET", re.compile(r"^/reservations/(?P<reference>[^/]+)$"), _h_get_reservation),
    ("PATCH", re.compile(r"^/reservations/(?P<reference>[^/]+)$"), _h_patch_reservation),
    ("POST", re.compile(r"^/reservation-moves$"), _h_reservation_moves),
    ("POST", re.compile(r"^/series$"), _h_create_series),
    ("GET", re.compile(r"^/series/(?P<series_id>[^/]+)$"), _h_get_series),
]


class RequestHandler(BaseHTTPRequestHandler):
    server_version = "Tablekeeper/1.0"
    protocol_version = "HTTP/1.1"

    def _read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        return self.rfile.read(length) if length > 0 else b""

    def _serve_file(self, path: pathlib.Path, content_type: str) -> None:
        try:
            data = path.read_bytes()
        except OSError:
            self._respond(*self._not_found())
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _try_serve_web(self, method: str, url_path: str) -> bool:
        """Browser pages and their static assets, served directly (not as JSON)."""
        if method != "GET":
            return False
        if url_path in PAGE_FILES:
            self._serve_file(WEB_DIR / PAGE_FILES[url_path], "text/html; charset=utf-8")
            return True
        if url_path.startswith("/static/"):
            relative = url_path[len("/static/"):]
            target = (STATIC_DIR / relative).resolve()
            try:
                target.relative_to(STATIC_DIR.resolve())
            except ValueError:
                self._respond(*self._not_found())
                return True
            if not target.is_file():
                self._respond(*self._not_found())
                return True
            content_type = CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream")
            self._serve_file(target, content_type)
            return True
        return False

    def _dispatch(self, method: str) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        if self._try_serve_web(method, parsed.path):
            return
        store: Store = self.server.store  # type: ignore[attr-defined]
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        raw_body = self._read_body()
        ctx = Context(store=store, headers=self.headers, query=query, raw_body=raw_body)

        for route_method, pattern, fn in ROUTES:
            if route_method != method:
                continue
            match = pattern.match(parsed.path)
            if not match:
                continue
            ctx.params = match.groupdict()
            try:
                with store.lock:
                    status, body = fn(ctx)
            except errors.ApiError as exc:
                status, body = exc.status, exc.body()
            except Exception:  # pragma: no cover - last-resort safety net, never 5xx by design
                status = 500
                body = {"error": {"code": "internal_error", "message": "unexpected server error"}}
            self._respond(status, body)
            return
        self._respond(*self._not_found())

    @staticmethod
    def _not_found():
        exc = errors.not_found("no such route")
        return exc.status, exc.body()

    def _respond(self, status: int, body) -> None:
        if body is None:
            payload = b""
        else:
            payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PATCH(self) -> None:
        self._dispatch("PATCH")

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib signature
        pass


class TablekeeperServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128


def build_server(port: int) -> TablekeeperServer:
    server = TablekeeperServer(("0.0.0.0", port), RequestHandler)
    server.store = Store()  # type: ignore[attr-defined]
    return server


def main() -> None:
    port = int(os.environ.get("PORT", "8080"))
    server = build_server(port)
    print(f"tablekeeper listening on 0.0.0.0:{port}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
