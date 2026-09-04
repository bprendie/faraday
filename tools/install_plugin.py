#!/usr/bin/python3
"""Copy a curated, symlink-free plugin tree; preserve an existing copy as backup."""
import argparse
from pathlib import Path
import shutil
import tempfile

CONTENTS = ("manifest.json", "Panel.qml", "CageIcon.qml", "Model.js", "LICENSE",
            "README.md", "faraday.png", "install.sh", "uninstall.sh", "backend", "packaging",
            "tools", "tests", "docs", "assets")
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.qmlc")


def copy_plugin(source, destination):
    source = source.resolve()
    if destination == source:
        return
    backup = destination.parent.parent / "faraday-backups" / (destination.name + ".previous")
    if backup.exists() or backup.is_symlink():
        raise RuntimeError(f"Move the previous backup before upgrading: {backup}")
    if destination.exists() or destination.is_symlink():
        if destination.is_symlink():
            if destination.resolve() != source:
                raise RuntimeError("Refusing to replace a link to another checkout")
        elif not (destination / ".faraday-installed").is_file():
            raise RuntimeError("Refusing to overwrite an unmanaged plugin checkout")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".faraday-stage-", dir=destination.parent) as staging:
        stage = Path(staging) / "plugin"
        stage.mkdir()
        for name in CONTENTS:
            item = source / name
            if item.is_symlink() or (item.is_dir() and any(p.is_symlink() for p in item.rglob("*"))):
                raise RuntimeError(f"Plugin contents cannot contain symlinks: {item}")
            if item.is_dir():
                shutil.copytree(item, stage / name, ignore=IGNORE)
            else:
                shutil.copy2(item, stage / name)
        (stage / ".faraday-installed").write_text("Managed copy created by Faraday install.sh\n")
        if destination.exists() or destination.is_symlink():
            backup.parent.mkdir(parents=True, exist_ok=True)
            destination.rename(backup)
        try:
            stage.rename(destination)
        except OSError:
            if backup.exists() or backup.is_symlink():
                backup.rename(destination)
            raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    copy_plugin(args.source, args.destination)
