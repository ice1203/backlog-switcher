"""排他動作・TTL（有効期限）ロジック。

安全のための設計判断（brief 11.「判断指針」の「安全 > シンプルさ」に基づく）:
  - `switch()` は brief の関数シグネチャに加えて `all_profiles` を受け取る。
    排他除名（config定義済みの**全**プロジェクトからの除名）には選択されたプロファイルだけでなく
    config全体のプロジェクト一覧が必要なため。
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
#: 「無期限」を既定にすると失効忘れの事故につながるため、安全側に倒す（brief 11.「安全 > シンプルさ」）。
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


def resolve_user_ids(config: DefaultConfig, client: BacklogClient, state: State) -> tuple[int, int]:
    """writer_user / reader_user を数値IDに解決する（read-onlyな操作。副作用なし）。

    数値ID直接指定・キャッシュ済みの場合はAPIを呼ばない。
    いずれかが未解決の場合のみ `GET /api/v2/users` をまとめて1回だけ呼ぶ。
    解決結果は `state.user_id_cache` に書き込む（永続化は呼び出し側の責務）。
    """

    def _from_cache_or_numeric(ref: str) -> int | None:
        if _is_numeric_id(ref):
            return int(ref)
        return state.user_id_cache.get(ref)

    writer_id = _from_cache_or_numeric(config.writer_user)
    reader_id = _from_cache_or_numeric(config.reader_user)
    if writer_id is not None and reader_id is not None:
        return writer_id, reader_id

    users = client.get_users()
    if writer_id is None:
        writer_id = _find_user_id_by_email(users, config.writer_user)
        state.user_id_cache[config.writer_user] = writer_id
    if reader_id is None:
        reader_id = _find_user_id_by_email(users, config.reader_user)
        state.user_id_cache[config.reader_user] = reader_id
    return writer_id, reader_id


def _add_user_idempotent(client: BacklogClient, project: str, user_id: int) -> None:
    """プロジェクトへユーザーを参加させる（既に参加済みなら何もしない＝冪等）。"""
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


def _exclusive_remove(client: BacklogClient, projects: list[str], writer_id: int, reader_id: int) -> None:
    """config定義済みの全プロジェクトから両仮想ユーザー（プロジェクトユーザー・プロジェクト管理者）を除名する。

    api.py の delete系メソッドは「対象が元々参加/管理者でない」場合（errors[].code == 6:
    NoResourceError）のみエラーを無視して None を返すため、その意味でのみ冪等
    （未参加ユーザーの除名・非管理者の管理者除名を含む）。レートリミット・認証エラー・
    権限エラー等は BacklogApiError として送出され、この関数の呼び出し元に伝播する。
    """
    for project in projects:
        client.delete_project_user(project, writer_id)
        client.delete_project_user(project, reader_id)
        client.delete_project_administrator(project, writer_id)
        client.delete_project_administrator(project, reader_id)


def switch(
    profiles: list[Profile],
    all_profiles: list[Profile],
    config: DefaultConfig,
    duration: str | None,
    client: BacklogClient,
    state: State,
) -> tuple[list[str], str]:
    """仮想ユーザーを対象プロファイルのプロジェクトへ排他的に参加させる。

    Args:
        profiles: 対象として選択されたプロファイル（1件以上）。
        all_profiles: config定義済みの全プロファイル（排他除名の対象プロジェクト算出に使用）。
        config: `[default]` セクション（space・仮想ユーザー・APIキー参照）。
        duration: `--duration` の値（未指定なら None）。
        client: マスターユーザーのAPIキーで初期化された BacklogClient。
        state: 現在の状態（このオブジェクトを直接変更する）。

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

    # 2. --duration / default_duration の形式検証（破壊的操作の前にfail fastする）
    expires_at = _compute_expires_at(duration, config.default_duration)

    # 3. メール→ID解決（read-only）
    writer_id, reader_id = resolve_user_ids(config, client, state)

    # 4. 排他除名: config定義済みの全プロジェクトから両仮想ユーザーを除名
    _exclusive_remove(client, _all_projects(all_profiles), writer_id, reader_id)

    # 5. 対象プロファイルのプロジェクトに参加させる
    new_grants: list[Grant] = []
    effective_permissions: list[str] = []

    for profile in profiles:
        if profile.permission == "read":
            _add_user_idempotent(client, profile.project, reader_id)
            effective = "read"
            granted_user_id = reader_id
        else:
            _add_user_idempotent(client, profile.project, writer_id)
            effective = profile.permission
            granted_user_id = writer_id
            if profile.permission == "admin":
                ok = _ensure_project_administrator(client, profile.project, writer_id)
                if not ok:
                    print(
                        f"警告: {profile.project} でプロジェクト管理者権限を付与できませんでした。"
                        "write権限として継続します",
                        file=sys.stderr,
                    )
                    effective = "write"

        effective_permissions.append(effective)
        new_grants.append(
            Grant(
                profile=profile.name,
                project=profile.project,
                user_id=granted_user_id,
                permission=effective,
                expires_at=expires_at,
            )
        )

    # 6. Grant記録（排他除名により以前のgrantsは全て無効化されているため丸ごと置き換える）
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
) -> list[str]:
    """config定義済みの全プロジェクトから両仮想ユーザーを除名する。

    Returns:
        stdout出力行リスト（unset行）。
    """
    writer_id, reader_id = resolve_user_ids(config, client, state)
    _exclusive_remove(client, _all_projects(all_profiles), writer_id, reader_id)
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
        if writer_id in current_members:
            actual = "admin" if writer_id in admins(profile.project) else "write"
        elif reader_id in current_members:
            actual = "read"
        else:
            actual = "-"

        grant = grants_by_profile.get(profile.name)
        if grant is None:
            expires_display = "-"
        elif grant.expires_at is None:
            expires_display = "無期限"
        else:
            expires_display = grant.expires_at

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
) -> int:
    """期限切れGrantを除名する（定期実行・遅延強制の両方から呼ばれる）。

    `config` は現状このロジック自体には使わないが、
    シグネチャの一貫性（release/get_statusと同型）のため受け取る。

    Returns:
        除名した件数。
    """
    del config  # 現状未使用（シグネチャの一貫性のため受け取る）
    known_projects = set(_all_projects(all_profiles))
    now = datetime.now(UTC)
    remaining: list[Grant] = []
    removed = 0

    for grant in state.grants:
        if not _is_expired(grant, now):
            remaining.append(grant)
            continue

        if grant.project in known_projects:
            client.delete_project_user(grant.project, grant.user_id)
            if grant.permission == "admin":
                client.delete_project_administrator(grant.project, grant.user_id)
        else:
            # config定義外のプロジェクトには操作しない（brief制約）。記録の削除のみ行う。
            print(
                f"警告: {grant.project} は現在のconfigに存在しないため、"
                "期限切れ付与の自動解除（API操作）をスキップしました（state.jsonの記録のみ削除します）",
                file=sys.stderr,
            )
        removed += 1

    state.grants = remaining
    return removed
