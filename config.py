from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


APP_NAME = "btmastodon"


@dataclass(frozen=True)
class ClientCredentials:
    client_id: str
    client_secret: str


@dataclass(frozen=True)
class AccountConfig:
    instance: str
    access_token: str
    client: ClientCredentials
    show_toot_numbers: bool = True
    show_toot_usernames: bool = True
    play_new_toot_sound: bool = True
    play_notification_sound: bool = True


def config_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / APP_NAME

    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config_home:
        return Path(xdg_config_home) / APP_NAME

    return Path.home() / ".config" / APP_NAME


def config_path() -> Path:
    return config_dir() / "config.json"


def load_config(path: Path | None = None) -> AccountConfig:
    path = path or config_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError("Not logged in. Run: btmastodon login <instance>") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Config file is not valid JSON: {path}") from exc

    try:
        client = ClientCredentials(
            client_id=raw["client"]["client_id"],
            client_secret=raw["client"]["client_secret"],
        )
        return AccountConfig(
            instance=raw["instance"],
            access_token=raw["access_token"],
            client=client,
            show_toot_numbers=bool(raw.get("show_toot_numbers", True)),
            show_toot_usernames=bool(
                raw.get("show_toot_usernames", raw.get("show_toot_accounts", True))
            ),
            play_new_toot_sound=bool(raw.get("play_new_toot_sound", True)),
            play_notification_sound=bool(raw.get("play_notification_sound", True)),
        )
    except KeyError as exc:
        raise ConfigError(f"Config file is missing required key: {exc}") from exc


def save_config(config: AccountConfig, path: Path | None = None) -> Path:
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {
        "instance": config.instance,
        "access_token": config.access_token,
        "client": {
            "client_id": config.client.client_id,
            "client_secret": config.client.client_secret,
        },
        "show_toot_numbers": config.show_toot_numbers,
        "show_toot_usernames": config.show_toot_usernames,
        "play_new_toot_sound": config.play_new_toot_sound,
        "play_notification_sound": config.play_notification_sound,
    }
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


class ConfigError(RuntimeError):
    pass
