# CLAUDE.md

## 依存関係の更新手順（uv.lock）— 必読

このリポジトリは**公開リポジトリ**のため、`uv.lock` は必ず公式PyPIのみを参照した状態を保つ。
正常な状態では、uv.lock に含まれるURLのホストは `pypi.org` と `files.pythonhosted.org` の2つだけ。

ローカル環境にプライベートレジストリ設定（環境変数 `UV_INDEX_URL` や `~/.config/uv/uv.toml` の `[[index]]`）がある場合、通常の `uv add` / `uv lock` を実行すると**そのレジストリのURLが uv.lock に焼き込まれる**。第三者がインストールできなくなり、社内利用ツールの情報も公開されるため、依存の追加・更新は必ず以下の形で行う:

```bash
# ロック再生成
env -u UV_INDEX_URL uv lock --no-config --default-index https://pypi.org/simple/

# 依存追加が必要な場合も同様に
env -u UV_INDEX_URL uv add --no-config --default-index https://pypi.org/simple/ <package>
```

普段の実行（`uv run --frozen ...` / `uv sync --frozen`）はロックを書き換えないため影響なし。

### push前チェック（必須）

以下の出力が `pypi.org` と `files.pythonhosted.org` の2行だけであることを確認する:

```bash
grep -oE 'https://[^/"]+' uv.lock | sort -u
```

それ以外のホストが出た場合は、上記のロック再生成コマンドでやり直してからpushする。

## 開発コマンド

```bash
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen pytest              # 単体テスト（実APIは呼ばない）
```

## 公開リポジトリとしての注意

- 実スペース名・実メールアドレス・APIキー・社内ツール名をコード・コメント・ロックファイルに含めない
- E2Eテストの実環境情報は環境変数（`BSWITCH_E2E_*`）注入のみ。ハードコード禁止
- `.work/`（設計メモ置き場）はgitignore済み。ソースコードのコメントから `.work/` 配下のファイルを参照しない
