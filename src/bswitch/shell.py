"""シェル統合: `shell-init` の出力生成、および export/unset行の生成。

Grantedと同じ「シェル関数ラッパー方式」を採用する:
`.zshrc` に `eval "$(command bswitch shell-init zsh)"` を書くと、
バイナリの標準出力（export/unset行のみ）を `eval` する `bswitch` シェル関数が定義される。
対話UI・通常メッセージは常にstderrへ出るため、evalに混入しない。
"""

from __future__ import annotations

import shlex

#: `bswitch shell-init zsh` が出力するシェル関数定義。
_ZSH_FUNCTION = """\
bswitch() {
  local output
  output=$(command bswitch "$@")
  local exit_code=$?
  if [[ -n "$output" ]]; then
    eval "$output"
  fi
  return $exit_code
}
"""

#: shell-init が対応するシェル一覧。
SUPPORTED_SHELLS: tuple[str, ...] = ("zsh",)


def shell_init(shell: str) -> str:
    """シェル関数定義文字列を返す。zshのみ対応。

    Raises:
        ValueError: zsh以外が指定された場合。
    """
    if shell not in SUPPORTED_SHELLS:
        raise ValueError(f"未対応のシェルです: '{shell}'（対応シェル: {', '.join(SUPPORTED_SHELLS)}）")
    return _ZSH_FUNCTION


def make_export_lines(space: str, project: str | None, mcp_url: str) -> list[str]:
    """switch / restore 成功後にstdoutへ出力するexport行を生成する。

    値はすべて `shlex.quote` でシェル引用符処理する
    （プロジェクトキー・URLに特殊文字が含まれていてもevalが壊れないようにするため）。
    `BACKLOG_API_KEY` は、API キー方式の版 (v0.1.0) で設定したキーをシェルに残さないため unset する。

    Args:
        space: スペースのホスト名（例: "your-space.backlog.jp"）。
        project: 対象プロジェクトキー。複数プロファイル選択時は None（unsetする）。
        mcp_url: config の `mcp_url`。`BSWITCH_MCP_URL` にそのまま入れる。
    """
    lines = [
        "unset BACKLOG_API_KEY",
        f"export BACKLOG_SPACE={shlex.quote(space)}",
        f"export BACKLOG_DOMAIN={shlex.quote(space)}",
    ]
    if project is None:
        lines.append("unset BACKLOG_PROJECT")
    else:
        lines.append(f"export BACKLOG_PROJECT={shlex.quote(project)}")
    lines.append(f"export BSWITCH_MCP_URL={shlex.quote(mcp_url)}")
    return lines


def make_unset_lines() -> list[str]:
    """release 成功後と、期限内の付与がないときの restore でstdoutへ出力するunset行を生成する。"""
    return ["unset BACKLOG_API_KEY BACKLOG_SPACE BACKLOG_DOMAIN BACKLOG_PROJECT BSWITCH_MCP_URL"]
