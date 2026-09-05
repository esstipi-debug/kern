"""Tests for the single state root (src/data_paths.py).

A self-serve client runs `docker run` against their own data. Before this module
that meant setting eight separate env vars, one per store, and `fly.toml` proves
how that goes: it re-roots eight of them by hand and misses the ninth
(`LINCHPIN_SCHEDULER_PATH`), whose jobstore therefore lands on the ephemeral
rootfs that PR #188 fixed for the other five.

The contract here is deliberately conservative: with `KERN_DATA_DIR` unset,
every path resolves exactly where it does today, so neither local development
nor the live Fly deploy changes behaviour.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.data_paths import (
    STATE_PATHS,
    data_root,
    resolve_state_path,
)

REPO = Path(__file__).resolve().parent.parent
FLY_TOML = REPO / "fly.toml"


# -- the root itself --------------------------------------------------------


def test_data_root_defaults_to_the_repo_data_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset means today's behaviour: `data/`, relative to the repo root."""
    monkeypatch.delenv("KERN_DATA_DIR", raising=False)
    assert data_root() == REPO / "data"


def test_data_root_honours_kern_data_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERN_DATA_DIR", "/srv/kern")
    assert data_root() == Path("/srv/kern")


def test_data_root_ignores_a_blank_kern_data_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    """An env var set to whitespace is the same as absent, not a path named ' '."""
    monkeypatch.setenv("KERN_DATA_DIR", "   ")
    assert data_root() == REPO / "data"


# -- per-store resolution ---------------------------------------------------


@pytest.mark.parametrize("name", sorted(STATE_PATHS))
def test_every_state_path_lands_under_the_root_when_it_is_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str
) -> None:
    """The whole point: one variable moves all nine, with none left behind."""
    monkeypatch.setenv("KERN_DATA_DIR", str(tmp_path))
    for spec in STATE_PATHS.values():
        monkeypatch.delenv(spec.env, raising=False)
    resolved = Path(resolve_state_path(name))
    assert tmp_path in resolved.parents or resolved.parent == tmp_path, (
        f"{name} resolved to {resolved}, outside KERN_DATA_DIR={tmp_path}"
    )


@pytest.mark.parametrize("name", sorted(STATE_PATHS))
def test_the_legacy_var_still_wins_over_the_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str
) -> None:
    """Fly sets eight of these explicitly. The root must never override them."""
    spec = STATE_PATHS[name]
    monkeypatch.setenv("KERN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv(spec.env, "/explicit/wins")
    assert Path(resolve_state_path(name)) == Path("/explicit/wins")


@pytest.mark.parametrize("name", sorted(STATE_PATHS))
def test_nothing_moves_when_the_root_is_unset(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """Zero behaviour change is the whole safety argument for this refactor."""
    spec = STATE_PATHS[name]
    monkeypatch.delenv("KERN_DATA_DIR", raising=False)
    monkeypatch.delenv(spec.env, raising=False)
    assert Path(resolve_state_path(name)) == Path(spec.legacy_default)


# -- the guard the packaging spec asks for ----------------------------------


def test_the_root_reproduces_what_fly_toml_sets_by_hand(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`KERN_DATA_DIR=/data` must equal the eight lines fly.toml maintains by hand.

    This is the strongest available evidence that the root is not a new,
    parallel layout: it is the one production already runs, expressed once.
    Any drift - a store added here with a different name than prod uses, or a
    prod value edited without updating the registry - fails here.
    """
    fly = FLY_TOML.read_text(encoding="utf-8")
    declared: dict[str, str] = {}
    for line in fly.splitlines():
        line = line.strip()
        if line.startswith("LINCHPIN_") and "=" in line:
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"')
            if value.startswith("/data"):
                declared[key] = value
    assert declared, "fly.toml no longer declares any /data path - registry is unverifiable"

    monkeypatch.setenv("KERN_DATA_DIR", "/data")
    by_env = {spec.env: name for name, spec in STATE_PATHS.items()}
    for env_var, prod_value in sorted(declared.items()):
        assert env_var in by_env, (
            f"fly.toml roots {env_var} at {prod_value} but the registry does not know it"
        )
        monkeypatch.delenv(env_var, raising=False)
        assert Path(resolve_state_path(by_env[env_var])) == Path(prod_value), (
            f"{env_var}: root gives {resolve_state_path(by_env[env_var])}, "
            f"prod uses {prod_value}"
        )


def test_the_scheduler_jobstore_is_in_the_registry() -> None:
    """The store fly.toml forgot.

    `LINCHPIN_SCHEDULER_PATH` is the one durable store PR #188 did not re-root,
    so its jobstore defaults onto the container's ephemeral rootfs. It does not
    bite production today - the cron endpoint calls the synchronous
    `run_once()`, never `build_scheduler()` - but it bites the first self-serve
    client who runs the background scheduler. Being in the registry is what
    fixes it for them without a ninth hand-written line.
    """
    assert "scheduler" in STATE_PATHS
    assert STATE_PATHS["scheduler"].env == "LINCHPIN_SCHEDULER_PATH"


def test_no_state_module_hardcodes_its_own_data_default() -> None:
    """A new store that ignores the root must fail here, not in a client's volume.

    This is the test the packaging spec asks for ("un test que falle si una ruta
    nueva no lo respeta"). It reads the modules that own state paths and fails on
    a literal `"data/..."` default, which is how every one of these was written
    before the registry existed.
    """
    owners = sorted({spec.module for spec in STATE_PATHS.values()})
    offenders: list[str] = []
    for rel in owners:
        path = REPO / rel
        if not path.exists():
            offenders.append(f"{rel} (missing - registry points at a module that is gone)")
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if '"data/' in stripped or "'data/" in stripped:
                offenders.append(f"{rel}:{lineno}  {stripped[:90]}")
    assert not offenders, (
        "these state modules still hardcode a data/ default instead of deriving it "
        "from src.data_paths:\n  " + "\n  ".join(offenders)
    )


def test_registry_env_vars_are_unique() -> None:
    """Two stores sharing one env var would silently collide in a client's volume."""
    envs = [spec.env for spec in STATE_PATHS.values()]
    assert len(envs) == len(set(envs)), f"duplicate env var in the registry: {envs}"


def test_kern_data_dir_is_not_read_outside_the_registry() -> None:
    """One reader, so the contract cannot drift into a second interpretation."""
    import subprocess

    tracked = subprocess.run(
        ["git", "ls-files", "*.py"], capture_output=True, text=True, cwd=REPO
    ).stdout.split()
    readers = []
    for rel in tracked:
        if rel.startswith("tests/") or rel == "src/data_paths.py":
            continue
        try:
            body = (REPO / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "KERN_DATA_DIR" in body:
            readers.append(rel)
    assert not readers, (
        "KERN_DATA_DIR must be read only by src/data_paths.py; also read in: "
        + ", ".join(readers)
    )
