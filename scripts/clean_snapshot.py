from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path

from paper_review_lib import HarnessError, find_root, load_config, resolve_repo_path


def copy_item(root: Path, relative: str, destination: Path) -> bool:
    source = resolve_repo_path(root, relative)
    if not source.exists():
        return False
    target = destination / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, target, dirs_exist_ok=True)
    else:
        shutil.copy2(source, target)
    return True


def create_snapshot(root: Path, destination: Path | None = None) -> Path:
    config = load_config(root)
    if destination is None:
        destination = Path(tempfile.mkdtemp(prefix="paper-review-fresh-"))
    else:
        destination = destination.resolve()
        if destination.exists() and any(destination.iterdir()):
            raise HarnessError(f"Snapshot destination is not empty: {destination}")
        destination.mkdir(parents=True, exist_ok=True)
    included = list(config["manuscript_roots"])
    included.extend(config.get("figure_roots", []))
    included.extend(config.get("bibliography_files", []))
    copied: set[str] = set()
    for relative in included:
        if relative in copied:
            continue
        copy_item(root, relative, destination)
        copied.add(relative)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a review-history-free manuscript snapshot")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        root = find_root(args.root)
        snapshot = create_snapshot(root, args.output)
    except HarnessError as exc:
        print(f"ERROR: {exc}")
        return 1
    print(snapshot)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
