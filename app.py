"""Helios — investment-model analytics & trade-signal platform.

Flask backend exposing a JSON API consumed by the single-page dashboard.
Run with:  python app.py   (or ./run.sh)

This module is the entry point and a thin compatibility facade: the web layer
lives in helios_web/ (one blueprint per section) and the domain logic in
engine/. Local .env settings load before the engine reads the environment.
"""
from __future__ import annotations

from helios_web.localenv import _load_local_env_file

_LOCAL_ENV_STATUS = _load_local_env_file()

import helios_web  # noqa: E402

app = helios_web.init_app()

from engine import ai_copilot  # noqa: E402,F401  (tests patch ai_copilot via this module)
from helios_web.core import lan_ips, print_banner  # noqa: E402,F401  (serve.py entrypoint helpers)

if __name__ == "__main__":
    # Dev entrypoint: localhost only. For network ("live") use ./serve.py.
    print_banner("127.0.0.1", 5000)
    app.run(host="127.0.0.1", port=5000, debug=False)
