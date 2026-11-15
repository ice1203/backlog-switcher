"""APIキーの fingerprint の計算。

`check` サブコマンドが、現在の `BACKLOG_API_KEY` と付与記録の fingerprint を照合するために使う。
bswitch は仮想ユーザーの API キーを取得・出力しない（仮想ユーザーは MCP サーバーの OAuth で認証する）。
"""

from __future__ import annotations

import hashlib


def compute_fingerprint(key: str) -> str:
    """APIキーの先頭・末尾5文字のSHA-256ハッシュを返す（64文字hex）。

    フルキーではなく部分文字列をハッシュ化することで、stateファイルが漏洩しても
    フルキーへの逆算ができない。ハッシュ化により、部分文字列自体も保護される。
    """
    partial = key[:5] + key[-5:]
    return hashlib.sha256(partial.encode()).hexdigest()
