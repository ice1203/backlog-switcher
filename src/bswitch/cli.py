"""bswitch CLIエントリポイント（argparse）。

出力の分離（docs/design.md「シェル統合の仕組み」の厳守事項）:
  - 対話UIメッセージ・警告・エラー・list/statusの表示結果はすべてstderrへ。
  - stdoutには `switch`/`release` 成功時のexport/unset行のみを出力する
    （シェル関数 `bswitch()` がこれを丸ごと `eval` するため、他の文字列が混じると壊れる）。

環境変数の入出力分離（docs/design.md「環境変数設計」の厳守事項）:
  - bswitchが読む入力は `BSWITCH_MASTER_API_KEY`（必須）と `BSWITCH_CONFIG`（任意）のみ。
  - `BACKLOG_API_KEY` はswitchが書き込む出力専用であり、bswitch自身は一切読まない。
    例外: `check` サブコマンドは「現在の BACKLOG_API_KEY が期待キーと整合しているか」を
    確認する目的に限り参照する。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import httpx

from bswitch import shell, switcher, ui
from bswitch.api import BacklogApiError, BacklogClient
from bswitch.config import Config, ConfigError, load_config
from bswitch.keys import KeyResolutionError, resolve_api_key
from bswitch.models import Profile, State
from bswitch.state import load_state, save_state
from bswitch.switcher import SwitcherError

#: bswitchが読む唯一の入力用マスターAPIキー環境変数（docs/design.md「環境変数設計」）。
ENV_MASTER_API_KEY = "BSWITCH_MASTER_API_KEY"

#: cli層で一律に捕捉し「エラー: <message>」として終了するアプリケーションレベルの例外群。
#: いずれもAPIキーの値を含まないことをそれぞれのモジュールで確認済み。
_APP_ERRORS: tuple[type[Exception], ...] = (
    ConfigError,
    BacklogApiError,
    KeyResolutionError,
    SwitcherError,
    ValueError,
)


def build_parser() -> argparse.ArgumentParser:
    """CLI引数パーサーを構築する。"""
    parser = argparse.ArgumentParser(
        prog="bswitch",
        description="Backlog権限スイッチャーCLI: 仮想ユーザーを必要な時だけ最小権限でプロジェクトへ参加させる",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    switch_parser = subparsers.add_parser("switch", help="仮想ユーザーを対象プロジェクトへ参加させる（排他動作）")
    switch_parser.add_argument(
        "profiles",
        nargs="*",
        help="プロファイル名（0個の場合はインタラクティブ選択UIを起動）",
    )
    switch_parser.add_argument(
        "--multi",
        action="store_true",
        help="インタラクティブ選択時にチェックボックス式の複数選択UIを使う",
    )
    switch_parser.add_argument(
        "--duration",
        default=None,
        metavar="DURATION",
        help="有効期限（例: 30s / 30m / 2h / 8h）。未指定時はconfigのdefault_durationを使用",
    )

    release_parser = subparsers.add_parser(
        "release", help="仮想ユーザーを除名する（既定はstate.jsonに記録された参加中プロジェクトのみ）"
    )
    release_parser.add_argument(
        "--all",
        action="store_true",
        help="config定義済みの全プロジェクトを走査して除名する（state.json消失時などの回復用）",
    )
    subparsers.add_parser("status", help="各プロファイルの参加状況・有効期限を表示する")
    subparsers.add_parser("list", help="configのプロファイル一覧を表示する")
    subparsers.add_parser("enforce", help="期限切れの付与だけを解除する（定期実行用）")

    subparsers.add_parser(
        "check",
        help="現在の BACKLOG_API_KEY が state.json の付与状態と整合しているか確認する（Backlog APIを呼ばない）",
    )

    shell_init_parser = subparsers.add_parser("shell-init", help="シェル統合用の関数定義を出力する")
    shell_init_parser.add_argument("shell", choices=list(shell.SUPPORTED_SHELLS), help="対象シェル（zshのみ対応）")

    return parser


def _handle_shell_init(shell_name: str) -> None:
    try:
        script = shell.shell_init(shell_name)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    # eval対象のためstdoutへ。
    print(script)


def _handle_list(config: Config) -> None:
    if not config.profiles:
        print("プロファイルが定義されていません", file=sys.stderr)
        return
    header = ("NAME", "PROJECT", "PERMISSION")
    rows = [(p.name, p.project, p.permission) for p in config.profiles.values()]
    widths = [max(len(row[i]) for row in [header, *rows]) for i in range(3)]
    print("  ".join(header[i].ljust(widths[i]) for i in range(3)), file=sys.stderr)
    print("  ".join("-" * widths[i] for i in range(3)), file=sys.stderr)
    for row in rows:
        print("  ".join(row[i].ljust(widths[i]) for i in range(3)), file=sys.stderr)


def _handle_check(config: Config, state: State) -> None:
    if not state.grants:
        print("[]")
        return
    # BACKLOG_API_KEY は bswitch の出力専用だが、check は「現在値が期待キーと一致するか」を
    # 目的とするため例外的に読む。
    current_key = os.environ.get("BACKLOG_API_KEY")
    results: list[dict[str, str]] = []
    for grant in state.grants:
        try:
            expected_key = resolve_api_key(grant.permission, config.default)
        except KeyResolutionError as exc:
            print(f"エラー: {exc}", file=sys.stderr)
            sys.exit(1)
        if current_key is None:
            status = "NOT_SET"
        elif current_key == expected_key:
            status = "OK"
        else:
            status = "MISMATCH"
        results.append({"profile": grant.profile, "permission": grant.permission, "status": status})
    print(json.dumps(results, ensure_ascii=False))


def _resolve_selected_profiles(args: argparse.Namespace, config: Config, all_profiles: list[Profile]) -> list[Profile]:
    if args.profiles:
        return [config.get_profile(name) for name in args.profiles]
    if args.multi:
        return ui.select_profiles_multi(all_profiles)
    return [ui.select_profile(all_profiles)]


_EXPORT_API_KEY_PREFIX = "export BACKLOG_API_KEY="


def _mask_api_key_line(line: str) -> str:
    """`export BACKLOG_API_KEY=...` 行の値部分だけを伏せる（他のexport行はそのまま）。"""
    if line.startswith(_EXPORT_API_KEY_PREFIX):
        return f"{_EXPORT_API_KEY_PREFIX}********"
    return line


def _handle_switch(args: argparse.Namespace, config: Config, client: BacklogClient, state: State) -> None:
    all_profiles = list(config.profiles.values())
    selected = _resolve_selected_profiles(args, config, all_profiles)
    if not selected:
        # ui.py側で0件はexit(0)される想定だが、防御的にここでも扱う。
        sys.exit(0)

    lines, _permission = switcher.switch(
        selected,
        all_profiles,
        config.default,
        args.duration,
        client,
        state,
        save=lambda: save_state(config.state_path, state),
    )
    save_state(config.state_path, state)

    if sys.stdout.isatty():
        # シェル関数（`eval "$(command bswitch shell-init zsh)"`）経由ではなく、
        # ターミナルへ直接出力されている＝evalされない＝APIキーの生値を表示する意味がない。
        # 生値の端末出力（スクロールバック・ターミナルログへの残留）を避けるため伏せて表示する。
        print(
            "警告: シェル統合が設定されていないため、環境変数は現在のシェルにエクスポートされません。"
            '.zshrcに `eval "$(command bswitch shell-init zsh)"` を追記してください。',
            file=sys.stderr,
        )
        for line in lines:
            print(_mask_api_key_line(line))
    else:
        for line in lines:
            print(line)


def _handle_release(args: argparse.Namespace, config: Config, client: BacklogClient, state: State) -> None:
    all_profiles = list(config.profiles.values())
    had_grants = bool(state.grants)
    lines = switcher.release(
        config.default,
        all_profiles,
        client,
        state,
        save=lambda: save_state(config.state_path, state),
        all_projects=args.all,
    )
    save_state(config.state_path, state)
    if args.all:
        print("config定義済みの全プロジェクトから仮想ユーザーを除名しました", file=sys.stderr)
    elif had_grants:
        print("state.jsonに記録された参加中プロジェクトから仮想ユーザーを除名しました", file=sys.stderr)
    else:
        print("参加中の付与記録はありません（環境変数のunsetのみ行います）", file=sys.stderr)
    for line in lines:
        print(line)


def _handle_status(config: Config, client: BacklogClient, state: State) -> None:
    all_profiles = list(config.profiles.values())
    text = switcher.get_status(all_profiles, config.default, client, state)
    # get_status()内でメール解決が起きた場合に備えキャッシュを永続化する。
    save_state(config.state_path, state)
    print(text, file=sys.stderr)


def main() -> None:
    """CLIエントリポイント（`pyproject.toml` の `project.scripts` から呼ばれる）。"""
    if len(sys.argv) == 1:
        sys.argv.append("switch")
    args = build_parser().parse_args()

    if args.command == "shell-init":
        _handle_shell_init(args.shell)
        return

    config = load_config()

    if args.command == "list":
        _handle_list(config)
        return

    if args.command == "check":
        state = load_state(config.state_path)
        _handle_check(config, state)
        return

    master_key = os.environ.get(ENV_MASTER_API_KEY)
    if not master_key:
        print(f"{ENV_MASTER_API_KEY} 環境変数を設定してください", file=sys.stderr)
        sys.exit(1)

    state = load_state(config.state_path)

    try:
        with BacklogClient(config.default.space, master_key) as client:
            all_profiles = list(config.profiles.values())

            # 遅延強制: list/shell-init以外の全サブコマンド冒頭で期限切れGrantを解除する。
            removed = switcher.enforce_expired(
                config.default,
                all_profiles,
                client,
                state,
                save=lambda: save_state(config.state_path, state),
            )
            save_state(config.state_path, state)
            if removed:
                print(f"期限切れの付与を{removed}件解除しました", file=sys.stderr)

            if args.command == "switch":
                _handle_switch(args, config, client, state)
            elif args.command == "release":
                _handle_release(args, config, client, state)
            elif args.command == "status":
                _handle_status(config, client, state)
            elif args.command == "enforce":
                pass  # 遅延強制の呼び出しで既に処理済み。
    except _APP_ERRORS as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        sys.exit(1)
    except httpx.HTTPError:
        # httpxの例外メッセージ・URLにはAPIキー（apiKeyクエリパラメータ）が含まれ得るため、
        # str(exc) を一切表示せず固定の安全なメッセージのみを出す。
        print("エラー: Backlogとの通信に失敗しました（ネットワーク接続を確認してください）", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("中断しました", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
