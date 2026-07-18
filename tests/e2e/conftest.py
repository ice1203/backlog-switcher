"""tests/e2e 共通フィクスチャ。

「10. E2Eテスト仕様」および「禁止事項」に従う。特に以下を厳守する:

- **E2Eが操作してよいのは環境変数 `BSWITCH_E2E_SPACE` で指定したスペースの
  `MY_PROJECT` プロジェクトのみ**。それ以外のスペースへは一切アクセスしない。
- 環境変数 `BSWITCH_E2E=1` が未設定なら全テストをスキップする
  （テストモジュール側で `pytest.mark.skipif` を使う。加えてこのファイルの各フィクスチャでも
  防御的に同じチェックを行い、将来追加されるテストファイルが誤ってフラグを見落としても
  実APIを呼ばないようにする）。
- 対象スペース・writer/readerのメールアドレスは環境変数
  （`BSWITCH_E2E_SPACE` / `BSWITCH_E2E_WRITER_EMAIL` / `BSWITCH_E2E_READER_EMAIL`、
  いずれもデフォルトなし・必須）から取得する。実スペース名・実メールアドレスをコードに
  ハードコードしないため。未設定時は `pytest.fail` で即座に停止する。
- 対象スペースが `BSWITCH_E2E_SPACE` の値と一致しなければ **即失敗**（スキップではなく
  `pytest.fail`）させる。
- APIキーはログ・ファイル・テストコードに一切書き出さない。
- テスト用configは環境変数から組み立てた内容を毎回 `$TMPDIR/bswitch-e2e-config` に書き出して
  使う（brief記載のテスト用config仕様どおり）。
"""

from __future__ import annotations

import os
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from bswitch.api import BacklogClient
from bswitch.config import Config, load_config

#: E2Eが操作してよい唯一のプロジェクト。
ALLOWED_PROJECT = "MY_PROJECT"


def e2e_enabled() -> bool:
    """`BSWITCH_E2E=1` が明示的にセットされているかどうか。"""
    return os.environ.get("BSWITCH_E2E") == "1"


def _tmpdir() -> Path:
    return Path(os.environ.get("TMPDIR") or tempfile.gettempdir())


def _guard_e2e_enabled() -> None:
    """個々のフィクスチャからも呼べる防御的ガード（多重防御）。"""
    if not e2e_enabled():
        pytest.skip("BSWITCH_E2E=1 が設定されていないためE2Eテストをスキップします")


def _required_env(name: str) -> str:
    """必須環境変数を読む。未設定なら pytest.fail する（E2E実行の前提条件エラー）。"""
    value = os.environ.get(name)
    if not value:
        pytest.fail(
            f"環境変数 '{name}' が設定されていません（E2Eの前提条件）。"
            "BSWITCH_E2E_SPACE / BSWITCH_E2E_WRITER_EMAIL / BSWITCH_E2E_READER_EMAIL を"
            "すべて設定してから実行してください。",
            pytrace=False,
        )
    return value


def _build_e2e_config_content(space: str, writer_email: str, reader_email: str) -> str:
    """E2Eテスト用configの内容を組み立てる（brief「E2Eテスト仕様」記載の構成どおり）。

    space・writer_user・reader_userは環境変数から注入する
    （実スペース名・実メールアドレスをコードにハードコードしないため）。
    """
    return f"""\
[default]
space = {space}
writer_user = {writer_email}
reader_user = {reader_email}
writer_api_key_ref = op://dummy/not-used
reader_api_key_ref = op://dummy/not-used

[profile test-read]
project = MY_PROJECT
permission = read

[profile test-write]
project = MY_PROJECT
permission = write

[profile test-admin]
project = MY_PROJECT
permission = admin
"""


@pytest.fixture(scope="session")
def e2e_space() -> str:
    """E2Eの対象スペースホスト名（`BSWITCH_E2E_SPACE`、デフォルトなし・必須）。"""
    _guard_e2e_enabled()
    return _required_env("BSWITCH_E2E_SPACE")


@pytest.fixture(scope="session")
def e2e_writer_email() -> str:
    """writer仮想ユーザーのメールアドレス（`BSWITCH_E2E_WRITER_EMAIL`、デフォルトなし・必須）。"""
    _guard_e2e_enabled()
    return _required_env("BSWITCH_E2E_WRITER_EMAIL")


@pytest.fixture(scope="session")
def e2e_reader_email() -> str:
    """reader仮想ユーザーのメールアドレス（`BSWITCH_E2E_READER_EMAIL`、デフォルトなし・必須）。"""
    _guard_e2e_enabled()
    return _required_env("BSWITCH_E2E_READER_EMAIL")


