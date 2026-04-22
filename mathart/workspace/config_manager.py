"""Local configuration manager for dual-track distillation workflows.

This module intentionally keeps secrets outside version control while still
providing a friendly terminal-driven setup flow for local research distillation.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping


REQUIRED_GITIGNORE_ENTRIES = (".env", "config.local.json", "*.local.json")
ENV_KEY_ALIASES: dict[str, tuple[str, ...]] = {
    "api_key": ("API_KEY", "MATHART_API_KEY"),
    "base_url": ("BASE_URL", "MATHART_BASE_URL"),
    "model_name": ("MODEL_NAME", "MATHART_MODEL_NAME"),
}


class ConfigurationSafetyError(RuntimeError):
    """Raised when secret-bearing local config is not protected from Git."""


class MissingLocalAPIConfigurationError(RuntimeError):
    """Raised when local distillation config is required but absent."""


@dataclass(frozen=True)
class LocalAPIConfig:
    """Strongly-typed local API configuration."""

    api_key: str
    base_url: str
    model_name: str
    storage_path: str
    source: str

    def redacted(self) -> dict[str, str]:
        masked = self.api_key[:4] + "…" + self.api_key[-4:] if len(self.api_key) >= 8 else "***"
        return {
            "api_key": masked,
            "base_url": self.base_url,
            "model_name": self.model_name,
            "storage_path": self.storage_path,
            "source": self.source,
        }


class ConfigManager:
    """Manage local API credentials for the local distillation lane."""

    def __init__(
        self,
        project_root: str | Path | None = None,
        *,
        env_filename: str = ".env",
        json_filename: str = "config.local.json",
    ) -> None:
        self.project_root = Path(project_root or Path.cwd()).resolve()
        self.env_path = self.project_root / env_filename
        self.json_path = self.project_root / json_filename
        self.gitignore_path = self.project_root / ".gitignore"

    def ensure_gitignore_protection(self) -> bool:
        """Ensure local secret files are ignored by Git before any write occurs."""
        existing = ""
        if self.gitignore_path.exists():
            existing = self.gitignore_path.read_text(encoding="utf-8")
        lines = existing.splitlines()
        changed = False
        for entry in REQUIRED_GITIGNORE_ENTRIES:
            if entry not in lines:
                lines.append(entry)
                changed = True
        final_text = "\n".join(line for line in lines if line is not None).rstrip() + "\n"
        if changed or not self.gitignore_path.exists():
            self.gitignore_path.write_text(final_text, encoding="utf-8")
        self._assert_gitignore_protection()
        return changed

    def _assert_gitignore_protection(self) -> None:
        if not self.gitignore_path.exists():
            raise ConfigurationSafetyError(".gitignore is missing; refusing to store API credentials")
        lines = set(self.gitignore_path.read_text(encoding="utf-8").splitlines())
        missing = [entry for entry in REQUIRED_GITIGNORE_ENTRIES if entry not in lines]
        if missing:
            raise ConfigurationSafetyError(
                "Local API config is not fully protected by .gitignore: " + ", ".join(missing)
            )

    def load(self, env: Mapping[str, str] | None = None) -> LocalAPIConfig | None:
        """Load config from the process environment, .env, or config.local.json."""
        env_mapping = dict(os.environ if env is None else env)
        config = self._load_from_environment(env_mapping)
        if config is not None:
            return config
        config = self._load_from_env_file()
        if config is not None:
            return config
        return self._load_from_json_file()

    def has_config(self, env: Mapping[str, str] | None = None) -> bool:
        return self.load(env=env) is not None

    def ensure_local_api_config(
        self,
        *,
        interactive: bool | None = None,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], None] = print,
        env: Mapping[str, str] | None = None,
        storage: str = "env",
    ) -> LocalAPIConfig:
        """Load existing config or guide the user through a safe first-time setup."""
        self.ensure_gitignore_protection()
        loaded = self.load(env=env)
        if loaded is not None:
            return loaded

        if interactive is None:
            interactive = sys.stdin.isatty()
        if not interactive:
            raise MissingLocalAPIConfigurationError(
                "本地科研蒸馏缺少 API 配置。请先在交互终端运行向导并填写 API_KEY / BASE_URL / MODEL_NAME。"
            )

        output_fn("[mathart] 未检测到本地科研蒸馏配置，下面将引导写入仅本地保存的密钥文件。")
        api_key = self._prompt_required(input_fn, "API_KEY: ")
        base_url = self._prompt_required(input_fn, "BASE_URL: ")
        model_name = self._prompt_required(input_fn, "MODEL_NAME: ")
        config = LocalAPIConfig(
            api_key=api_key,
            base_url=base_url,
            model_name=model_name,
            storage_path=str(self.env_path if storage == "env" else self.json_path),
            source="wizard",
        )
        if storage == "json":
            self.save_json(config)
        else:
            self.save_env(config)
        output_fn(f"[mathart] 本地配置已安全写入 {config.storage_path}，并已确认受 .gitignore 保护。")
        return self.load(env=env) or config

    def save_env(self, config: LocalAPIConfig) -> Path:
        self.ensure_gitignore_protection()
        self.env_path.write_text(self._render_env(config), encoding="utf-8")
        self._assert_gitignore_protection()
        return self.env_path

    def save_json(self, config: LocalAPIConfig) -> Path:
        self.ensure_gitignore_protection()
        payload = {
            "API_KEY": config.api_key,
            "BASE_URL": config.base_url,
            "MODEL_NAME": config.model_name,
        }
        self.json_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self._assert_gitignore_protection()
        return self.json_path

    def describe_state(self, env: Mapping[str, str] | None = None) -> dict[str, object]:
        current = self.load(env=env)
        return {
            "project_root": str(self.project_root),
            "gitignore_path": str(self.gitignore_path),
            "env_path": str(self.env_path),
            "json_path": str(self.json_path),
            "gitignore_protected": self._is_gitignore_protected(),
            "config_present": current is not None,
            "config": None if current is None else current.redacted(),
        }

    def _is_gitignore_protected(self) -> bool:
        if not self.gitignore_path.exists():
            return False
        lines = set(self.gitignore_path.read_text(encoding="utf-8").splitlines())
        return all(entry in lines for entry in REQUIRED_GITIGNORE_ENTRIES)

    def _load_from_environment(self, env_mapping: Mapping[str, str]) -> LocalAPIConfig | None:
        resolved: dict[str, str] = {}
        for field, aliases in ENV_KEY_ALIASES.items():
            for alias in aliases:
                value = env_mapping.get(alias)
                if value:
                    resolved[field] = value.strip()
                    break
        if len(resolved) != 3:
            return None
        return LocalAPIConfig(
            api_key=resolved["api_key"],
            base_url=resolved["base_url"],
            model_name=resolved["model_name"],
            storage_path="<environment>",
            source="environment",
        )

    def _load_from_env_file(self) -> LocalAPIConfig | None:
        if not self.env_path.exists():
            return None
        values: dict[str, str] = {}
        for raw_line in self.env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, raw_value = line.split("=", 1)
            values[key.strip()] = self._decode_env_value(raw_value.strip())
        if not all(key in values and values[key] for key in ("API_KEY", "BASE_URL", "MODEL_NAME")):
            return None
        return LocalAPIConfig(
            api_key=values["API_KEY"],
            base_url=values["BASE_URL"],
            model_name=values["MODEL_NAME"],
            storage_path=str(self.env_path),
            source="env_file",
        )

    def _load_from_json_file(self) -> LocalAPIConfig | None:
        if not self.json_path.exists():
            return None
        payload = json.loads(self.json_path.read_text(encoding="utf-8"))
        if not all(payload.get(key) for key in ("API_KEY", "BASE_URL", "MODEL_NAME")):
            return None
        return LocalAPIConfig(
            api_key=str(payload["API_KEY"]).strip(),
            base_url=str(payload["BASE_URL"]).strip(),
            model_name=str(payload["MODEL_NAME"]).strip(),
            storage_path=str(self.json_path),
            source="json_file",
        )

    def _prompt_required(self, input_fn: Callable[[str], str], prompt: str) -> str:
        value = input_fn(prompt).strip()
        if not value:
            raise MissingLocalAPIConfigurationError(f"{prompt.strip()} 不能为空")
        return value

    def _render_env(self, config: LocalAPIConfig) -> str:
        return (
            "# Local-only MathArt API credentials. Never commit this file.\n"
            f"API_KEY={self._encode_env_value(config.api_key)}\n"
            f"BASE_URL={self._encode_env_value(config.base_url)}\n"
            f"MODEL_NAME={self._encode_env_value(config.model_name)}\n"
        )

    @staticmethod
    def _encode_env_value(value: str) -> str:
        if any(ch.isspace() for ch in value) or any(ch in value for ch in ('#', '"', "'")):
            return json.dumps(value, ensure_ascii=False)
        return value

    @staticmethod
    def _decode_env_value(value: str) -> str:
        text = value.strip()
        if not text:
            return text
        if text[0] in {'"', "'"}:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text.strip('"').strip("'")
        return text


__all__ = [
    "ConfigManager",
    "ConfigurationSafetyError",
    "LocalAPIConfig",
    "MissingLocalAPIConfigurationError",
    "REQUIRED_GITIGNORE_ENTRIES",
]
