from __future__ import annotations

from pathlib import Path

from ...utils.paths import data_dir, config_dir

A_MEMORIX_SYSTEM_ID = "a_memorix"


def package_root() -> Path:
    return Path(__file__).resolve().parent


def src_root() -> Path:
    return package_root().parent


def repo_root() -> Path:
    """A_Memorix 相对路径的基准目录，即 FxBot 的数据根目录。"""
    return data_dir()


def config_path() -> Path:
    return config_dir("amemorix") / "config.json"


def default_data_dir() -> Path:
    return data_dir("a-memorix")


def artifacts_root() -> Path:
    return default_data_dir() / "artifacts"


def web_root() -> Path:
    return package_root() / "web"


def resolve_repo_path(raw_path: str | Path | None, *, fallback: Path | None = None) -> Path:
    if raw_path is None:
        return (fallback or default_data_dir()).resolve()

    raw_value = str(raw_path).strip()
    if not raw_value:
        return (fallback or default_data_dir()).resolve()

    candidate = Path(raw_value).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()

    return (repo_root() / candidate).resolve()
