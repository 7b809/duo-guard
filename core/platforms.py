# core/platforms.py
"""
Typed access to the platform auto-refresh list.

Sources, in priority order:
  1. runtime_config_collection in MongoDB (toggles from the bot)
  2. PLATFORMS env var / legacy UPSTOX_* env vars (from core/config.py)

The env-supplied list defines WHICH platforms exist. The runtime
config defines the CURRENT flag values for each. This way the bot can
only toggle platforms the operator declared in .env.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from core.config import settings
from core.runtime_config import get_runtime_config

log = logging.getLogger("duo-guard.platforms")


@dataclass(frozen=True)
class PlatformConfig:
    name: str
    auto_refresh: bool = True
    on_boot: bool = True


def _dedupe(platforms: list[PlatformConfig]) -> list[PlatformConfig]:
    seen: set[str] = set()
    out: list[PlatformConfig] = []
    for p in platforms:
        if p.name in seen:
            log.warning(
                "Duplicate platform entry '%s' in PLATFORMS — ignoring "
                "the later one.",
                p.name,
            )
            continue
        seen.add(p.name)
        out.append(p)
    return out


def _load_env_platforms() -> list[PlatformConfig]:
    """Build the base list from settings.PLATFORMS (env defaults)."""
    out: list[PlatformConfig] = []
    for entry in settings.PLATFORMS:
        name = str(entry.get("name", "")).strip().lower()
        if not name:
            continue
        out.append(
            PlatformConfig(
                name=name,
                auto_refresh=bool(entry.get("auto_refresh", True)),
                on_boot=bool(entry.get("on_boot", True)),
            )
        )
    return _dedupe(out)


# Base list — defines which platforms exist. Never changes at runtime.
_ENV_PLATFORMS: list[PlatformConfig] = _load_env_platforms()


def _apply_runtime_overrides(
    base: PlatformConfig,
) -> PlatformConfig:
    """Return `base` merged with whatever the runtime config says."""
    cfg = get_runtime_config()
    override = (cfg.get("platforms") or {}).get(base.name) or {}
    return PlatformConfig(
        name=base.name,
        auto_refresh=bool(override.get("auto_refresh", base.auto_refresh)),
        on_boot=bool(override.get("on_boot", base.on_boot)),
    )


def all_platforms() -> list[PlatformConfig]:
    """Every declared platform, with runtime overrides applied."""
    return [_apply_runtime_overrides(p) for p in _ENV_PLATFORMS]


def get_platform(name: str) -> PlatformConfig | None:
    name = str(name).strip().lower()
    for p in _ENV_PLATFORMS:
        if p.name == name:
            return _apply_runtime_overrides(p)
    return None


def platforms_for_boot() -> list[PlatformConfig]:
    """
    Platforms whose tokens should be refreshed on app startup.

    Filtered by `on_boot` ONLY (after runtime override).
    """
    return [p for p in all_platforms() if p.on_boot]


def platforms_for_scheduled() -> list[PlatformConfig]:
    """
    Platforms that should have a daily scheduler job.

    Filtered by `auto_refresh` ONLY (after runtime override).
    """
    return [p for p in all_platforms() if p.auto_refresh]


def known_platform_names() -> list[str]:
    """Every platform declared in .env, regardless of runtime state."""
    return [p.name for p in _ENV_PLATFORMS]