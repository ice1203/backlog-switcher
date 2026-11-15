"""`bswitch-open-browser`: 認可画面を、付与中の仮想ユーザーの Chrome プロファイルで開く。

Claude Code の `settings.json` の `env` で `BROWSER=bswitch-open-browser` と設定して使う。
Claude Code は `/mcp` の認証で開く URL を、このコマンドの引数として渡す。

振り分け:
  - ホストが config の `space` と同じ URL と、スキーム・ホスト・ポートが `mcp_url` と同じ URL は、
    期限内の付与の権限 (read → reader, write / admin → writer) の Chrome プロファイルで開く。
    付与がない・プロファイルが config にない・reader と writer の付与が混ざっているときは開かない
    （既定のブラウザに渡すと、マスターユーザーで認可してしまうため）。
  - ループバックの OAuth の認可要求で `mcp_url` と一致しないものは開かない
    （`mcp_url` と MCP サーバーの `MCP_SERVER_BASE_URL` のホスト名の食い違いで、
    認可画面が既定のブラウザに開くのを防ぐため）。
  - それ以外の URL は `open <URL>` で既定のブラウザに渡す。
  - config が読めないときは振り分けの対象を判定できないため、どの URL も開かない。

Backlog API は呼ばない（`BSWITCH_MASTER_API_KEY` も読まない）。
"""

from __future__ import annotations

import subprocess
import sys
from urllib.parse import SplitResult, parse_qs, urlsplit

from bswitch.config import Config, ConfigError, load_config
from bswitch.models import Grant
from bswitch.state import load_state
from bswitch.switcher import NO_ACTIVE_GRANT_MESSAGE, active_grants

USAGE = "使い方: bswitch-open-browser <URL>"

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_DEFAULT_PORTS = {"http": 80, "https": 443}


class OpenBrowserError(Exception):
    """URL を開かずに終了する理由。メッセージは stderr に出す。"""


def _origin(parts: SplitResult) -> tuple[str, str, int | None] | None:
    """(スキーム, ホスト (小文字), ポート (省略時は既定値)) を返す。ポートが不正なら None。"""
    try:
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    return scheme, host, port if port is not None else _DEFAULT_PORTS.get(scheme)


def _is_authorization_request(parts: SplitResult) -> bool:
    query = parse_qs(parts.query)
    return "response_type" in query and "client_id" in query


def _browser_profile(config: Config, grants: list[Grant]) -> str:
    """期限内の付与から仮想ユーザーを決め、その Chrome プロファイル名を返す。"""
    roles = {"reader" if g.permission == "read" else "writer" for g in grants}
    if len(roles) > 1:
        raise OpenBrowserError(
            "reader と writer の付与が混ざっているため、認可画面を開きません。"
            "`bswitch release` のあと `bswitch switch <profile>` を実行し直してください"
        )
    role = roles.pop()
    key = f"{role}_browser_profile"
    profile = config.default.writer_browser_profile if role == "writer" else config.default.reader_browser_profile
    if profile is None:
        raise OpenBrowserError(
            f"{role} の Chrome プロファイルが設定されていないため、認可画面を開きません。"
            f"config の [default] に {key} を設定してください"
        )
    return profile


def _load_active_grants(config: Config) -> list[Grant]:
    grants = active_grants(load_state(config.state_path))
    if not grants:
        raise OpenBrowserError(f"{NO_ACTIVE_GRANT_MESSAGE}（仮想ユーザーの付与がないため、認可画面を開きません）")
    return grants


def build_open_command(url: str, config: Config) -> list[str]:
    """URL を開く `open` コマンドの引数を返す。開かないときは OpenBrowserError を送出する。"""
    parts = urlsplit(url)
    origin = _origin(parts)
    mcp_origin = _origin(urlsplit(config.default.mcp_url))
    is_space = parts.hostname is not None and parts.hostname.lower() == config.default.space.lower()
    is_mcp = origin is not None and origin == mcp_origin

    if is_space or is_mcp:
        profile = _browser_profile(config, _load_active_grants(config))
        return ["open", "-na", "Google Chrome", "--args", f"--profile-directory={profile}", url]

    if (parts.hostname or "").lower() in _LOOPBACK_HOSTS and _is_authorization_request(parts):
        raise OpenBrowserError(
            f"MCP サーバーの認可画面と思われる URL が mcp_url ({config.default.mcp_url}) と一致しないため、開きません。"
            "config の mcp_url と、MCP サーバーの MCP_SERVER_BASE_URL のホスト名・ポートをそろえてください"
        )
    return ["open", url]


def main(argv: list[str] | None = None) -> None:
    """コンソールスクリプト `bswitch-open-browser` のエントリポイント。"""
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print(USAGE, file=sys.stderr)
        sys.exit(2)
    url = args[0]

    try:
        config = load_config()
    except ConfigError as exc:
        print(f"bswitch-open-browser: エラー: {exc}（config を読めないため、URL を開きません）", file=sys.stderr)
        sys.exit(1)

    try:
        command = build_open_command(url, config)
    except OpenBrowserError as exc:
        print(f"bswitch-open-browser: エラー: {exc}", file=sys.stderr)
        sys.exit(1)

    # 失敗の理由 (Chrome がないなど) は open 自身が stderr に出す。
    sys.exit(subprocess.run(command, check=False).returncode)


if __name__ == "__main__":
    main()
