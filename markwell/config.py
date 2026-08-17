"""Application configuration: load/save config.json and derive workspace paths.

JSON keys stay UPPER_SNAKE (matching the historic ``app/config.json`` format)
so existing config files keep loading. Unknown keys are preserved verbatim so
a load/save round-trip never silently drops user data.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import paths

# Python attribute name -> JSON key
_FIELD_KEYS = {
    "notion_api_key": "NOTION_API_KEY",
    "notion_database_id": "NOTION_DATABASE_ID",
    "drive_folder_name": "DRIVE_FOLDER_NAME",
    "ocr_lang": "OCR_LANG",
    "enable_drive": "ENABLE_DRIVE",
    "enable_notion": "ENABLE_NOTION",
    "input_dir": "INPUT_DIR",
    "processed_dir": "PROCESSED_DIR",
    "output_dir": "OUTPUT_DIR",
}


def _resolve_workspace_path(value: str, default_subdir: str) -> Path:
    """Resolve a config-supplied directory override.

    Empty -> the default subdirectory under the workspace root. A relative
    value is resolved against ``paths.default_workspace_dir()`` rather than
    the process CWD, which is unpredictable depending on how the app was
    launched. An absolute value is returned untouched.
    """
    if not value:
        return paths.default_workspace_dir() / default_subdir
    p = Path(value)
    if p.is_absolute():
        return p
    return paths.default_workspace_dir() / p


@dataclass
class AppConfig:
    notion_api_key: str = ""
    notion_database_id: str = ""
    drive_folder_name: str = "mdconversion"
    ocr_lang: str = "eng+kor"
    enable_drive: bool = True
    enable_notion: bool = True
    input_dir: str = ""  # "" -> derive from paths.default_workspace_dir()/"input_files"
    processed_dir: str = ""  # "" -> .../"processed_files"
    output_dir: str = ""  # "" -> .../"output_files"
    # Unknown JSON keys seen on load (e.g. the removed OCR_ENGINE), kept so
    # save_config() doesn't drop them. Not part of the public field table.
    extra: dict = field(default_factory=dict, repr=False, compare=False)

    def resolved_input_dir(self) -> Path:
        return _resolve_workspace_path(self.input_dir, "input_files")

    def resolved_processed_dir(self) -> Path:
        return _resolve_workspace_path(self.processed_dir, "processed_files")

    def resolved_output_dir(self) -> Path:
        return _resolve_workspace_path(self.output_dir, "output_files")

    @property
    def notion_configured(self) -> bool:
        return bool(self.enable_notion and self.notion_api_key and self.notion_database_id)

    @property
    def drive_configured(self) -> bool:
        return bool(self.enable_drive and credentials_path().exists())


def credentials_path() -> Path:
    return paths.user_data_dir() / "credentials.json"


def token_path() -> Path:
    return paths.user_data_dir() / "token.json"


def history_path() -> Path:
    return paths.user_data_dir() / "history.json"


def config_path() -> Path:
    return paths.user_data_dir() / "config.json"


def load_config() -> AppConfig:
    path = config_path()
    if not path.exists():
        return AppConfig()

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        # A corrupt or unreadable config must never block startup -- the GUI
        # shows defaults and the user can re-enter settings.
        return AppConfig()

    if not isinstance(raw, dict):
        return AppConfig()

    known_json_keys = set(_FIELD_KEYS.values())
    kwargs = {
        py_name: raw[json_key]
        for py_name, json_key in _FIELD_KEYS.items()
        if json_key in raw
    }
    extra = {k: v for k, v in raw.items() if k not in known_json_keys}
    return AppConfig(**kwargs, extra=extra)


def save_config(cfg: AppConfig) -> None:
    data = dict(cfg.extra)  # unknown keys first, so known fields always win on conflict
    for py_name, json_key in _FIELD_KEYS.items():
        data[json_key] = getattr(cfg, py_name)

    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
