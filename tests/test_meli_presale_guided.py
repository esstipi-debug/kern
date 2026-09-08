"""GuidedOutcome wrapper -- skipped when nosilo_core is not installed."""

from __future__ import annotations

import pytest

pytest.importorskip("nosilo_core")

from nosilo_core.guided import HANDOFF, OPTIONS, verify_guided  # noqa: E402

from src.connectors.meli_catalog import demo_meli_catalog  # noqa: E402
from src.meli_presale import PresaleQuery, classify_presale  # noqa: E402
from src.meli_presale_guided import outcome_for  # noqa: E402


def test_ready_hits_are_options_never_executed():
    scan = classify_presale(
        [PresaleQuery(q="Auriculares inalambricos demo")],
        demo_meli_catalog(),
    )
    outcome = outcome_for(scan)
    assert outcome.status == OPTIONS
    assert all("never POST" in (o.action or "") for o in outcome.options)
    assert verify_guided(outcome) == []


def test_no_ready_hits_are_a_handoff():
    scan = classify_presale(
        [PresaleQuery(q="producto que no existe en el catalogo")],
        demo_meli_catalog(),
    )
    outcome = outcome_for(scan)
    assert outcome.status == HANDOFF
    assert verify_guided(outcome) == []
