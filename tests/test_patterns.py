from datetime import datetime, timedelta, timezone

import pytest

from src.patterns import (
    Occurrence,
    family_of,
    to_episodes,
)

BASE = datetime(2026, 7, 1, tzinfo=timezone.utc)


def occ(hours: float, *, signal: str = "rop_breach", entity: str = "SKU-1") -> Occurrence:
    return Occurrence(entity_id=entity, signal=signal, ts=BASE + timedelta(hours=hours))


def test_family_of_groups_the_two_availability_signals():
    assert family_of("rop_breach") == "availability"
    assert family_of("stockout_projected") == "availability"


def test_family_of_falls_back_to_the_signal_itself_when_unknown():
    """Una senal del CSV del cliente que no conocemos es su propia familia."""
    assert family_of("quiebre_de_stock") == "quiebre_de_stock"


def test_a_continuous_run_is_one_episode():
    occs = [occ(h) for h in range(0, 10)]

    episodes = to_episodes(occs, gap_hours=48.0, cadence_hours=1.0)

    assert len(episodes) == 1
    assert episodes[0].n_occurrences == 10


def test_a_certified_gap_splits_into_two_episodes():
    occs = [occ(0), occ(1), occ(100), occ(101)]

    episodes = to_episodes(occs, gap_hours=48.0, cadence_hours=1.0,
                           certified_gap=lambda a, b: True)

    assert len(episodes) == 2
    assert [e.n_occurrences for e in episodes] == [2, 2]


def test_an_uncertified_gap_does_not_split():
    """El sistema pudo haber estado caido: fusionar es la respuesta conservadora."""
    occs = [occ(0), occ(1), occ(100), occ(101)]

    episodes = to_episodes(occs, gap_hours=48.0, cadence_hours=1.0,
                           certified_gap=lambda a, b: False)

    assert len(episodes) == 1
    assert episodes[0].n_occurrences == 4
    assert episodes[0].boundary_certified is False


def test_gaps_below_the_threshold_never_split_regardless_of_certification():
    occs = [occ(0), occ(20)]

    episodes = to_episodes(occs, gap_hours=48.0, cadence_hours=1.0,
                           certified_gap=lambda a, b: True)

    assert len(episodes) == 1


def test_a_single_occurrence_episode_spans_one_cadence_unit_not_zero():
    episodes = to_episodes([occ(0)], gap_hours=48.0, cadence_hours=1.0)

    assert episodes[0].span_hours == pytest.approx(1.0)


def test_span_is_last_minus_first_plus_one_cadence_unit():
    occs = [occ(0), occ(1), occ(2)]

    episodes = to_episodes(occs, gap_hours=48.0, cadence_hours=1.0)

    assert episodes[0].span_hours == pytest.approx(3.0)


def test_occurrences_are_sorted_by_ts_before_grouping():
    """Las filas pueden llegar desordenadas; el motor no confia en el orden de entrada."""
    occs = [occ(5), occ(0), occ(2)]

    episodes = to_episodes(occs, gap_hours=48.0, cadence_hours=1.0)

    assert len(episodes) == 1
    assert episodes[0].start == BASE


def test_an_episode_containing_the_escalation_signal_is_a_failure_episode():
    occs = [occ(0), occ(1, signal="stockout_projected")]

    episodes = to_episodes(occs, gap_hours=48.0, cadence_hours=1.0)

    assert episodes[0].is_failure is True


def test_a_rop_only_episode_is_not_a_failure_episode():
    """Cruzar el ROP es el comportamiento disenado de revision continua."""
    occs = [occ(0), occ(1)]

    episodes = to_episodes(occs, gap_hours=48.0, cadence_hours=1.0)

    assert episodes[0].is_failure is False


def test_a_family_without_escalation_signal_counts_every_episode_as_failure():
    occs = [occ(0, signal="excess_growing")]

    episodes = to_episodes(occs, gap_hours=48.0, cadence_hours=1.0)

    assert episodes[0].is_failure is True


def test_mixed_entities_raise_because_the_caller_must_group_first():
    occs = [occ(0, entity="A"), occ(1, entity="B")]

    with pytest.raises(ValueError, match="one entity"):
        to_episodes(occs, gap_hours=48.0, cadence_hours=1.0)


