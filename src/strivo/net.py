"""One HTTPS context for every outside call (TMDB, GitHub).

python.org builds of Python on macOS don't use the system certificate store, so plain urllib fails with
CERTIFICATE_VERIFY_FAILED there. Use certifi's bundle when it's installed (it's a dependency), else the default.
"""
from __future__ import annotations

import functools
import ssl


@functools.lru_cache(maxsize=1)
def ssl_context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:  # pragma: no cover
        return ssl.create_default_context()
