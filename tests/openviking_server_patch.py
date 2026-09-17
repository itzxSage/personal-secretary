"""Patch OpenViking 0.4.9's ``str_to_uint64`` for Python 3.13 (xxhash needs bytes).

Python 3.13 broke ``xxhash.xxh64(str)`` — it now requires bytes.  OpenViking
0.4.9 calls the function with plain strings, so the vector-store silently
fails to index.  This module monkey-patches both call-sites at import time.
"""

from __future__ import annotations

import xxhash
from openviking.storage.vectordb.collection import (  # pyright: ignore[reportMissingTypeStubs]
    local_collection,
)
from openviking.storage.vectordb.utils import (  # pyright: ignore[reportMissingTypeStubs]
    str_to_uint64 as _mod,
)


def _patched(input_string: str) -> int:
    return xxhash.xxh64(input_string.encode("utf-8")).intdigest()


_mod.str_to_uint64 = _patched  # type: ignore[attr-defined]
local_collection.str_to_uint64 = _patched  # type: ignore[attr-defined]
