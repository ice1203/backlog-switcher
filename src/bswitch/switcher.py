"""排他動作・TTL（有効期限）ロジック。

安全のための設計判断（docs/design.md「判断指針」の「安全 > シンプルさ」に基づく）:
  - `switch()` の排他除名は「前回grantsに記録されたプロジェクト」のみを対象にする。
    config定義済み全プロジェクトの走査はBacklogのレートリミットを
    浪費するため。state.json消失等で回収漏れが起きた場合は `release --all`（全件除名）で回復できる。
  - `switch()` は除名・参加のAPI操作が成功するたびにstate.jsonへ保存する（こまめな保存）。
    途中で失敗・中断しても「実際の参加状況」と「記録」のズレが最小になり、
    参加済みプロジェクトは次回switchの排他除名で自動回収できる。
    ファイル書き込みは何度やってもAPIレートリミットを消費しないため、保存回数は惜しまない。
  - 排他除名・release では project-user だけでなく project-administrator も除名する
    （API側の `delete_project_administrator` は対象不在エラー（code 6）のみ無視するため、
    「以前 admin だったユーザーの管理者フラグが除名後も残る」事故を防ぐ safety net として機能する。
    レートリミット等の一時的/致命的なエラーは BacklogApiError として呼び出し元に伝播する）。
  - 期限切れGrantの `project` が現在のconfigに存在しない場合（configから該当プロファイルが削除された場合）は
    実API操作をスキップし、state.jsonからの記録削除のみ行う
    （「config定義外のプロジェクトに操作しない」制約を厳守するため）。
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from bswitch.api import BacklogClient
from bswitch.keys import resolve_api_key
from bswitch.models import DefaultConfig, Grant, Profile, State
from bswitch.shell import make_export_lines, make_unset_lines

#: --duration / default_duration のパース対象パターン（例: 30s, 30m, 2h, 8h）。
_DURATION_PATTERN = re.compile(r"^(\d+)(s|m|h)$")
_UNIT_SECONDS: dict[str, int] = {"s": 1, "m": 60, "h": 3600}

#: switch()の戻り値（選択されたpermissionレベル）を決めるための優先順位。
_PERMISSION_RANK: dict[str, int] = {"read": 0, "write": 1, "admin": 2}


class SwitcherError(Exception):
    """switcher内のロジックエラー（バリデーション失敗・ユーザー解決失敗等）。APIキーは含めない。"""


def parse_duration(value: str) -> int:
    """ "30s"/"30m"/"2h"/"8h" 形式の期間文字列を秒数に変換する。

    不正な形式の場合は ValueError を送出する。
    """
    match = _DURATION_PATTERN.match(value.strip())
    if not match:
        raise ValueError(f"不正な期間形式です: '{value}'（例: 30s, 30m, 2h, 8h）")
    amount = int(match.group(1))
    unit = match.group(2)
    return amount * _UNIT_SECONDS[unit]


#: `--duration` も `default_duration` も未指定の場合に適用するプログラム既定値。
#: 「無期限」を既定にすると失効忘れの事故につながるため、安全側に倒す（判断指針「安全 > シンプルさ」）。
_FALLBACK_DURATION = "8h"


def _compute_expires_at(duration: str | None, default_duration: str | None) -> str | None:
    """expires_atを計算する（ISO8601・UTC）。

    優先順位: `--duration` > `default_duration` > プログラム既定値（8h）。
    """
    effective = duration if duration is not None else default_duration
    if effective is None:
        effective = _FALLBACK_DURATION
    seconds = parse_duration(effective)
    expires = datetime.now(UTC) + timedelta(seconds=seconds)
    return expires.isoformat()


def _is_expired(grant: Grant, now: datetime) -> bool:
    if grant.expires_at is None:
        return False
    try:
        expires = datetime.fromisoformat(grant.expires_at)
    except ValueError:
        # 壊れた記録は期限切れ扱いにして掃除する。
        return True
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    return expires <= now


def _all_projects(profiles: list[Profile]) -> list[str]:
    """profiles群から重複除去済みのプロジェクトキー一覧を返す（順序維持）。"""
    seen: dict[str, None] = {}
    for profile in profiles:
        seen.setdefault(profile.project, None)
    return list(seen.keys())


def _is_numeric_id(value: str) -> bool:
    return value.strip().isdigit()


def _find_user_id_by_email(users: list[dict[str, object]], email: str) -> int:
    for user in users:
        if user.get("mailAddress") == email:
            raw_id = user.get("id")
            if raw_id is None:
                continue
            return int(raw_id)  # type: ignore[arg-type]
    raise SwitcherError(
        f"ユーザー '{email}' を解決できませんでした（該当するメールアドレスのユーザーが見つかりません）"
    )


def resolve_user_ids(config: DefaultConfig, client: BacklogClient, state: State) -> tuple[int | None, int | None]:
    """writer_user / reader_user を数値IDに解決する（read-onlyな操作。副作用なし）。

    configで未設定のユーザーはNoneを返す。
    数値ID直接指定・キャッシュ済みの場合はAPIを呼ばない。
    いずれかが未解決の場合のみ `GET /api/v2/users` をまとめて1回だけ呼ぶ。
    解決結果は `state.user_id_cache` に書き込む（永続化は呼び出し側の責務）。
    """

    def _from_cache_or_numeric(ref: str | None) -> int | None:
        if ref is None:
            return None
        if _is_numeric_id(ref):
            return int(ref)
        return state.user_id_cache.get(ref)

    writer_id = _from_cache_or_numeric(config.writer_user)
    reader_id = _from_cache_or_numeric(config.reader_user)

    need_api = (config.writer_user is not None and writer_id is None) or (
        config.reader_user is not None and reader_id is None
    )
    if not need_api:
        return writer_id, reader_id

    users = client.get_users()
    if config.writer_user is not None and writer_id is None:
        writer_id = _find_user_id_by_email(users, config.writer_user)
        state.user_id_cache[config.writer_user] = writer_id
    if config.reader_user is not None and reader_id is None:
        reader_id = _find_user_id_by_email(users, config.reader_user)
        state.user_id_cache[config.reader_user] = reader_id
    return writer_id, reader_id


def _add_user_idempotent(client: BacklogClient, project: str, user_id: int) -> None:
    """プロジェクトへユーザーを参加させる（既に参加済みなら何もしない＝冪等）。

    追加失敗（権限エラー含む）は api.py の例外がそのまま伝播する。
    ユーザーが明示的に選択したプロジェクトへの追加失敗を隠さないため。
    """
    existing = {u.get("id") for u in client.get_project_users(project)}
    if user_id in existing:
        return
    client.add_project_user(project, user_id)


def _ensure_project_administrator(client: BacklogClient, project: str, user_id: int) -> bool:
    """プロジェクト管理者に設定する（既に管理者なら何もしない＝冪等）。

    戻り値: 設定できた（または既に設定済みだった）場合 True。
    権限エラー（errors[].code == 5）でスキップされた場合 False。
    """
    existing = {a.get("id") for a in client.get_project_administrators(project)}
    if user_id in existing:
        return True
    result = client.add_project_administrator(project, user_id)
    return result is not None


def _exclusive_remove(client: BacklogClient, projects: list[str], writer_id: int | None, reader_id: int | None) -> None:
    """指定されたプロジェクト群から両仮想ユーザー（プロジェクトユーザー・プロジェクト管理者）を除名する。

    writer_id / reader_id が None の場合はその側の除名をスキップする。
    api.py の delete系メソッドは「対象が元々参加/管理者でない」（code 6: NoResourceError）と
    「マスターユーザーに除名権限がない」（code 5: UnauthorizedOperationError）の場合のみ
    エラーを無視して None を返すため、その意味でのみ冪等。レートリミット・認証エラー等は
    BacklogApiError として送出され、この関数の呼び出し元に伝播する。
    """
    for project in projects:
        if writer_id is not None:
            client.delete_project_user(project, writer_id)
            client.delete_project_administrator(project, writer_id)
        if reader_id is not None:
            client.delete_project_user(project, reader_id)
            client.delete_project_administrator(project, reader_id)


def switch(
    profiles: list[Profile],
    all_profiles: list[Profile],
    config: DefaultConfig,
    duration: str | None,
    client: BacklogClient,
    state: State,
    save: Callable[[], None] | None = None,
) -> tuple[list[str], str]:
    """仮想ユーザーを対象プロファイルのプロジェクトへ排他的に参加させる。

    Args:
        profiles: 対象として選択されたプロファイル（1件以上）。
        all_profiles: config定義済みの全プロファイル（config定義外プロジェクトの操作除外に使用）。
        config: `[default]` セクション（space・仮想ユーザー・APIキー参照）。
        duration: `--duration` の値（未指定なら None）。
        client: マスターユーザーのAPIキーで初期化された BacklogClient。
        state: 現在の状態（このオブジェクトを直接変更する）。
        save: stateを永続化するコールバック。除名・参加のAPI操作が成功するたびに呼ばれる
            （Noneなら途中保存しない。最終保存は呼び出し側の責務）。

    Returns:
        (stdout出力行リスト, 選択された全体permissionレベル "read"/"write"/"admin")

    Raises:
        SwitcherError: 引数が空、read と write/admin の混在、ユーザー解決失敗など。
        ValueError: `--duration`/`default_duration` の形式が不正な場合。
        BacklogApiError: API呼び出しが失敗した場合（冪等性で無視されるもの以外）。
    """
    if not profiles:
        raise SwitcherError("switch対象のプロファイルが指定されていません")

    # 1. permission検証（read と write/admin の混在は不可。write + admin は許可）
    permissions = {p.permission for p in profiles}
    if "read" in permissions and (permissions - {"read"}):
        raise SwitcherError("read権限とwrite/admin権限を同時に選択することはできません（write + admin の混在は可）")

    # 1b. 選択されたpermissionに必要なユーザーが設定されているか検証
    if "read" in permissions and config.reader_user is None:
        raise SwitcherError("read権限のプロファイルが選択されましたが、reader_user が設定されていません")
    if permissions & {"write", "admin"} and config.writer_user is None:
        raise SwitcherError("write/admin権限のプロファイルが選択されましたが、writer_user が設定されていません")

    # 2. --duration / default_duration の形式検証（破壊的操作の前にfail fastする）
    expires_at = _compute_expires_at(duration, config.default_duration)

    # 3. メール→ID解決（read-only）
    writer_id, reader_id = resolve_user_ids(config, client, state)

    def _save() -> None:
        if save is not None:
            save()

    # 4. 継続判定: 選択プロファイルと同一プロジェクト・同一仮想ユーザーのgrantが既にあれば
    #    「除名→再参加」のサイクル（更新系5コール）を丸ごとスキップできる。
    #    同一プロファイルの再実行（新しいターミナルでの再switch・期限更新）を0コールにするため。
    continuing: dict[str, Grant] = {}
    for profile in profiles:
        # step 1bで該当ユーザーの存在を検証済み
        uid: int = reader_id if profile.permission == "read" else writer_id  # type: ignore[assignment]
        for g in state.grants:
            if g.project == profile.project and g.user_id == uid:
                continuing[profile.project] = g

    # 5. 排他除名: 前回grantsに含まれるプロジェクトのみ（継続プロジェクトは除く。
    #    config定義済み全プロジェクトの走査はレートリミット枠（更新系150回/時）を浪費するため）。
    #    1プロジェクト分の除名が完了するたびに記録を削除して保存し、
    #    途中失敗しても「実際の参加状況」と「state.json」のズレを最小化する。
    known = set(_all_projects(all_profiles))
    for prev_project in list(dict.fromkeys(g.project for g in state.grants)):
        if prev_project in continuing:
            continue  # 今回も同一ユーザーで参加継続するため除名しない（記録は参加フェーズで更新）
        if prev_project in known:
            _exclusive_remove(client, [prev_project], writer_id, reader_id)
        else:
            # config定義外のプロジェクトには操作しない（docs/design.md「禁止事項」）。記録の削除のみ行う。
            print(
                f"警告: {prev_project} は現在のconfigに存在しないため、"
                "除名（API操作）をスキップしました（state.jsonの記録のみ削除します）",
                file=sys.stderr,
            )
        state.grants = [g for g in state.grants if g.project != prev_project]
        _save()

    # 6. 対象プロファイルのプロジェクトに参加させる（継続プロジェクトは参加API省略）。
    #    参加が成功するたびにGrantを記録・保存する（後続の管理者付与・APIキー解決が
    #    失敗しても、参加済みの事実が記録に残り次回switchの除名で回収できる）。
    new_grants: list[Grant] = []
    effective_permissions: list[str] = []

    for profile in profiles:
        # step 1bで該当ユーザーの存在を検証済み
        granted_user_id: int = reader_id if profile.permission == "read" else writer_id  # type: ignore[assignment]
        prev = continuing.get(profile.project)
        if prev is None:
            _add_user_idempotent(client, profile.project, granted_user_id)
        elif prev.permission == "admin" and profile.permission != "admin":
            # admin→write/read切替（同一ユーザー継続）: 残っている管理者フラグのみ外す。
            # 失敗時はprevの記録（admin）が残るため、次回switchで再試行される。
            client.delete_project_administrator(profile.project, granted_user_id)
        grant = Grant(
            profile=profile.name,
            project=profile.project,
            user_id=granted_user_id,
            # adminは管理者付与前だがadminとして先に記録する（付与後・記録前に落ちても
            # 除名側は admin として掃除されるため安全側。失敗したら直後にwriteへ訂正する）。
            permission=profile.permission,
            expires_at=expires_at,
        )
        if prev is not None:
            state.grants = [g for g in state.grants if g is not prev]
        state.grants.append(grant)
        _save()

        effective = profile.permission
        if profile.permission == "admin":
            if prev is not None and prev.permission == "admin":
                pass  # 前回付与済みの管理者権限を継続（API呼び出しなし）
            else:
                ok = _ensure_project_administrator(client, profile.project, granted_user_id)
                if not ok:
                    print(
                        f"警告: {profile.project} でプロジェクト管理者権限を付与できませんでした。"
                        "write権限として継続します",
                        file=sys.stderr,
                    )
                    effective = "write"
                    grant.permission = "write"
                    write_profile = next(
                        (p for p in all_profiles if p.project == profile.project and p.permission == "write"),
                        None,
                    )
                    if write_profile is not None:
                        grant.profile = write_profile.name
                    _save()

        effective_permissions.append(effective)
        new_grants.append(grant)

    # 7. Grant記録の正規化（除名フェーズで旧記録は削除済みのため、今回付与分のみが残る。
    #    最終的な永続化は呼び出し側の save_state が行う）
    state.grants = new_grants

    # 7. stdout出力行の生成
    overall = max(effective_permissions, key=lambda p: _PERMISSION_RANK[p])
    api_key = resolve_api_key(overall, config)

    project_for_output: str | None
    if len(profiles) == 1:
        project_for_output = profiles[0].project
    else:
        project_for_output = None
        print("複数プロジェクトのため BACKLOG_PROJECT は未設定です", file=sys.stderr)

    lines = make_export_lines(api_key, config.space, project_for_output)
    return lines, overall


def release(
    config: DefaultConfig,
    all_profiles: list[Profile],
    client: BacklogClient,
    state: State,
    save: Callable[[], None] | None = None,
    *,
    all_projects: bool = False,
) -> list[str]:
    """両仮想ユーザーを除名し、grants記録を空にする。

    既定ではstate.jsonのgrantsに記録されたプロジェクトのみを対象にする
    （付与記録が0件ならAPIを一切呼ばず、レートリミットを消費しない）。
    `all_projects=True` はconfig定義済みの全プロジェクトをgrantsに関係なく走査する回復経路で、
    state.json消失等でgrants由来の除名から漏れた残留参加を回収できる。
    いずれのモードも1プロジェクト分の除名が完了するたびに該当grantを記録から削除して
    保存し、途中失敗しても記録と実状態のズレを最小化する。

    Args:
        all_projects: Trueならconfig定義済みの全プロジェクトを走査する（`release --all`）。
        save: stateを永続化するコールバック（除名の進行に応じて呼ばれる。Noneなら途中保存しない）。

    Returns:
        stdout出力行リスト（unset行）。
    """
    if not all_projects and not state.grants:
        return make_unset_lines()

    writer_id, reader_id = resolve_user_ids(config, client, state)
    known = set(_all_projects(all_profiles))
    if all_projects:
        targets = _all_projects(all_profiles)
    else:
        targets = list(dict.fromkeys(g.project for g in state.grants))

    for project in targets:
        if project in known:
            _exclusive_remove(client, [project], writer_id, reader_id)
        else:
            # config定義外のプロジェクトには操作しない（docs/design.md「禁止事項」）。記録の削除のみ行う。
            print(
                f"警告: {project} は現在のconfigに存在しないため、"
                "除名（API操作）をスキップしました（state.jsonの記録のみ削除します）",
                file=sys.stderr,
            )
        remaining = [g for g in state.grants if g.project != project]
        if len(remaining) != len(state.grants):
            state.grants = remaining
            if save is not None:
                save()
    # 全走査モードで残ったconfig定義外プロジェクトのgrant記録も含めて最終的に空にする（API操作はしない）。
    state.grants = []
    return make_unset_lines()


def get_status(
    all_profiles: list[Profile],
    config: DefaultConfig,
    client: BacklogClient,
    state: State,
) -> str:
    """各プロファイルの参加状況・期限を表形式文字列で返す（stderr出力用）。"""
    if not all_profiles:
        return "プロファイルが定義されていません"

    writer_id, reader_id = resolve_user_ids(config, client, state)
    grants_by_profile = {g.profile: g for g in state.grants}

    members_cache: dict[str, set[int]] = {}
    admins_cache: dict[str, set[int]] = {}

    def members(project: str) -> set[int]:
        if project not in members_cache:
            members_cache[project] = {
                int(u["id"]) for u in client.get_project_users(project) if u.get("id") is not None
            }
        return members_cache[project]

    def admins(project: str) -> set[int]:
        if project not in admins_cache:
            admins_cache[project] = {
                int(a["id"]) for a in client.get_project_administrators(project) if a.get("id") is not None
            }
        return admins_cache[project]

    rows: list[tuple[str, str, str, str]] = []
    for profile in all_profiles:
        current_members = members(profile.project)
        if writer_id is not None and writer_id in current_members:
            actual = "admin" if writer_id in admins(profile.project) else "write"
        elif reader_id is not None and reader_id in current_members:
            actual = "read"
        else:
            actual = "-"

        grant = grants_by_profile.get(profile.name)
        if grant is None:
            expires_display = "-"
        elif grant.expires_at is None:
            expires_display = "無期限"
        else:
            expires_display = datetime.fromisoformat(grant.expires_at).astimezone().isoformat()

        rows.append((profile.name, profile.project, actual, expires_display))

    headers = ("PROFILE", "PROJECT", "STATUS", "EXPIRES")
    columns = [headers, *rows]
    widths = [max(len(row[i]) for row in columns) for i in range(4)]

    lines = [
        "  ".join(headers[i].ljust(widths[i]) for i in range(4)),
        "  ".join("-" * widths[i] for i in range(4)),
    ]
    lines.extend("  ".join(row[i].ljust(widths[i]) for i in range(4)) for row in rows)
    return "\n".join(lines)


def enforce_expired(
    config: DefaultConfig,
    all_profiles: list[Profile],
    client: BacklogClient,
    state: State,
    save: Callable[[], None] | None = None,
) -> int:
    """期限切れGrantを除名する（定期実行・遅延強制の両方から呼ばれる）。

    `config` は現状このロジック自体には使わないが、
    シグネチャの一貫性（release/get_statusと同型）のため受け取る。

    1件の除名が完了するたびに記録を削除して保存し、途中失敗しても記録と実状態のズレを
    最小化する。

    Args:
        save: stateを永続化するコールバック（除名の進行に応じて呼ばれる。Noneなら途中保存しない）。

    Returns:
        除名した件数。
    """
    del config  # 現状未使用（シグネチャの一貫性のため受け取る）
    known_projects = set(_all_projects(all_profiles))
    now = datetime.now(UTC)
    removed = 0

    for grant in list(state.grants):
        if not _is_expired(grant, now):
            continue

        if grant.project in known_projects:
            client.delete_project_user(grant.project, grant.user_id)
            if grant.permission == "admin":
                client.delete_project_administrator(grant.project, grant.user_id)
        else:
            # config定義外のプロジェクトには操作しない（docs/design.md「禁止事項」）。記録の削除のみ行う。
            print(
                f"警告: {grant.project} は現在のconfigに存在しないため、"
                "期限切れ付与の自動解除（API操作）をスキップしました（state.jsonの記録のみ削除します）",
                file=sys.stderr,
            )
        state.grants = [g for g in state.grants if g is not grant]
        if save is not None:
            save()
        removed += 1

    return removed