@pytest.fixture(scope="session")
def e2e_config_path(e2e_space: str, e2e_writer_email: str, e2e_reader_email: str) -> Path:
    """テスト用configファイルを `$TMPDIR/bswitch-e2e-config` に生成する。

    実行のたびに内容を上書きするため、前回実行の残骸に影響されない。
    """
    path = _tmpdir() / "bswitch-e2e-config"
    path.write_text(_build_e2e_config_content(e2e_space, e2e_writer_email, e2e_reader_email), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


@pytest.fixture(scope="session")
def e2e_config(e2e_config_path: Path, e2e_space: str) -> Config:
    """テスト用configを読み込み、対象スペース・対象プロジェクトの安全チェックを行う。

    - space が `e2e_space`（`BSWITCH_E2E_SPACE`）と一致しなければ即失敗（`pytest.fail`）させる。
    - 定義済み全プロファイルの project が `MY_PROJECT` 以外を指していないか確認する
      （config定義外のプロジェクトを誤って操作しないための安全装置）。
    """
    _guard_e2e_enabled()
    config = load_config(e2e_config_path)

    if config.default.space != e2e_space:
        pytest.fail(
            f"E2Eの対象スペースが '{e2e_space}' ではありません（実際: '{config.default.space}'）。"
            "意図しないスペースへの誤操作を防ぐためテストを拒否します。",
            pytrace=False,
        )
    for profile in config.profiles.values():
        if profile.project != ALLOWED_PROJECT:
            pytest.fail(
                f"E2Eが操作してよいのは '{ALLOWED_PROJECT}' のみです"
                f"（プロファイル '{profile.name}' が '{profile.project}' を参照しています）。",
                pytrace=False,
            )
    return config


class _RedactedSecret:
    """秘密値をラップし、pytestの失敗トレースバック（引数一覧のrepr表示）に生値が
    出力されないようにするための小さなラッパー。

    初回実機E2E実行（2026-07-15）で、`master_api_key` をテスト関数の引数として直接
    受け取っていたテストが失敗した際、pytestのデフォルトlong形式トレースバックが
    失敗フレームの引数値をそのまま `repr()` して出力し、APIキーの生値がツール出力に
    露出する事故が発生した。この再発防止のため、秘密値を保持するフィクスチャは
    常にこのラッパー経由で受け渡す（実値が必要な箇所でのみ明示的に `.reveal()` する）。
    """

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "<redacted>"

    def __str__(self) -> str:
        return "<redacted>"


@pytest.fixture(scope="session")
def master_api_key() -> _RedactedSecret:
    """マスターユーザーのAPIキー（`BSWITCH_MASTER_API_KEY` からのみ読む）。

    トレースバック露出防止のため `_RedactedSecret` でラップして返す。
    このフィクスチャを直接requestするテスト関数を新設する場合は、
    生値が必要になっても `.reveal()` をテスト関数内のローカル変数に一時保持するだけに留め、
    テスト関数の**引数**としては絶対に生の `str` を要求しないこと。
    """
    _guard_e2e_enabled()
    key = os.environ.get("BSWITCH_MASTER_API_KEY")
    if not key:
        pytest.fail("BSWITCH_MASTER_API_KEY が設定されていません（E2Eの前提条件）", pytrace=False)
    return _RedactedSecret(key)


@pytest.fixture(scope="session")
def master_client(e2e_config: Config, master_api_key: _RedactedSecret) -> Iterator[BacklogClient]:
    """マスターAPIキーで初期化した BacklogClient（`BSWITCH_E2E_SPACE` で指定したスペース専用）。

    `BacklogClient.__repr__` はキーを含めない安全な実装になっている
    （`src/bswitch/api.py` 参照）ため、このフィクスチャ自体はテスト関数の引数として
    直接requestしてよい。
    """
    with BacklogClient(e2e_config.default.space, master_api_key.reveal()) as client:
        yield client


@pytest.fixture(autouse=True)
def _pace_requests() -> Iterator[None]:
    """対象スペースのレート制限（60 req/min、実測値）に配慮し、テスト間隔を空ける。

    初回実機E2E実行（2026-07-15）で、8ステップを無間隔で連続実行した結果
    `HTTP 429 Too Many Requests` を受け、`release` のexclusive-remove（エラーを
    無視する設計）が見かけ上成功して後片付け漏れを起こす事故があったための対策。
    """
    yield
    if e2e_enabled():
        time.sleep(2.0)


@pytest.fixture(scope="session", autouse=True)
def _cleanup_after_session(e2e_config_path: Path, e2e_space: str) -> Iterator[None]:
    """セッション終了時、テスト内容にかかわらず必ず対象プロジェクトから両仮想ユーザーを除名する後片付け。

    テスト途中で失敗しても後片付けが漏れないようにするための安全網
    （brief「後片付け確認」要件・完了基準「E2E終了後、テストプロジェクトから仮想ユーザーが
    除名されている」に対応）。E2E無効時・configが `BSWITCH_E2E_SPACE` 以外を指す異常時は
    何もしない。
    """
    yield
    if not e2e_enabled():
        return
    try:
        config = load_config(e2e_config_path)
    except Exception:
        return
    if config.default.space != e2e_space:
        return
    key = os.environ.get("BSWITCH_MASTER_API_KEY")
    if not key:
        return

    from bswitch import switcher
    from bswitch.state import load_state, save_state

    all_profiles = list(config.profiles.values())
    state = load_state(config.state_path)
    try:
        with BacklogClient(config.default.space, key) as client:
            switcher.release(config.default, all_profiles, client, state)
    except Exception:
        # 後片付け自体の失敗はテスト結果に影響させない（best-effort）。
        return
    save_state(config.state_path, state)
