"""InquirerPyによる対話UI。

重要: InquirerPy（prompt_toolkit）はデフォルトではUI描画をstdoutに行う
（`AppSession.output` が未指定の場合 `create_output()` が `sys.stdout` を使うため。
prompt_toolkit本体のソースで確認済み）。
本ツールはstdoutをシェル関数のeval対象として使うため、これは致命的に不都合。
そのため `create_app_session(output=...)` でstderr向けのOutputを明示的に注入し、
UI描画を含む全メッセージをstderrへ強制する。
"""

from __future__ import annotations

import sys

from InquirerPy import inquirer
from prompt_toolkit.application.current import create_app_session
from prompt_toolkit.output import create_output

from bswitch.models import Profile


def _format_choice(profile: Profile) -> str:
    return f"{profile.name} ({profile.project} / {profile.permission})"


def _make_choices(profiles: list[Profile]) -> list[dict[str, object]]:
    # InquirerPyの `Choice`（dataclass）は内部で `dataclasses.asdict()` を使うため、
    # value にProfile（dataclass）を入れると再帰的にdict化されてしまう。
    # dict形式のchoiceならvalueがasdictを通らずProfileのまま保持される。
    return [{"name": _format_choice(p), "value": p} for p in profiles]


def select_profile(profiles: list[Profile]) -> Profile:
    """単一選択（fuzzy filter付き）。

    選択されず終了（Ctrl+C等）した場合は `sys.exit(0)` する。
    """
    choices = _make_choices(profiles)
    output = create_output(stdout=sys.stderr)
    try:
        with create_app_session(output=output):
            result = inquirer.fuzzy(
                message="切り替えるプロファイルを選択してください:",
                choices=choices,
            ).execute()
    except KeyboardInterrupt:
        result = None

    if result is None:
        print("プロファイルが選択されなかったため終了します", file=sys.stderr)
        sys.exit(0)
    return result


def select_profiles_multi(profiles: list[Profile]) -> list[Profile]:
    """複数選択（チェックボックス式）。

    0件選択（何も選ばずEnter、またはCtrl+C等でのキャンセル）の場合は `sys.exit(0)` する。
    """
    choices = _make_choices(profiles)
    output = create_output(stdout=sys.stderr)
    try:
        with create_app_session(output=output):
            result = inquirer.checkbox(
                message="切り替えるプロファイルを選択してください（スペースで選択、Enterで確定）:",
                choices=choices,
            ).execute()
    except KeyboardInterrupt:
        result = []

    if not result:
        print("プロファイルが選択されなかったため終了します", file=sys.stderr)
        sys.exit(0)
    return result
