from datetime import datetime, timedelta, timezone

import pytest

from src.patterns import (
    Occurrence,
    Thresholds,
    build_profiles,
    family_of,
    is_chronic_or_worse,
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


WINDOW_START = BASE
WINDOW_END = BASE + timedelta(days=90)
TH = Thresholds()


def _profiles(occs, **kwargs):
    return build_profiles(
        occs, window_start=WINDOW_START, window_end=WINDOW_END,
        thresholds=kwargs.pop("thresholds", TH), **kwargs
    )


def _failure_run(day: float, *, entity: str = "SKU-1") -> list[Occurrence]:
    """Un episodio-falla corto: cruza el ROP y escala a riesgo de quiebre."""
    return [
        occ(day * 24, entity=entity),
        occ(day * 24 + 1, signal="stockout_projected", entity=entity),
    ]


def test_three_failure_episodes_is_chronic():
    occs = _failure_run(0) + _failure_run(30) + _failure_run(60)

    profile = _profiles(occs, certified_gap=lambda a, b: True)[0]

    assert profile.n_failure_episodes == 3
    assert profile.recurrence_label == "chronic"


def test_two_failure_episodes_is_acute():
    occs = _failure_run(0) + _failure_run(30)

    profile = _profiles(occs, certified_gap=lambda a, b: True)[0]

    assert profile.recurrence_label == "acute"


def test_one_failure_episode_is_one_off():
    profile = _profiles(_failure_run(0), certified_gap=lambda a, b: True)[0]

    assert profile.recurrence_label == "one_off"


def test_rop_only_episodes_never_reach_chronic():
    """Seis cruces de ROP sin escalacion son ciclos de reposicion, no fallas."""
    occs = [o for day in (0, 15, 30, 45, 60, 75) for o in [occ(day * 24)]]

    profile = _profiles(occs, certified_gap=lambda a, b: True)[0]

    assert profile.n_episodes == 6
    assert profile.n_failure_episodes == 0
    assert profile.escalation_rate == 0.0
    assert profile.recurrence_label == "one_off"


def test_persistent_is_an_independent_axis_from_the_episode_count():
    """Un unico episodio que cubre media ventana es persistent, no one_off-y-listo."""
    occs = [occ(h) for h in range(0, 24 * 50, 6)]

    profile = _profiles(occs)[0]

    assert profile.n_episodes == 1
    assert profile.recurrence_label == "one_off"
    assert profile.is_persistent is True


def test_duty_cycle_is_covered_hours_over_window_hours():
    occs = [occ(h) for h in range(0, 24 * 45, 6)]

    profile = _profiles(occs)[0]

    assert profile.duty_cycle == pytest.approx(50.0, abs=1.0)


def test_oscillating_needs_availability_and_excess_on_the_same_entity():
    occs = (
        _failure_run(0) + _failure_run(30) + _failure_run(60)
        + [occ(70 * 24, signal="excess_growing")]
    )

    profiles = _profiles(occs, certified_gap=lambda a, b: True)
    availability = [p for p in profiles if p.family == "availability"][0]

    assert availability.is_oscillating is True


def test_excess_on_a_different_entity_does_not_make_it_oscillating():
    occs = (
        _failure_run(0) + _failure_run(30) + _failure_run(60)
        + [occ(70 * 24, signal="excess_growing", entity="OTRO-SKU")]
    )

    profiles = _profiles(occs, certified_gap=lambda a, b: True)
    availability = [p for p in profiles if p.entity_id == "SKU-1"][0]

    assert availability.is_oscillating is False


def test_uncertified_gaps_are_counted_and_reported():
    occs = _failure_run(0) + _failure_run(30)

    profile = _profiles(occs, certified_gap=lambda a, b: False)[0]

    assert profile.n_episodes == 1
    assert profile.n_boundaries_unknown == 1


def test_days_since_last_episode_is_measured_from_the_window_end():
    profile = _profiles(_failure_run(80), certified_gap=lambda a, b: True)[0]

    assert profile.days_since_last_episode == pytest.approx(10.0, abs=0.2)


def test_chronic_min_episodes_is_clamped_to_at_least_two():
    """Con 1, todo one_off seria cronico y la clase acute quedaria indefinida."""
    assert Thresholds(chronic_min_episodes=1).chronic_min_episodes == 2


def test_profiles_are_built_per_entity_and_family():
    occs = _failure_run(0, entity="A") + [occ(0, signal="excess_growing", entity="A")]

    profiles = _profiles(occs)

    assert {(p.entity_id, p.family) for p in profiles} == {("A", "availability"), ("A", "excess")}


def test_is_chronic_or_worse_covers_chronic_persistent_and_oscillating():
    chronic = _profiles(_failure_run(0) + _failure_run(30) + _failure_run(60),
                        certified_gap=lambda a, b: True)[0]
    one_off = _profiles(_failure_run(0), certified_gap=lambda a, b: True)[0]

    assert is_chronic_or_worse(chronic) is True
    assert is_chronic_or_worse(one_off) is False


def test_escalation_rate_is_the_fraction_of_failure_episodes_over_all_episodes():
    """Dos episodios-falla de tres: tiene que dar 2/3, no n_failures pelado ni 1.0."""
    occs = (
        _failure_run(0)      # episodio 1: escala a falla
        + [occ(30 * 24)]     # episodio 2: solo cruza ROP, no escala
        + _failure_run(60)   # episodio 3: escala a falla
    )

    profile = _profiles(occs)[0]

    assert profile.n_episodes == 3
    assert profile.n_failure_episodes == 2
    assert profile.escalation_rate == pytest.approx(2 / 3)


def test_a_persistent_excess_profile_is_never_oscillating_by_itself():
    """is_oscillating describe el lado disponibilidad de un vaiven con exceso en
    la MISMA entidad -- el propio perfil de excess (que trivialmente aparece en
    entities_with_excess) no se marca oscillating aunque sea persistent."""
    occs = [occ(h, signal="excess_growing") for h in range(0, 24 * 50, 6)]

    profile = _profiles(occs)[0]

    assert profile.family == "excess"
    assert profile.is_persistent is True
    assert profile.is_oscillating is False
