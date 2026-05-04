"""Security middleware — Host allowlist, CORS / CSRF for write endpoints.

Security §Authn / Authz:
  - Host allowlist (DNS-rebinding defence): reject Host headers that are not
    127.0.0.1 / localhost.
  - State-changing routes (POST/PUT/PATCH/DELETE) require allow-listed
    Origin AND custom X-Requested-By header.

Test-only admin routes (``/api/v1/admin/test-only/*``) are intentionally
exempt from CSRF — but **only when ``settings.env == 'development'``**.
The mount of those routes is itself env-gated (Security §Test-only admin
hooks) so the bypass is dead code in staging/production; we double-gate
here so that a future engineer who mounts a test-only route without
env-gating the mount cannot leak a CSRF bypass.

``EXTRA_ALLOWED_HOSTS`` extends the loopback allowlist for the local eval
harness; production AND staging are publicly reachable so honoring the env
var there would defeat the DNS-rebinding defence. The env-gate matches the
twin-override pattern from user-CLAUDE.md: dev-only allowlist, never the
inverse "anywhere except production."
"""
from __future__ import annotations

import os
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from api.settings import get_settings


_ALLOWED_HOSTS: frozenset[str] = frozenset(
    {
        "127.0.0.1",
        "localhost",
        "[::1]",
        "::1",
    }
)


def _extra_allowed_hosts() -> frozenset[str]:
    """Read ``EXTRA_ALLOWED_HOSTS`` (comma-separated) lazily.

    **Dev-only.** Outside ``env == 'development'`` the env var is silently
    ignored. Lets the eval harness (or a future hostname-aware reverse
    proxy) expand the allowlist without a code change. The loopback set
    above is always allowed; this only adds to it when we are in dev.
    """
    if get_settings().env != "development":
        return frozenset()
    raw = os.environ.get("EXTRA_ALLOWED_HOSTS", "")
    return frozenset(h.strip().lower() for h in raw.split(",") if h.strip())

_ALLOWED_ORIGINS: frozenset[str] = frozenset(
    {
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    }
)


def _extra_allowed_origins() -> frozenset[str]:
    """Read ``EXTRA_ALLOWED_ORIGINS`` (comma-separated) lazily.

    **Dev-only.** Outside ``env == 'development'`` the env var is silently
    ignored. Mirrors :func:`_extra_allowed_hosts` for the eval harness's
    ``host.docker.internal`` Origin (the harness runs in Docker; the API
    runs on the host).
    """
    if get_settings().env != "development":
        return frozenset()
    raw = os.environ.get("EXTRA_ALLOWED_ORIGINS", "")
    return frozenset(o.strip() for o in raw.split(",") if o.strip())

_WRITE_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _host_without_port(host_header: str) -> str:
    """Strip ``:port`` from the Host header. IPv6 brackets preserved."""
    if not host_header:
        return ""
    # IPv6 with brackets: keep brackets, strip optional :port after them.
    if host_header.startswith("["):
        close = host_header.find("]")
        if close == -1:
            return host_header
        return host_header[: close + 1]
    if ":" in host_header:
        return host_header.rsplit(":", 1)[0]
    return host_header


def _error(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"code": code, "message": message, "details": None},
    )


def _is_test_only_path(path: str) -> bool:
    return path.startswith("/api/v1/admin/test-only/")


class HostAllowlistMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        host_header = request.headers.get("host", "")
        host = _host_without_port(host_header).lower()
        if host not in _ALLOWED_HOSTS and host not in _extra_allowed_hosts():
            return _error(
                "invalid_host",
                f"Host header {host_header!r} is not in the loopback allow-list.",
                status=400,
            )
        return await call_next(request)


class CsrfMiddleware(BaseHTTPMiddleware):
    """Enforces same-origin custom-header pattern on state-changing routes.

    Reads are unconditional (Security §CSRF). Test-only admin routes bypass
    CSRF — but **only in development**. The bypass is double-gated (env +
    path) as defence-in-depth: even if a future engineer mounts a test-only
    route without env-gating the mount, the CSRF middleware still enforces
    the same-origin custom-header check in staging/production.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        method = request.method.upper()
        if method not in _WRITE_METHODS:
            return await call_next(request)
        if (
            get_settings().env == "development"
            and _is_test_only_path(request.url.path)
        ):
            return await call_next(request)
        origin = request.headers.get("origin")
        if origin is None:
            return _error(
                "origin_required",
                "Origin header missing or not in the allow-list. "
                "State-changing requests must come from the local UI.",
                status=403,
            )
        if origin not in _ALLOWED_ORIGINS and origin not in _extra_allowed_origins():
            return _error(
                "origin_required",
                "Origin header missing or not in the allow-list. "
                "State-changing requests must come from the local UI.",
                status=403,
            )
        if request.headers.get("x-requested-by") != "bible-study-ui":
            return _error(
                "x_requested_by_required",
                "Missing X-Requested-By: bible-study-ui header on a state-changing request.",
                status=403,
            )
        return await call_next(request)
