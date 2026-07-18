"""bswitch 実機E2Eテスト（`BSWITCH_E2E_SPACE` で指定したスペースの MY_PROJECT のみ対象）。

以下の8項目を、この順序で直列実行する:

  1. 前提確認（スペース確認・プロジェクト存在確認・仮想ユーザー2人のID解決）
  2. switch test-read → reader が参加
  3. switch test-write → reader除名・writer参加（排他動作。CLI経由で検証）
  4. switch test-write 再実行 → エラーにならない（冪等性）
  5. switch test-admin → writer参加＋プロジェクト管理者フラグ付与（プラン制約なら記録して継続）
  6. status → 実際の参加状況と表示が一致
  7. TTL: switch test-read --duration 30s → 30秒待機 → enforce → reader除名
  8. release → 両仮想ユーザーが除名（後片付けを兼ねる。CLI経由で検証）

実装方法:
  - 基本は `switcher.py` / `api.py` を直接Pythonから呼び出し、APIの結果を直接検証する。
  - ステップ3・8はCLI（`uv run bswitch ...`）をsubprocess経由で呼び、
    stdout（evalされるexport/unset行）とstderr（メッセージ）の分離を確認する。

安全のための注意:
  - APIキーはテストコード・出力に一切埋め込まない。CLI呼び出しのstdout/stderrは、
    値そのものではなく「export/unsetされている変数名」だけを検査する
    （失敗時にpytestが差分をダンプしてもキーの値が露出しないようにするため）。
  - マスターAPIキーはテスト関数の**引数**としては絶対に受け取らない
    （`master_api_key` フィクスチャは `_RedactedSecret` でラップ済みだが、
    生の文字列を直接引数にすると失敗時のpytestトレースバックへ値が出力されるリスクがあるため、
    CLIサブプロセス呼び出しは常に `os.environ` を素通しする形にしている）。
  - 各ステップは直列実行（並列化しない）。
  - 対象スペース（`BSWITCH_E2E_SPACE`）の実測レート制限は60 req/min。`errors[].code == 13`
    （TooManyRequestsError）を検出した場合はテスト側で待機してリトライする
    （`_call_with_retry` / `_run_cli`）。src側の冪等性設計とは無関係の、
    テスト実行の安定性のためだけの仕組み。
"""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from bswitch import switcher
from bswitch.api import BacklogApiError, BacklogClient
from bswitch.config import Config
from bswitch.state import load_state, save_state

from .conftest import ALLOWED_PROJECT, e2e_enabled

#: errors[].code == 13: TooManyRequestsError。
_CODE_TOO_MANY_REQUESTS = 13

#: BSWITCH_E2E=1 が未設定なら本モジュールの全テストをスキップする。
#: skipif はフィクスチャのセットアップより前に評価されるため、フラグ未設定時は実APIに一切触れない。
pytestmark = pytest.mark.skipif(
    not e2e_enabled(),
    reason="BSWITCH_E2E=1 が設定されていないためE2Eテストをスキップします"
    "（明示フラグなしでテストから実APIを呼ばないための安全装置）",
)

#: リポジトリルート（tests/e2e/test_e2e.py から3階層上）。CLIサブプロセスのcwdに使う。
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _members(client: BacklogClient, project: str) -> set[int]:
    return {int(u["id"]) for u in client.get_project_users(project) if u.get("id") is not None}


def _admins(client: BacklogClient, project: str) -> set[int]:
    return {int(a["id"]) for a in client.get_project_administrators(project) if a.get("id") is not None}


def _call_with_retry[T](func: Callable[[], T], *, max_attempts: int = 3, backoff_seconds: float = 20.0) -> T:
    """`errors[].code == 13`（TooManyRequestsError）を検出した場合に待機してリトライする。

    対象スペース（`BSWITCH_E2E_SPACE`）の実測レート制限（60 req/min）に対するテスト側の
    耐性であり、src側の「エラーを無視する冪等性」設計（api.pyのdelete系メソッド等）とは無関係。
    それ以外のエラーはそのまま送出する。
    """
    last_exc: BacklogApiError | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return func()
        except BacklogApiError as exc:
            if exc.code != _CODE_TOO_MANY_REQUESTS or attempt == max_attempts:
                raise
            last_exc = exc
            print(
                f"[rate-limit] code=13 (TooManyRequestsError) を検出。"
                f"{backoff_seconds:.0f}秒待機してリトライします（{attempt}/{max_attempts}）"
            )
            time.sleep(backoff_seconds)
    assert last_exc is not None  # pragma: no cover - ループは必ずreturnかraiseで抜ける
    raise last_exc


