"""仮想ユーザーAPIキーの解決。

解決順序:
  1. 環境変数 `BACKLOG_WRITER_API_KEY` / `BACKLOG_READER_API_KEY` が設定済みならそれを使う
     （テスト・非1Password環境用フォールバック）
  2. 未設定なら config の `writer_api_key_ref` / `reader_api_key_ref`（`op://...`）を
     `subprocess.run(["op", "read", ref])` で取得する
  3. `op` コマンド呼び出しに失敗した場合は分かりやすいエラーメッセージで終了する
     （APIキーの値はメッセージに一切含めない）

注意:
  - bswitchは `BACKLOG_API_KEY`（出力専用）を一切読まない
  - マスターユーザーのキーも同じ順序で `BSWITCH_MASTER_API_KEY` > config の `master_api_key_ref` から解決する
    （AIエージェントを起動するシェルにマスターキーを常駐させないため、`master_api_key_ref` を推奨）
"""

from __future__ import annotations

import hashlib
import os
import subprocess

from bswitch.models import DefaultConfig

#: writer仮想ユーザーAPIキーの環境変数フォールバック名
ENV_WRITER_API_KEY = "BACKLOG_WRITER_API_KEY"
#: reader仮想ユーザーAPIキーの環境変数フォールバック名
ENV_READER_API_KEY = "BACKLOG_READER_API_KEY"
#: マスターユーザーAPIキーの環境変数名（docs/design.md「環境変数設計」）
ENV_MASTER_API_KEY = "BSWITCH_MASTER_API_KEY"

#: op不在時の案内文（role別）。マスターキーはシェルへの常駐を禁止しているため、
#: 環境変数をexportする案内はせず、1回の実行にだけ渡す方法を示す。
_OP_NOT_FOUND_HINT = {
    "writer": f"1Password CLIをインストールするか、環境変数でキーを直接指定してください（{ENV_WRITER_API_KEY}）。",
    "reader": f"1Password CLIをインストールするか、環境変数でキーを直接指定してください（{ENV_READER_API_KEY}）。",
    "master": (
        "1Password CLIをインストールしてください。使えない場合は、シェル関数（wrapper）の中で "
        f'`{ENV_MASTER_API_KEY}="$key" command bswitch "$@"` の出力をevalする形で1回の実行にだけキーを渡してください'
        "（READMEの「マスターAPIキーの設定」参照。exportによるシェルへの常駐は不可）。"
    ),
}


def compute_fingerprint(key: str) -> str:
    """APIキーの先頭・末尾5文字のSHA-256ハッシュを返す（64文字hex）。

    フルキーではなく部分文字列をハッシュ化することで、stateファイルが漏洩しても
    フルキーへの逆算ができない。ハッシュ化により、部分文字列自体も保護される。
    """
    partial = key[:5] + key[-5:]
    return hashlib.sha256(partial.encode()).hexdigest()


class KeyResolutionError(Exception):
    """APIキー解決に失敗した場合の例外。APIキーの値は含めない。"""


def _read_from_1password(ref: str, *, role: str) -> str:
    """`op read <ref>` でAPIキーを取得する。

    失敗時は KeyResolutionError を送出する（メッセージにキーの値・refの中身は含めるが、
    ref自体は秘密情報ではないため問題ない。取得結果のキー値は絶対に含めない）。
    """
    try:
        result = subprocess.run(
            ["op", "read", ref],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise KeyResolutionError(
            f"{role}用APIキーの取得に失敗しました: 1Password CLI（op）が見つかりません。{_OP_NOT_FOUND_HINT[role]}"
        ) from exc
    if result.returncode != 0:
        raise KeyResolutionError(
            f"{role}用APIキーの取得に失敗しました: `op read {ref}` がエラー終了しました"
            f"（終了コード {result.returncode}）。1Password CLIのログイン状態と参照先を確認してください。"
        )
    key = result.stdout.strip()
    if not key:
        raise KeyResolutionError(f"{role}用APIキーの取得に失敗しました: `op read {ref}` の結果が空でした。")
    return key


def resolve_writer_key(config: DefaultConfig) -> str:
    """writer仮想ユーザーのAPIキーを解決する。"""
    env_value = os.environ.get(ENV_WRITER_API_KEY)
    if env_value:
        return env_value
    if config.writer_api_key_ref is None:
        raise KeyResolutionError(
            "writer用APIキーが設定されていません。"
            f"configの writer_api_key_ref または環境変数 {ENV_WRITER_API_KEY} を設定してください。"
        )
    return _read_from_1password(config.writer_api_key_ref, role="writer")


def resolve_reader_key(config: DefaultConfig) -> str:
    """reader仮想ユーザーのAPIキーを解決する。"""
    env_value = os.environ.get(ENV_READER_API_KEY)
    if env_value:
        return env_value
    if config.reader_api_key_ref is None:
        raise KeyResolutionError(
            "reader用APIキーが設定されていません。"
            f"configの reader_api_key_ref または環境変数 {ENV_READER_API_KEY} を設定してください。"
        )
    return _read_from_1password(config.reader_api_key_ref, role="reader")


def resolve_master_key(config: DefaultConfig) -> str:
    """マスターユーザーのAPIキーを解決する（コマンド実行時のみ取得し、シェルへは書き出さない）。"""
    env_value = os.environ.get(ENV_MASTER_API_KEY)
    if env_value:
        return env_value
    if config.master_api_key_ref is None:
        raise KeyResolutionError(
            "マスターAPIキーが設定されていません。"
            "configの master_api_key_ref を設定してください"
            f"（{ENV_MASTER_API_KEY} を使う場合は1回の実行にだけ渡し、exportによるシェルへの常駐は不可）。"
        )
    return _read_from_1password(config.master_api_key_ref, role="master")


def resolve_api_key(permission: str, config: DefaultConfig) -> str:
    """permissionに応じたAPIキーを解決する。

    read → reader、write/admin → writer。
    """
    if permission == "read":
        return resolve_reader_key(config)
    if permission in ("write", "admin"):
        return resolve_writer_key(config)
    raise ValueError(f"未知のpermissionです: {permission}")
