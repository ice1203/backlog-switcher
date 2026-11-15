"""keys.py（APIキーの fingerprint）の単体テスト。"""

from __future__ import annotations

import hashlib

from bswitch import keys


def test_compute_fingerprint_hashes_head_and_tail() -> None:
    key = "abcde-middle-part-vwxyz"

    assert keys.compute_fingerprint(key) == hashlib.sha256(b"abcdevwxyz").hexdigest()
