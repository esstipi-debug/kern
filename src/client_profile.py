"""Per-client parameter profile — durable, client-wide cost/capacity defaults.

Closes the gap between a one-off ``params`` override and the engine's generic
hardcoded defaults (0.95 service level, 0.25 holding rate, $75 order cost, ...):
an analyst records a client's real numbers once via :func:`upsert_profile`, and
every later run for that client reuses them through :func:`merge_params`.

Not a connector to a client's own system of record — see ``src/writeback.py``
for that. This is Linchpin's own local operating data about a client, so it
carries none of the risk-tier/approval ceremony writeback does.

Resolution priority (highest first), enforced by the caller via
``merge_params``: explicit per-call ``params`` > this profile > the engine's
own hardcoded default.
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
import time
import unicodedata
from dataclasses import asdict, dataclass, replace
from pathlib import Path

DEFAULT_CLIENTS_ROOT = Path("clients")

SCHEMA_VERSION = 1

# Provenance of a profile's numbers. Free text is rejected so a future
# "operator-confirmed vs machine-inferred" branch can trust the label.
VALID_SOURCES = frozenset({"manual", "elicited", "csv_inferred"})

# Placeholder labels that anonymous/unlabeled callers collapse to
# (JobRequest.client's own default and the webapp's blank-field fallback; the
# MCP surface's default client_label). They carry no real tenant identity, so a
# profile keyed on them would be shared by every unrelated caller — see
# is_generic_client_label().
GENERIC_CLIENT_LABEL = "Client"
_GENERIC_SLUGS = frozenset({"client", "mcp-client"})

_SLUG_COLLAPSE_RE = re.compile(r"[^a-z0-9]+")

# upsert_profile is last-writer-wins across processes (no cross-process lock);
# concurrent upserts for the same client can drop each other's fields. Fine for
# the single-operator workflow this serves — revisit before multi-writer use.


def slugify_client_id(name: str) -> str:
    """Stable, filesystem-safe key derived from a display name.

    Accents transliterate instead of vanishing ("Café" -> "cafe",
    "Ñandú SA" -> "nandu-sa") so distinct Spanish client names don't collide.
    Raises ``ValueError`` if nothing alphanumeric survives (all-punctuation or
    non-Latin-script names) — callers treat that as "this label has no profile".
    """
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    slug = _SLUG_COLLAPSE_RE.sub("-", ascii_name.strip().lower()).strip("-")
    if not slug:
        raise ValueError(f"client name yields an empty slug: {name!r}")
    return slug


def is_generic_client_label(name: str) -> bool:
    """Whether ``name`` slugifies to a placeholder label (case/whitespace-insensitive).

    A trust-boundary check, not cosmetics: these labels carry no real tenant
    identity, so they must never resolve to (or persist) a shared profile.
    """
    try:
        return slugify_client_id(name) in _GENERIC_SLUGS
    except ValueError:
        return False


def _require_positive_finite(value: float | None, field_name: str) -> None:
    if value is not None and not (math.isfinite(value) and value > 0):
        raise ValueError(f"{field_name} must be a finite number > 0")


@dataclass(frozen=True)
class WarehouseCapacity:
    """A physical storage limit. Stored for now; not yet enforced as a hard
    constraint by the optimization engine (see CAPABILITY_EXPANSION_PLAN for
    the capacitated-allocation follow-up) — ``unit`` is free-form ("m3",
    "pallets", "sku_slots") because it is not yet interpreted by any model.
    """

    value: float
    unit: str

    def __post_init__(self) -> None:
        _require_positive_finite(self.value, "warehouse_capacity.value")
        if not self.unit.strip():
            raise ValueError("warehouse_capacity.unit is required")


@dataclass(frozen=True)
class ClientProfile:
    """Durable per-client parameters, loaded once and reused across runs."""

    client_id: str
    display_name: str
    schema_version: int = SCHEMA_VERSION
    currency: str | None = None
    service_level: float | None = None
    holding_rate: float | None = None
    order_cost: float | None = None
    lead_time_days: float | None = None
    warehouse_capacity: WarehouseCapacity | None = None
    source: str = "manual"
    updated_at: str | None = None  # ISO date; caller-supplied (module stays pure)

    def __post_init__(self) -> None:
        if self.service_level is not None and not 0 < self.service_level < 1:
            raise ValueError("service_level must be in (0, 1)")
        _require_positive_finite(self.holding_rate, "holding_rate")
        _require_positive_finite(self.order_cost, "order_cost")
        _require_positive_finite(self.lead_time_days, "lead_time_days")
        if self.source not in VALID_SOURCES:
            raise ValueError(f"source must be one of {sorted(VALID_SOURCES)}, got {self.source!r}")

    def as_params(self) -> dict:
        """Non-None engine params, keyed exactly as the tools already read them.

        ``lead_time_days`` acts as the default lead time when a client's CSV
        carries none (per-SKU CSV values still win). ``warehouse_capacity`` is
        deliberately NOT included: no engine consumes it yet, and a dataclass
        object inside ``params`` would poison any JSON-serialization of the
        merged dict — it stays profile-only reference data until the
        capacitated-allocation capability lands.
        """
        fields = ("service_level", "holding_rate", "order_cost", "lead_time_days")
        return {k: v for k in fields if (v := getattr(self, k)) is not None}


def _profile_path(client_id: str, root: Path) -> Path:
    return root / client_id / "profile.json"


def load_profile(client_id: str, *, root: Path | str = DEFAULT_CLIENTS_ROOT) -> ClientProfile | None:
    """Load a client's profile, or ``None`` if it has never been recorded.

    Always ``None`` for the generic placeholder labels/slugs — regardless of
    whether a file happens to exist there — so an anonymous/unlabeled caller
    can never inherit another (unrelated) profile's real numbers.
    """
    if is_generic_client_label(client_id):
        return None
    path = _profile_path(client_id, Path(root))
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        stored_version = raw.get("schema_version", 1)
        if stored_version > SCHEMA_VERSION:
            raise ValueError(
                f"profile schema v{stored_version} is newer than this code understands "
                f"(v{SCHEMA_VERSION}) — refusing to reinterpret or silently downgrade it"
            )
        capacity_raw = raw.get("warehouse_capacity")
        return ClientProfile(
            client_id=raw["client_id"],
            display_name=raw.get("display_name", raw["client_id"]),
            schema_version=stored_version,
            currency=raw.get("currency"),
            service_level=raw.get("service_level"),
            holding_rate=raw.get("holding_rate"),
            order_cost=raw.get("order_cost"),
            lead_time_days=raw.get("lead_time_days"),
            warehouse_capacity=WarehouseCapacity(**capacity_raw) if capacity_raw else None,
            source=raw.get("source", "manual"),
            updated_at=raw.get("updated_at"),
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"corrupt client profile at {path}: {exc}") from exc


def save_profile(profile: ClientProfile, *, root: Path | str = DEFAULT_CLIENTS_ROOT) -> Path:
    """Persist a profile to ``<root>/<client_id>/profile.json``. Returns the path written.

    ``client_id`` must be canonical (its own slug): that both guarantees the
    orchestrator's slugified lookup will find the file again, and makes a path
    escape (separators, '..', absolute prefixes) impossible. Written atomically
    (temp file + ``os.replace``) so a crash mid-write can never corrupt the
    previous, still-valid profile — this is hand-answered operator data with no
    other source to regenerate it from.
    """
    if is_generic_client_label(profile.client_id):
        raise ValueError(
            "refusing to save a profile under a generic placeholder client label "
            f"({GENERIC_CLIENT_LABEL!r} etc.) — it has no real tenant identity and would "
            "be shared by every unlabeled caller; use a specific client identifier"
        )
    if profile.client_id != slugify_client_id(profile.client_id):
        raise ValueError(
            f"client_id must be a canonical slug (got {profile.client_id!r}, expected "
            f"{slugify_client_id(profile.client_id)!r}) — the orchestrator looks profiles "
            "up by slug, so anything else would save under a key that is never loaded"
        )
    path = _profile_path(profile.client_id, Path(root))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".profile-", suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(asdict(profile), indent=2, sort_keys=True, allow_nan=False))
        # Windows: os.replace over a file a concurrent reader holds open raises a
        # transient PermissionError (no FILE_SHARE_DELETE) — retry briefly.
        for attempt in range(3):
            try:
                os.replace(tmp_name, path)
                break
            except PermissionError:
                if attempt == 2:
                    raise
                time.sleep(0.05 * (attempt + 1))
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return path


_UNSET = object()  # sentinel: "caller did not pass this" (None is a legal stored value)


def upsert_profile(
    client_id: str,
    display_name: str,
    *,
    root: Path | str = DEFAULT_CLIENTS_ROOT,
    source: str | object = _UNSET,
    updated_at: str | None | object = _UNSET,
    **fields: object,
) -> ClientProfile:
    """Load-or-create a client's profile, apply field updates, validate, and persist.

    ``client_id`` is canonicalized (slugified) first, so passing the display
    name directly ("Acme Corp") lands on the same file the orchestrator will
    look up ("acme-corp"). Fields not passed — including ``source`` and
    ``updated_at`` — are preserved from the existing profile rather than reset:
    an analyst answering one missing question never has to re-supply (or
    accidentally destroy) anything already on record.
    """
    client_id = slugify_client_id(client_id)
    existing = load_profile(client_id, root=root)
    base = existing or ClientProfile(client_id=client_id, display_name=display_name)
    meta: dict[str, object] = {}
    if source is not _UNSET:
        meta["source"] = source
    if updated_at is not _UNSET:
        meta["updated_at"] = updated_at
    updated = replace(base, display_name=display_name, **meta, **fields)
    save_profile(updated, root=root)
    return updated


def merge_params(params: dict, profile: ClientProfile | None) -> dict:
    """Client profile fills gaps only — any key already in ``params`` always wins."""
    if profile is None:
        return dict(params)
    return {**profile.as_params(), **params}
