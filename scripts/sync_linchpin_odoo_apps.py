"""Sync kern_dry_run into the Odoo Apps Store git layout.

The Apps Store reads a dedicated repository whose *branch name* is the Odoo
series (``17.0``, ``19.0``) and whose root contains the module directory
``kern_dry_run/``. That repo is ``esstipi-debug/linchpin-odoo-apps`` -- not
this one. Giving the Store (or the GitHub user ``online-odoo``) access to
``kern`` would expose the private product; the dedicated repo exists so the
Store only ever sees the addon.

From the kern repo root:

    python scripts/sync_linchpin_odoo_apps.py
    python scripts/sync_linchpin_odoo_apps.py --push

``--push`` requires ``esstipi-debug/linchpin-odoo-apps`` to already exist
(empty is fine). This environment's GitHub token is scoped to ``kern`` and
cannot create a second repo. Create it once with your own GitHub login:

    gh repo create esstipi-debug/linchpin-odoo-apps --private --disable-issues --disable-wiki

Do not register ``kern#17.0`` / ``kern#19.0`` with apps.odoo.com.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SERIES = ("17.0", "19.0")
_MODULE = "kern_dry_run"
_DEFAULT_STORE_DIR = _REPO.parent / "linchpin-odoo-apps"
_DEFAULT_REMOTE = "https://github.com/esstipi-debug/linchpin-odoo-apps.git"
_SKIP_DIR_NAMES = {"__pycache__", ".git"}
_SKIP_FILE_SUFFIXES = {".pyc", ".pyo"}

_STORE_README = """# linchpin-odoo-apps

Private mirror of Kern's Odoo Apps Store module. The Apps Store clones this
repo; the branch name **is** the Odoo series.

| Branch | Module at repo root | Source in `esstipi-debug/kern` |
|---|---|---|
| `17.0` | `kern_dry_run/` | `odoo_addon/17.0/kern_dry_run/` |
| `19.0` | `kern_dry_run/` | `odoo_addon/19.0/kern_dry_run/` |

Same technical name on both series on purpose: the Store treats them as
one app across versions only if the directory name matches.

Regenerate from the main repo (do not edit this tree by hand):

    python scripts/sync_linchpin_odoo_apps.py --push

Listing: **Kern Inventory AI**, OPL-1, Store price free (no `price` key).
A broken manifest unpublishes every module in this repository.
"""

_STORE_GITIGNORE = """__pycache__/
*.py[cod]
.DS_Store
"""


def stage_series(src_repo: Path, dest_root: Path, series: str) -> Path:
    """Copy ``odoo_addon/<series>/kern_dry_run`` onto ``dest_root/kern_dry_run``.

    ``dest_root`` is the working tree of one Store branch (the module lives at
    the root, not under ``odoo_addon/``). Returns the module destination.
    """
    src = src_repo / "odoo_addon" / series / _MODULE
    if not (src / "__manifest__.py").is_file():
        raise FileNotFoundError(f"missing addon at {src}")
    dest = dest_root / _MODULE
    if dest.exists():
        shutil.rmtree(dest)
    dest_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest, ignore=_ignore_store_copy)
    return dest


def _ignore_store_copy(directory: str, names: list[str]) -> set[str]:
    skipped: set[str] = set()
    for name in names:
        path = Path(directory) / name
        if name in _SKIP_DIR_NAMES and path.is_dir():
            skipped.add(name)
        elif path.suffix in _SKIP_FILE_SUFFIXES:
            skipped.add(name)
    return skipped


def write_store_root_files(dest_root: Path) -> None:
    (dest_root / "README.md").write_text(_STORE_README, encoding="utf-8")
    (dest_root / ".gitignore").write_text(_STORE_GITIGNORE, encoding="utf-8")


def _git(cwd: Path, *args: str, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        text=True,
        capture_output=capture,
    )


def _has_commits(cwd: Path) -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def ensure_store_repo(store_dir: Path, remote: str) -> None:
    if (store_dir / ".git").exists():
        return
    store_dir.mkdir(parents=True, exist_ok=True)
    _git(store_dir, "init")
    _git(store_dir, "config", "user.email", "cursoragent@cursor.com")
    _git(store_dir, "config", "user.name", "Cursor Agent")
    existing = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=store_dir,
        capture_output=True,
        text=True,
    )
    if existing.returncode != 0:
        _git(store_dir, "remote", "add", "origin", remote)


def commit_series(store_dir: Path, src_repo: Path, series: str) -> None:
    """Build (or reset) branch ``series`` so its root is ``kern_dry_run/``."""
    ensure_store_repo(store_dir, _DEFAULT_REMOTE)
    # Detach from whatever is checked out so we can orphan without carrying files.
    if _has_commits(store_dir):
        # A local branch of this name may already exist; start from it so we
        # can rewrite the tree, otherwise start an orphan.
        exists = subprocess.run(
            ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{series}"],
            cwd=store_dir,
        )
        if exists.returncode == 0:
            _git(store_dir, "checkout", series)
        else:
            _git(store_dir, "checkout", "--orphan", series)
    else:
        _git(store_dir, "checkout", "--orphan", series)

    # Clear the index + worktree except .git, then restage the series.
    for child in store_dir.iterdir():
        if child.name == ".git":
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()

    stage_series(src_repo, store_dir, series)
    write_store_root_files(store_dir)
    _git(store_dir, "add", "-A")
    status = _git(store_dir, "status", "--porcelain", capture=True)
    if not status.stdout.strip():
        print(f"{series}: already up to date")
        return
    _git(
        store_dir,
        "commit",
        "-m",
        f"sync kern_dry_run {series} from esstipi-debug/kern",
    )
    print(f"{series}: committed")


def remote_exists(remote: str) -> bool:
    result = subprocess.run(
        ["git", "ls-remote", remote, "HEAD"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def push_series(store_dir: Path, series: str, remote: str) -> None:
    _git(store_dir, "push", "-u", remote, f"{series}:{series}")
    print(f"{series}: pushed to {remote}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--store-dir",
        type=Path,
        default=Path(os.environ.get("LINCHPIN_ODOO_APPS_DIR", str(_DEFAULT_STORE_DIR))),
        help="local clone of linchpin-odoo-apps (default: sibling directory)",
    )
    parser.add_argument(
        "--remote",
        default=_DEFAULT_REMOTE,
        help="git remote URL to push (default: esstipi-debug/linchpin-odoo-apps)",
    )
    parser.add_argument(
        "--push",
        action="store_true",
        help="git push each series branch to --remote",
    )
    parser.add_argument(
        "--series",
        choices=_SERIES,
        action="append",
        help="limit to one series (repeatable). Default: both.",
    )
    args = parser.parse_args(argv)
    series_list = tuple(args.series) if args.series else _SERIES

    for series in series_list:
        commit_series(args.store_dir, _REPO, series)

    if not args.push:
        print(f"staged in {args.store_dir} (no push)")
        return 0

    if not remote_exists(args.remote):
        print(
            f"remote not reachable: {args.remote}\n"
            "Create the private repo with YOUR GitHub login (this agent cannot):\n"
            "  gh repo create esstipi-debug/linchpin-odoo-apps "
            "--private --disable-issues --disable-wiki\n"
            "Then re-run: python scripts/sync_linchpin_odoo_apps.py --push\n"
            "Do not register esstipi-debug/kern with apps.odoo.com -- that "
            "would expose the private product to online-odoo.",
            file=sys.stderr,
        )
        return 2

    for series in series_list:
        push_series(args.store_dir, series, args.remote)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