def _run_cli(
    args: list[str], config_path: Path, *, max_attempts: int = 3, backoff_seconds: float = 20.0
) -> subprocess.CompletedProcess[str]:
    """`uv run bswitch <args>` をサブプロセスで実行する（stdout/stderr分離確認用）。

    マスターAPIキーはテスト関数の引数として受け取らず、常に親プロセスの環境変数
    （`BSWITCH_MASTER_API_KEY`。E2Eの前提条件として既に設定済み）をそのまま子プロセスへ
    継承させる。`BSWITCH_CONFIG` のみテスト用configのパスで上書きする。
    レート制限（stderrに "Rate Limit" を含みexit code 1）を検出した場合は待機してリトライする。
    """
    env = dict(os.environ)
    env["BSWITCH_CONFIG"] = str(config_path)

    for attempt in range(1, max_attempts + 1):
        result = subprocess.run(
            ["uv", "run", "bswitch", *args],
            cwd=str(_PROJECT_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if result.returncode != 0 and "Rate Limit" in result.stderr and attempt < max_attempts:
            print(
                f"[rate-limit] CLI呼び出しでレート制限を検出。"
                f"{backoff_seconds:.0f}秒待機してリトライします（{attempt}/{max_attempts}）"
            )
            time.sleep(backoff_seconds)
            continue
        return result
    return result  # pragma: no cover - ループは必ず途中でreturnする


def _exported_var_names(stdout: str) -> set[str]:
    """`export FOO=...` 行から `FOO` だけを取り出す（値は一切保持しない）。"""
    names: set[str] = set()
    for line in stdout.strip().splitlines():
        if line.startswith("export "):
            names.add(line[len("export ") :].split("=", 1)[0])
    return names


def _unset_var_names(stdout: str) -> set[str]:
    names: set[str] = set()
    for line in stdout.strip().splitlines():
        if line.startswith("unset "):
            names.update(line[len("unset ") :].split())
    return names


@pytest.fixture(scope="module")
def resolved_ids(e2e_config: Config, master_client: BacklogClient) -> dict[str, int]:
    """writer/readerの数値IDを一度だけ解決し、モジュール全体で共有する。"""
    state = load_state(e2e_config.state_path)
    writer_id, reader_id = _call_with_retry(lambda: switcher.resolve_user_ids(e2e_config.default, master_client, state))
    save_state(e2e_config.state_path, state)
    return {"writer": writer_id, "reader": reader_id}


class TestE2EFlow:
    """E2Eテスト8項目をこの順序で直列実行するテストクラス。

    pytestはデフォルトでモジュール内のテストを定義順に収集・実行する
    （本リポジトリにはpytest-randomly等の順序をランダム化するプラグインは入っていない）。
    番号プレフィックスは実行順序を明示するためのもの。
    """

    # -- 1. 前提確認 -----------------------------------------------------

    def test_01_preconditions(
        self,
        e2e_config: Config,
        master_client: BacklogClient,
        resolved_ids: dict[str, int],
        e2e_space: str,
        e2e_writer_email: str,
        e2e_reader_email: str,
    ) -> None:
        # スペース確認（conftest.e2e_config フィクスチャで既にpytest.failガード済みだが明示的にも確認）。
        assert e2e_config.default.space == e2e_space

        # プロジェクト存在確認: MY_PROJECTが存在しなければ get_project_users が
        # BacklogApiError（code 6 相当）を送出するため、例外なく完了すること自体が存在確認になる。
        members_before = _members(master_client, ALLOWED_PROJECT)
        print(f"[01 precondition] MY_PROJECT is reachable. current members(id)={sorted(members_before)}")

        # 仮想ユーザー2人のID解決。
        writer_id = resolved_ids["writer"]
        reader_id = resolved_ids["reader"]
        assert isinstance(writer_id, int) and writer_id > 0
        assert isinstance(reader_id, int) and reader_id > 0
        assert writer_id != reader_id
        print(f"[01 precondition] resolved writer_id={writer_id} reader_id={reader_id}")

        # メール→ID解決結果がstate.jsonにキャッシュされていること（config.py/switcher.py仕様）。
        state = load_state(e2e_config.state_path)
        assert state.user_id_cache.get(e2e_writer_email) == writer_id
        assert state.user_id_cache.get(e2e_reader_email) == reader_id

    # -- 2. switch test-read ---------------------------------------------

    def test_02_switch_test_read(
        self, e2e_config: Config, master_client: BacklogClient, resolved_ids: dict[str, int]
    ) -> None:
        state = load_state(e2e_config.state_path)
        all_profiles = list(e2e_config.profiles.values())
        profile = e2e_config.get_profile("test-read")

        _lines, overall = _call_with_retry(
            lambda: switcher.switch([profile], all_profiles, e2e_config.default, None, master_client, state)
        )
        save_state(e2e_config.state_path, state)

        assert overall == "read"
        members = _members(master_client, ALLOWED_PROJECT)
        assert resolved_ids["reader"] in members, "readerがMY_PROJECTに参加していません"
        assert resolved_ids["writer"] not in members, "writerが参加したままです（read選択時は不参加のはず）"
        print(f"[02 switch test-read] members after switch: {sorted(members)}")

    # -- 3. switch test-write（CLI経由・排他動作確認） --------------------

    def test_03_switch_test_write_exclusive_via_cli(
        self,
        e2e_config: Config,
        master_client: BacklogClient,
        resolved_ids: dict[str, int],
    ) -> None:
        result = _run_cli(["switch", "test-write"], e2e_config.path)
        assert result.returncode == 0, (
            f"switch test-write がexit {result.returncode} で終了しました "
            f"(stderr末尾: {result.stderr[-300:] if result.stderr else ''})"
        )

        # stdout/stderr分離の確認: eval対象(export行)はstdoutのみに出て、stderrに"export "行は現れない。
        exported = _exported_var_names(result.stdout)
        assert exported == {"BACKLOG_API_KEY", "BACKLOG_SPACE", "BACKLOG_DOMAIN", "BACKLOG_PROJECT"}
        assert "export " not in result.stderr

        # 排他動作の実機確認: readerが除名され、writerが参加している。
        members = _members(master_client, ALLOWED_PROJECT)
        assert resolved_ids["reader"] not in members, "排他除名されたはずのreaderがまだ参加しています"
        assert resolved_ids["writer"] in members, "writerがMY_PROJECTに参加していません"
        print(f"[03 switch test-write / CLI] exported_vars={sorted(exported)} members={sorted(members)}")

    # -- 4. switch test-write 再実行（冪等性） -----------------------------

    def test_04_switch_test_write_idempotent(
        self, e2e_config: Config, master_client: BacklogClient, resolved_ids: dict[str, int]
    ) -> None:
        state = load_state(e2e_config.state_path)
        all_profiles = list(e2e_config.profiles.values())
        profile = e2e_config.get_profile("test-write")

        # 例外を送出せず完了すること自体が冪等性の確認。
        _lines, overall = _call_with_retry(
            lambda: switcher.switch([profile], all_profiles, e2e_config.default, None, master_client, state)
        )
        save_state(e2e_config.state_path, state)

        assert overall == "write"
        members = _members(master_client, ALLOWED_PROJECT)
        assert resolved_ids["writer"] in members
        print("[04 switch test-write again] re-run did not raise; writer still a member (idempotent)")

    # -- 5. switch test-admin ---------------------------------------------

    def test_05_switch_test_admin(
        self,
        e2e_config: Config,
        master_client: BacklogClient,
        resolved_ids: dict[str, int],
        request: pytest.FixtureRequest,
    ) -> None:
        state = load_state(e2e_config.state_path)
        all_profiles = list(e2e_config.profiles.values())
        profile = e2e_config.get_profile("test-admin")

        try:
            # このステップは直前の4ステップ(01〜04)で既にAPI呼び出しを重ねた後に実行されるため、
            # 60req/minのレート制限窓を確実にクリアできるよう長めのbackoffを使う。
            _lines, overall = _call_with_retry(
                lambda: switcher.switch([profile], all_profiles, e2e_config.default, None, master_client, state),
                max_attempts=2,
                backoff_seconds=65.0,
            )
        except BacklogApiError as exc:
            save_state(e2e_config.state_path, state)
            # code 13 (TooManyRequestsError) はレート制限であり「プラン制約」ではない。
            # _call_with_retry で吸収しきれなかった場合はテスト失敗として素直に扱う
            # （「プラン制約で検証不可」と誤って記録しないようにするため）。
            if exc.code == _CODE_TOO_MANY_REQUESTS:
                raise
            request.node.user_properties.append(("admin_result", f"plan_constrained code={exc.code}"))
            pytest.skip(
                f"プラン制約等によりプロジェクト管理者機能を検証できませんでした（code={exc.code}）。"
                "write権限の付与自体はresolve/exclusive-removeの経路で検証済み。"
            )
            return

        save_state(e2e_config.state_path, state)
        members = _members(master_client, ALLOWED_PROJECT)
        assert resolved_ids["writer"] in members

        admins = _admins(master_client, ALLOWED_PROJECT)
        if overall == "admin":
            assert resolved_ids["writer"] in admins, "overall=='admin'なのにadministrators一覧に写っていません"
            request.node.user_properties.append(("admin_result", "granted"))
            print("[05 switch test-admin] project administrator flag granted successfully")
        else:
            # E2E対象スペースではマスターがスペース管理者のため通常はここに来ない想定だが、
            # 実際に縮退した場合は記録して継続する（異常終了しない設計の実機確認にもなる）。
            assert overall == "write"
            request.node.user_properties.append(("admin_result", "degraded_to_write"))
            print("[05 switch test-admin] degraded to 'write' (code5 fallback triggered on this space)")

    # -- 6. status ----------------------------------------------------------

    def test_06_status(self, e2e_config: Config, master_client: BacklogClient, resolved_ids: dict[str, int]) -> None:
        state = load_state(e2e_config.state_path)
        all_profiles = list(e2e_config.profiles.values())

        text = _call_with_retry(lambda: switcher.get_status(all_profiles, e2e_config.default, master_client, state))
        save_state(e2e_config.state_path, state)
        print("[06 status]\n" + text)

        # 実際のプロジェクト参加状況（APIで直接確認）。
        members = _members(master_client, ALLOWED_PROJECT)
        admins = _admins(master_client, ALLOWED_PROJECT)
        if resolved_ids["writer"] in members:
            expected_actual = "admin" if resolved_ids["writer"] in admins else "write"
        elif resolved_ids["reader"] in members:
            expected_actual = "read"
        else:
            expected_actual = "-"

        data_rows = text.splitlines()[2:]  # ヘッダ行・区切り行を除く
        assert data_rows, "statusの出力にデータ行がありません"
        for row in data_rows:
            columns = row.split()
            # PROFILE PROJECT STATUS EXPIRES... の順（STATUSは3列目）。
            assert columns[1] == ALLOWED_PROJECT
            assert columns[2] == expected_actual, (
                f"status表示が実際の参加状況と一致しません（行: {row!r}、実際の状態: {expected_actual!r}）"
            )

    # -- 7. TTL（有効期限付き付与→enforce） ----------------------------------

    def test_07_ttl_duration_and_enforce(
        self, e2e_config: Config, master_client: BacklogClient, resolved_ids: dict[str, int]
    ) -> None:
        state = load_state(e2e_config.state_path)
        all_profiles = list(e2e_config.profiles.values())
        profile = e2e_config.get_profile("test-read")

        _lines, overall = _call_with_retry(
            lambda: switcher.switch([profile], all_profiles, e2e_config.default, "30s", master_client, state)
        )
        save_state(e2e_config.state_path, state)
        assert overall == "read"

        members = _members(master_client, ALLOWED_PROJECT)
        assert resolved_ids["reader"] in members, "readerが --duration 30s で参加していません"

        grant = next((g for g in state.grants if g.profile == "test-read"), None)
        assert grant is not None and grant.expires_at is not None
        print(f"[07 ttl] granted with --duration 30s (expires_at={grant.expires_at}); sleeping 31s...")

        # 実時間で30秒待機する（TTLの実挙動を実機で確認するため）。
        time.sleep(31)

        state = load_state(e2e_config.state_path)
        removed = _call_with_retry(
            lambda: switcher.enforce_expired(e2e_config.default, all_profiles, master_client, state)
        )
        save_state(e2e_config.state_path, state)

        assert removed == 1, f"enforce で解除された件数が想定と異なります（removed={removed}）"
        members_after = _members(master_client, ALLOWED_PROJECT)
        assert resolved_ids["reader"] not in members_after, "期限切れのはずのreaderがまだ参加しています"
        print(f"[07 ttl] enforce removed {removed} expired grant(s); reader is no longer a member")

    # -- 8. release（CLI経由・後片付けを兼ねる） -----------------------------

    def test_08_release_via_cli(
        self,
        e2e_config: Config,
        master_client: BacklogClient,
        resolved_ids: dict[str, int],
    ) -> None:
        result = _run_cli(["release"], e2e_config.path)
        assert result.returncode == 0, (
            f"release がexit {result.returncode} で終了しました "
            f"(stderr末尾: {result.stderr[-300:] if result.stderr else ''})"
        )

        # stdout/stderr分離の確認: unset行はstdoutのみ、確認メッセージはstderrのみ。
        unset_names = _unset_var_names(result.stdout)
        assert unset_names == {"BACKLOG_API_KEY", "BACKLOG_SPACE", "BACKLOG_DOMAIN", "BACKLOG_PROJECT"}
        assert "除名しました" in result.stderr
        assert "unset " not in result.stderr

        # 後片付け確認: 両仮想ユーザーがmembers・administrators双方から除名されていること。
        members = _members(master_client, ALLOWED_PROJECT)
        admins = _admins(master_client, ALLOWED_PROJECT)
        assert resolved_ids["writer"] not in members
        assert resolved_ids["reader"] not in members
        assert resolved_ids["writer"] not in admins
        assert resolved_ids["reader"] not in admins
        print("[08 release / CLI] both virtual users removed from MY_PROJECT (members & administrators)")
