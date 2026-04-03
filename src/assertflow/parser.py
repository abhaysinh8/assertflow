"""Safe YAML loading and environment selection."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from assertflow.errors import ConfigurationError, SuiteValidationError
from assertflow.models import SuiteConfig

YAML_SUFFIXES = {".yaml", ".yml"}


def _safe_mapping(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigurationError(f"cannot read {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise SuiteValidationError(f"invalid YAML in {path}: {exc}") from exc
    if raw is None:
        raise SuiteValidationError(f"{path} is empty")
    if not isinstance(raw, dict):
        raise SuiteValidationError(f"{path} must contain a YAML mapping at its root")
    return raw


def load_suite(path: Path) -> SuiteConfig:
    path = path.resolve()
    if path.suffix.lower() not in YAML_SUFFIXES:
        raise SuiteValidationError(f"suite must be a .yaml or .yml file: {path}")
    try:
        return SuiteConfig.model_validate(_safe_mapping(path))
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        raise SuiteValidationError(f"invalid suite {path}: {details}") from exc


def discover_suites(target: Path) -> list[Path]:
    target = target.resolve()
    if target.is_file():
        if target.suffix.lower() not in YAML_SUFFIXES:
            raise SuiteValidationError(f"suite must be a .yaml or .yml file: {target}")
        return [target]
    if not target.is_dir():
        raise SuiteValidationError(f"suite path does not exist: {target}")
    candidates = sorted(
        path
        for path in target.rglob("*")
        if path.is_file() and path.suffix.lower() in YAML_SUFFIXES
    )
    suites: list[Path] = []
    for path in candidates:
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            suites.append(path)
            continue
        if not isinstance(raw, dict) or "name" in raw or "steps" in raw:
            suites.append(path)
    if not suites:
        raise SuiteValidationError(f"no YAML suites found under {target}")
    return suites


def find_environment_file(suite_path: Path) -> Path | None:
    """Find the nearest conventional environments.yaml file."""
    for directory in (suite_path.parent, *suite_path.parents):
        candidate = directory / "environments.yaml"
        if candidate.is_file():
            return candidate
    return None


def load_environment(path: Path | None, name: str | None) -> dict[str, Any]:
    if name is None:
        return {}
    if path is None:
        raise ConfigurationError(
            f"environment {name!r} was selected but no --env-file was provided"
        )
    environments = _safe_mapping(path.resolve())
    selected = environments.get(name)
    if not isinstance(selected, dict):
        available = ", ".join(sorted(str(key) for key in environments)) or "none"
        raise ConfigurationError(
            f"environment {name!r} not found in {path}; available: {available}"
        )
    return dict(selected)
