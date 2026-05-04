"""Local dev launcher.

Reads ``Settings`` once at process start and hands ``settings.bind_host`` /
``settings.bind_port`` to ``uvicorn.run``. The settings validator already
constrains ``bind_host`` to {``127.0.0.1``, ``localhost``, ``::1``} unless
``BIND_HOST_ALLOW_NON_LOCAL=1`` is set; routing the launcher through this
module is what makes that constraint actually hold for ``make dev``.

Why a Python wrapper instead of inlining ``$(shell …)`` in the Makefile:
the Makefile shape is read by humans and copy-pasted; surfacing the
loopback guarantee as a single bash one-liner with a Python shell-out is
how the bind-host validator gets accidentally bypassed (run uvicorn
directly, ignore the validator). The wrapper makes the contract explicit.
"""
from __future__ import annotations

import sys

import uvicorn

from api.settings import get_settings


def main() -> int:
    settings = get_settings()
    uvicorn.run(
        "api.main:app",
        host=settings.bind_host,
        port=settings.bind_port,
        reload=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
