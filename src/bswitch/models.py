"""bswitch のデータモデル定義。"""

from __future__ import annotations

from dataclasses import dataclass, field

#: 有効な権限レベル。
PERMISSIONS: tuple[str, ...] = ("read", "write", "admin")


@dataclass(frozen=True)
class Profile:
    """configの `[profile <name>]` セクションから構築されるプロファイル。"""

    name: str
    project: str
    permission: str  # "read" | "write" | "admin"


@dataclass(frozen=True)
class DefaultConfig:
    """configの `[default]` セクションから構築される既定設定。"""

    space: str
    writer_user: str | None = None
    reader_user: str | None = None
    writer_api_key_ref: str | None = None
    reader_api_key_ref: str | None = None
    default_duration: str | None = None


@dataclass
class Grant:
    """state.json内の1件の付与記録。"""

    profile: str
    project: str
    user_id: int
    permission: str  # "read" | "write" | "admin"
    expires_at: str | None = None  # ISO8601 or None（無期限）
    key_fingerprint: str | None = None  # sha256(key[:5]+key[-5:]) の hex。APIキーそのものは含まない

    def to_dict(self) -> dict[str, object]:
        return {
            "profile": self.profile,
            "project": self.project,
            "user_id": self.user_id,
            "permission": self.permission,
            "expires_at": self.expires_at,
            "key_fingerprint": self.key_fingerprint,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Grant:
        return cls(
            profile=str(data["profile"]),
            project=str(data["project"]),
            user_id=int(data["user_id"]),  # type: ignore[arg-type]
            permission=str(data["permission"]),
            expires_at=(str(data["expires_at"]) if data.get("expires_at") is not None else None),
            key_fingerprint=(str(data["key_fingerprint"]) if data.get("key_fingerprint") is not None else None),
        )


@dataclass
class State:
    """state.json全体を表すデータモデル。APIキーの値は含めない（key_fingerprintはハッシュのみ）。"""

    grants: list[Grant] = field(default_factory=list)
    user_id_cache: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "grants": [g.to_dict() for g in self.grants],
            "user_id_cache": dict(self.user_id_cache),
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> State:
        grants_raw = data.get("grants", [])
        cache_raw = data.get("user_id_cache", {})
        grants = [Grant.from_dict(g) for g in grants_raw]  # type: ignore[union-attr]
        cache = {str(k): int(v) for k, v in cache_raw.items()}  # type: ignore[union-attr]
        return cls(grants=grants, user_id_cache=cache)
