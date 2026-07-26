#!/usr/bin/env bash
set -euo pipefail
INPUT=$(cat)

# ─────────────────────────────────────────────────────────────────────
# bswitch プロジェクト整合ガード（SessionStart）
# セッション開始時に `bswitch check` を実行し、「作業ディレクトリ」と
# 「現在Backlogへ付与中のプロジェクト」をコンテキストへ注入して、
# 両者が対応しているかをAI自身に判定・報告させる。
# 目的: 別ターミナルで別プロファイルへswitch済みのまま作業を続け、
# 無関係な顧客のプロジェクトへ書き込んでしまう事故を、作業開始前に気づかせる。
# SessionStartはツール実行をブロックできないため本スクリプトは注意喚起に留まる。
# キー不整合の強制ブロックはPreToolUseのbswitch_key_guard.shが担う。
# ─────────────────────────────────────────────────────────────────────

BSWITCH="$HOME/.local/bin/bswitch"

CWD=$(echo "$INPUT" | jq -r '.cwd // empty')

# bswitch未導入環境では何もしない
[[ -x "$BSWITCH" ]] || exit 0

# checkはJSONをstderrに出す（stdoutはシェル関数のeval対象のため常に空）。
# check自体の失敗（config未作成等）・JSON以外の出力は注意喚起を諦めてスキップする（fail-open）。
CHECK_JSON=$("$BSWITCH" check 2>&1 >/dev/null) || exit 0
echo "$CHECK_JSON" | jq -e 'type == "array"' >/dev/null 2>&1 || exit 0

GRANT_COUNT=$(echo "$CHECK_JSON" | jq 'length')

if [[ "$GRANT_COUNT" == "0" ]]; then
  # 付与0件かつキーも無い状態は正常（Backlogを使っていない）。
  # Backlogと無関係な作業のノイズになるため何も出力しない。
  if [[ -n "${BACKLOG_API_KEY:-}" ]]; then
    cat <<'EOF'
【bswitch: 警告】
BACKLOG_API_KEY が設定されていますが、付与記録（state.json）は0件です。
解除済みプロファイルのキーが残留している可能性が高く、Backlogアクセスは権限エラーになります。
Backlog操作が必要な場合は、ユーザーに「bswitch switch <profile> の実行」を依頼してください。
権限のある別のプロジェクトやキーで勝手に再試行してはいけません。
EOF
  fi
  exit 0
fi

# 全プロファイル一覧（bswitch list相当）は注入しない。
# 毎セッション全プロジェクトキーがコンテキストに載るのを避けるため、
# 判断に必要な「付与中の分」だけを出す。
GRANT_LINES=$(echo "$CHECK_JSON" | jq -r \
  '.[] | "  - profile=\(.profile) project=\(.project) permission=\(.permission) status=\(.status)"')

# 前半は cwd と付与内容を埋め込むため展開ありのheredoc。
cat <<EOF
【bswitch: セッション開始時チェック — 最初の応答で必ず実行すること】
作業ディレクトリ: ${CWD}
現在Backlogへ付与中の権限:
${GRANT_LINES}
status の意味: OK=このシェルの BACKLOG_API_KEY は付与記録と一致 / MISMATCH・NOT_SET=不一致 / UNKNOWN=判定不能
EOF

# 後半はバッククォートや <...> を含む固定文面のため、シェル展開を止めた引用付きheredocで出す。
cat <<'EOF'

このセッションの最初の応答で、ユーザーの依頼に着手する前に、次を必ず実行すること。
Backlogを操作する予定があるかどうかに関わらず、セッション開始時に1回必ず行う。

1. 上記の作業ディレクトリのパス・ディレクトリ名・そこにある CLAUDE.md / README から、
   このディレクトリがどの案件・顧客・プロジェクトのものかを把握する
2. それが上記の付与中プロジェクト（project=）と意味的に対応しているかを判定する
   （例: ディレクトリ名 customer-a ↔ project=CUSTOMER_A は対応。無関係な名称なら不一致）
3. 判定結果を応答の**冒頭**に**1行**で報告する。次の書式をそのまま使うこと
   （絵文字・太字・バッククォートを含める。ユーザーが一目で気づけるようにするため）:

   - status が OK 以外（MISMATCH / NOT_SET / UNKNOWN）※プロジェクト対応の判定より優先して表示する
     🚨 **bswitch: キー不整合 (<status>)** — `<project>` (<permission>) の付与記録に対し、このシェルの BACKLOG_API_KEY が一致していません。`bswitch switch <profile>` を再実行してください

   - プロジェクトが対応しない
     🚨 **bswitch: プロジェクト不一致** — この作業ディレクトリは `<推定した案件>` に見えますが、付与中は `<project>` (<permission>) です。`bswitch switch <profile>` を実行してください

   - 対応を判断できない
     ❓ **bswitch: 判断不能** — `<project>` (<permission>) 付与中。この作業ディレクトリとの対応が判断できません

   - 対応する かつ status=OK
     ✅ **bswitch** — `<project>` (<permission>) で整合

このセッション中は継続して次を守ること:
- 上記の判定が不一致・判断不能のまま Backlog を操作しない。必要ならユーザーへ対象プロジェクトを確認する
- 権限エラーが出ても、権限のある別プロジェクトへ書き込んで回避してはならない
EOF

exit 0
