"""state.json（付与状態）の読み書き。

パスはconfigファイルと同じディレクトリの `state.json`
（呼び出し側が `Config.state_path` を渡す想定）。

APIキーは絶対に記録しない。State/Grantモデル（models.py）にAPIキーを保持するフィールドは存在しない。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from bswitch.models import State


def load_state(path: Path) -> State:
    """state.jsonを読み込む。ファイル不在・空・壊れている場合は空のStateを返す。"""
    if not path.is_file():
        return State()
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return State()
    if not raw.strip():
        return State()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return State()
    if not isinstance(data, dict):
        return State()
    return State.from_dict(data)


def save_state(path: Path, state: State) -> None:
    """state.jsonへ書き込む。

    一時ファイルに書いてから `os.replace` でatomicに置き換える
    （書き込み中のクラッシュによる破損を防ぐ）。ファイルパーミッションは可能なら 0600 にする。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(payload + "\n", encoding="utf-8")
    try:
        os.chmod(tmp_path, 0o600)
    except OSError:
        pass
    os.replace(tmp_path, path)
