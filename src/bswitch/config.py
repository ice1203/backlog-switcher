"""bswitch の設定ファイル（INI形式）読み込み・バリデーション。

configパスは環境変数 `BSWITCH_CONFIG` > `~/.config/backlog-switcher/config` の順で解決する。
"""

from __future__ import annotations

import configparser
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from bswitch.models import DEFAULT_MCP_URL, PERMISSIONS, DefaultConfig, Profile

#: 既定のconfigファイルパス。
DEFAULT_CONFIG_PATH = Path.home() / ".config" / "backlog-switcher" / "config"

#: config不在時にstderrへ表示するサンプル。
SAMPLE_CONFIG = """\
[default]
space = your-space.backlog.jp      ; スペースのホスト名
writer_user = svc-writer@example.com   ; write/admin用仮想ユーザー（メールアドレスまたは数値ID）
reader_user = svc-reader@example.com   ; read用仮想ユーザー（writer_userとどちらか一方は必須）
writer_browser_profile = Profile 1     ; writerで認可画面を開くChromeのプロファイル（MCPサーバーの認可に必須）
reader_browser_profile = Profile 2     ; readerで認可画面を開くChromeのプロファイル（同上）
mcp_url = http://localhost:3333/mcp    ; 任意。MCPサーバーの接続先（省略時はこの値）
default_duration = 8h       ; 任意。--duration未指定時の有効期限（未設定なら8h）

[profile customer-a]
project = CUSTOMER_A        ; Backlogプロジェクトキー
permission = read           ; read | write | admin

[profile customer-b]
project = CUSTOMER_B
permission = write
"""


class ConfigError(Exception):
    """configの内容が不正な場合の例外。"""


@dataclass
class Config:
    """読み込み済みのconfig全体（[default] + 全プロファイル）。"""

    path: Path
    default: DefaultConfig
    profiles: dict[str, Profile] = field(default_factory=dict)

    @property
    def state_path(self) -> Path:
        """state.jsonのパス（configファイルと同じディレクトリ）。"""
        return self.path.parent / "state.json"

    def get_profile(self, name: str) -> Profile:
        """プロファイル名からProfileを取得する。未定義なら ConfigError。"""
        try:
            return self.profiles[name]
        except KeyError as exc:
            raise ConfigError(f"プロファイル '{name}' は定義されていません") from exc

    def all_projects(self) -> list[str]:
        """config定義済みの全プロジェクトキー（重複除去・順序維持）。"""
        seen: dict[str, None] = {}
        for profile in self.profiles.values():
            seen.setdefault(profile.project, None)
        return list(seen.keys())


def get_config_path() -> Path:
    """configファイルのパスを決定する（BSWITCH_CONFIG > 既定パス）。"""
    override = os.environ.get("BSWITCH_CONFIG")
    if override:
        return Path(override).expanduser()
    return DEFAULT_CONFIG_PATH


def _require(section: configparser.SectionProxy, key: str, context: str) -> str:
    value = section.get(key)
    if value is None or value.strip() == "":
        raise ConfigError(f"{context}: 必須項目 '{key}' が設定されていません")
    return value.strip()


def _parse_default(parser: configparser.ConfigParser) -> DefaultConfig:
    if not parser.has_section("default"):
        raise ConfigError("[default] セクションが見つかりません")
    section = parser["default"]
    space = _require(section, "space", "[default]")

    def _optional(key: str) -> str | None:
        value = section.get(key)
        if value is None or value.strip() == "":
            return None
        return value.strip()

    # 無期限のキーの参照を config に残さないため、値が空でもエラーにする。
    for key in _REMOVED_API_KEY_REFS:
        if key in section:
            raise ConfigError(
                f"[default]: {key} は使えません。"
                f"仮想ユーザーは OAuth で認証するため、config から {key} の行を削除してください"
            )

    writer_user = _optional("writer_user")
    reader_user = _optional("reader_user")
    if writer_user is None and reader_user is None:
        raise ConfigError("[default]: writer_user または reader_user の少なくとも一方を設定してください")

    mcp_url = _optional("mcp_url") or DEFAULT_MCP_URL
    _validate_mcp_url(mcp_url)

    return DefaultConfig(
        space=space,
        writer_user=writer_user,
        reader_user=reader_user,
        default_duration=_optional("default_duration"),
        mcp_url=mcp_url,
        writer_browser_profile=_optional("writer_browser_profile"),
        reader_browser_profile=_optional("reader_browser_profile"),
    )


#: 仮想ユーザーの API キーの参照先。config に書かれていたらエラーにする。
_REMOVED_API_KEY_REFS: tuple[str, ...] = ("writer_api_key_ref", "reader_api_key_ref")


def _validate_mcp_url(url: str) -> None:
    """mcp_url のスキームが http / https で、ホストがあることを確かめる。"""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ConfigError(
            "[default]: mcp_url は http:// または https:// で始まり、ホストを含む URL にしてください"
            f"（指定値: '{url}'）"
        )


def _parse_profiles(parser: configparser.ConfigParser) -> dict[str, Profile]:
    profiles: dict[str, Profile] = {}
    for section_name in parser.sections():
        if not section_name.startswith("profile "):
            continue
        name = section_name[len("profile ") :].strip()
        if not name:
            raise ConfigError(f"[{section_name}] のプロファイル名が空です")
        if name in profiles:
            raise ConfigError(f"プロファイル名 '{name}' が重複しています")
        section = parser[section_name]
        project = _require(section, "project", f"[{section_name}]")
        permission = _require(section, "permission", f"[{section_name}]")
        if permission not in PERMISSIONS:
            raise ConfigError(
                f"[{section_name}]: permission は {'/'.join(PERMISSIONS)} のいずれかである必要があります"
                f"（指定値: '{permission}'）"
            )
        profiles[name] = Profile(name=name, project=project, permission=permission)
    return profiles


def load_config(path: Path | None = None) -> Config:
    """configファイルを読み込みバリデーションする。

    ファイル不在時はサンプルconfigをstderrへ表示して `sys.exit(1)` する
    （自動生成はしない仕様）。
    内容が不正な場合は ConfigError を送出する。
    """
    config_path = path if path is not None else get_config_path()
    if not config_path.is_file():
        print(f"設定ファイルが見つかりません: {config_path}", file=sys.stderr)
        print("以下の形式で作成してください:", file=sys.stderr)
        print(file=sys.stderr)
        print(SAMPLE_CONFIG, file=sys.stderr)
        sys.exit(1)

    parser = configparser.ConfigParser(inline_comment_prefixes=(";",))
    try:
        parser.read(config_path, encoding="utf-8")
    except configparser.Error as exc:
        raise ConfigError(
            f"設定ファイルを INI 形式として読めません: {config_path}（{exc.__class__.__name__}）"
        ) from exc

    default = _parse_default(parser)
    profiles = _parse_profiles(parser)

    return Config(path=config_path, default=default, profiles=profiles)