def test_mixed_families_raise_because_the_caller_must_group_first():
    occs = [occ(0, signal="rop_breach"), occ(1, signal="excess_growing")]

    with pytest.raises(ValueError, match="one family"):
        to_episodes(occs, gap_hours=48.0, cadence_hours=1.0)


def test_empty_input_returns_no_episodes():
    assert to_episodes([], gap_hours=48.0, cadence_hours=1.0) == []


def test_default_certified_gap_treats_every_threshold_gap_as_certified():
    """``certified_gap=None`` es el modo CSV: sin instrumentacion, todo hueco
    >= umbral se asume certificado y parte. Si el default se invirtiera a
    "nunca partir", este hueco de 99h se fundiria en un unico episodio de 4
    ocurrencias en vez de partir en dos de a 2."""
    occs = [occ(0), occ(1), occ(100), occ(101)]

    episodes = to_episodes(occs, gap_hours=48.0, cadence_hours=1.0)

    assert len(episodes) == 2
    assert [e.n_occurrences for e in episodes] == [2, 2]
    assert episodes[1].boundary_certified is True


def test_peak_signal_is_the_escalation_signal_even_when_it_is_not_the_last_occurrence():
    occs = [
        occ(0, signal="rop_breach"),
        occ(1, signal="stockout_projected"),
        occ(2, signal="rop_breach"),
    ]

    episodes = to_episodes(occs, gap_hours=48.0, cadence_hours=1.0)

    assert episodes[0].peak_signal == "stockout_projected"


def test_peak_signal_falls_back_to_the_first_signal_when_there_is_no_escalation():
    occs = [occ(0), occ(1)]

    episodes = to_episodes(occs, gap_hours=48.0, cadence_hours=1.0)

    assert episodes[0].peak_signal == "rop_breach"


def test_prior_gap_hours_is_none_for_the_first_episode():
    episodes = to_episodes([occ(0), occ(1)], gap_hours=48.0, cadence_hours=1.0)

    assert episodes[0].prior_gap_hours is None


def test_prior_gap_hours_carries_the_size_of_the_gap_that_opened_the_episode():
    occs = [occ(0), occ(1), occ(100), occ(101)]

    episodes = to_episodes(occs, gap_hours=48.0, cadence_hours=1.0,
                           certified_gap=lambda a, b: True)

    assert episodes[1].prior_gap_hours == pytest.approx(99.0)


def test_multiple_consecutive_uncertified_gaps_all_merge_into_one_episode():
    """Tres corridas separadas por DOS huecos sin certificar tienen que
    fundirse en un unico episodio (no en dos fusiones independientes), y el
    episodio fundido no debe heredar un prior_gap_hours espurio."""
    occs = [occ(0), occ(1), occ(100), occ(101), occ(200), occ(201)]

    episodes = to_episodes(occs, gap_hours=48.0, cadence_hours=1.0,
                           certified_gap=lambda a, b: False)

    assert len(episodes) == 1
    assert episodes[0].n_occurrences == 6
    assert episodes[0].boundary_certified is False
    assert episodes[0].prior_gap_hours is None


def test_mixed_certified_then_uncertified_gap_keeps_the_entry_gap_as_prior_gap_hours():
    """Un corte certificado abre un episodio nuevo; un hueco DENTRO de ese
    episodio que despues falla certificacion se funde sin pisar el
    prior_gap_hours de entrada (el que si certifico)."""
    entry_gap_start = BASE + timedelta(hours=1)

    def certified_gap(start: datetime, end: datetime) -> bool:
        return start == entry_gap_start

    occs = [occ(0), occ(1), occ(100), occ(101), occ(250)]

    episodes = to_episodes(occs, gap_hours=48.0, cadence_hours=1.0,
                           certified_gap=certified_gap)

    assert len(episodes) == 2
    episode_a, episode_b = episodes
    assert episode_a.n_occurrences == 2
    assert episode_b.n_occurrences == 3
    assert episode_b.boundary_certified is False
    assert episode_b.prior_gap_hours == pytest.approx(99.0)
