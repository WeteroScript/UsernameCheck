"""Persistent storage paths shared by all bot modules.

All persistent JSON files live in ./data. Legacy root-level files and the
old ./sessions directory are migrated into ./data on first startup.
"""
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"


def _move_legacy_file(source: Path, target: Path) -> None:
    if not source.is_file() or source == target:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        shutil.move(str(source), str(target))
        return
    legacy_dir = DATA_DIR / "_legacy"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    backup = legacy_dir / source.name
    index = 1
    while backup.exists():
        backup = legacy_dir / f"{source.stem}.{index}{source.suffix}"
        index += 1
    shutil.move(str(source), str(backup))


def data_file(filename: str) -> str:
    """Return an absolute path inside data/, migrating legacy root files."""
    raw_path = Path(str(filename).strip())
    if raw_path.is_absolute():
        target = raw_path
    else:
        target = DATA_DIR / raw_path.name
        legacy = PROJECT_ROOT / raw_path.name
        _move_legacy_file(legacy, target)
    target.parent.mkdir(parents=True, exist_ok=True)
    return str(target)


def data_directory(name: str = "") -> str:
    """Return a directory inside data/, migrating any previous root directory."""
    target = DATA_DIR / name if name else DATA_DIR
    legacy = PROJECT_ROOT / name if name else None

    if legacy is not None and legacy.is_dir() and legacy != target:
        target.mkdir(parents=True, exist_ok=True)
        for source in list(legacy.iterdir()):
            destination = target / source.name
            if destination.exists():
                legacy_dir = target / "_legacy"
                legacy_dir.mkdir(parents=True, exist_ok=True)
                destination = legacy_dir / source.name
                index = 1
                while destination.exists():
                    destination = legacy_dir / f"{source.stem}.{index}{source.suffix}"
                    index += 1
            shutil.move(str(source), str(destination))
        try:
            legacy.rmdir()
        except OSError:
            pass

    target.mkdir(parents=True, exist_ok=True)
    return str(target)
