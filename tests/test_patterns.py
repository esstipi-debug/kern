from datetime import datetime, timedelta, timezone

import pytest

from src.patterns import (
    Occurrence,
    RecurrenceProfile,
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


# --------------------------------------------------------------------------
# Thresholds: parametrizacion end-to-end (los 4 campos, valores NO default)
# --------------------------------------------------------------------------


def test_chronic_min_episodes_threshold_changes_the_recurrence_label():
    """Con 3 episodios-falla, el default (chronic_min_episodes=3) clasifica
    'chronic'. Subir el umbral a 4 sobre la MISMA entrada tiene que bajarlo a
    'acute'. Si build_profiles ignorara thresholds.chronic_min_episodes y
    usara el 3 default hardcodeado, el segundo perfil seguiria dando
    'chronic'."""
    occs = _failure_run(0) + _failure_run(30) + _failure_run(60)

    default_profile = _profiles(occs)[0]
    stricter_profile = _profiles(occs, thresholds=Thresholds(chronic_min_episodes=4))[0]

    assert default_profile.recurrence_label == "chronic"
    assert stricter_profile.recurrence_label == "acute"


def test_persistent_duty_pct_threshold_changes_is_persistent():
    """Estas ocurrencias dan un duty cycle de ~55%: pasa el default (50%) pero
    no un umbral mas estricto (90%) sobre la MISMA entrada. Si build_profiles
    ignorara thresholds.persistent_duty_pct y usara el 50.0 default
    hardcodeado, el segundo perfil seguiria dando is_persistent=True."""
    occs = [occ(h) for h in range(0, 24 * 50, 6)]

    default_profile = _profiles(occs)[0]
    stricter_profile = _profiles(occs, thresholds=Thresholds(persistent_duty_pct=90.0))[0]

    assert default_profile.is_persistent is True
    assert stricter_profile.is_persistent is False


def test_episode_gap_hours_threshold_changes_episode_count():
    """Un hueco de 59h parte en dos episodios bajo el default (48h) pero no
    bajo un umbral mas laxo (100h) sobre la MISMA entrada. Si build_profiles
    ignorara thresholds.episode_gap_hours y usara el 48.0 default
    hardcodeado, ambos perfiles verian el mismo n_episodes."""
    occs = [occ(0), occ(1), occ(60), occ(61)]

    default_profile = _profiles(occs)[0]
    laxer_profile = _profiles(occs, thresholds=Thresholds(episode_gap_hours=100.0))[0]

    assert default_profile.n_episodes == 2
    assert laxer_profile.n_episodes == 1


def test_cadence_hours_threshold_changes_duty_cycle():
    """cadence_hours entra en el span de cada episodio (span = ultima menos
    primera ocurrencia, mas cadence). Agrandarlo sobre la MISMA entrada tiene
    que mover duty_cycle proporcionalmente. Si build_profiles ignorara
    thresholds.cadence_hours y usara el 1.0 default hardcodeado, ambos
    perfiles tendrian el mismo duty_cycle."""
    occs = [occ(0), occ(1), occ(2)]
    window_hours = (WINDOW_END - WINDOW_START).total_seconds() / 3600.0

    default_profile = _profiles(occs)[0]
    wider_profile = _profiles(occs, thresholds=Thresholds(cadence_hours=10.0))[0]

    assert default_profile.duty_cycle == pytest.approx(3 / window_hours * 100.0)
    assert wider_profile.duty_cycle == pytest.approx(12 / window_hours * 100.0)
    assert wider_profile.duty_cycle > default_profile.duty_cycle


# --------------------------------------------------------------------------
# is_chronic_or_worse: las 3 clausulas protegidas por separado
# --------------------------------------------------------------------------


def _synthetic_profile(
    *, recurrence_label: str, is_persistent: bool, is_oscillating: bool
) -> RecurrenceProfile:
    """Perfil construido a mano, sin pasar por build_profiles.

    build_profiles nunca produce is_oscillating=True con is_persistent=False
    y recurrence_label != 'chronic' (is_oscillating exige chronic O persistent
    como precondicion propia -- ver su docstring/logica). Un perfil derivado
    de build_profiles para "oscilante pero no chronic" seria SIEMPRE tambien
    persistent, y por lo tanto no distinguiria la clausula
    ``or profile.is_oscillating`` de la clausula ``or profile.is_persistent``
    en is_chronic_or_worse -- exactamente el patron de coincidencia accidental
    que ya paso tres veces en este modulo. Construir el perfil a mano aisla
    cada clausula de verdad.
    """
    return RecurrenceProfile(
        entity_id="SKU-X",
        family="availability",
        episodes=(),
        n_occurrences=0,
        n_episodes=0,
        n_failure_episodes=0,
        escalation_rate=0.0,
        duty_cycle=0.0,
        days_since_last_episode=0.0,
        n_boundaries_unknown=0,
        recurrence_label=recurrence_label,
        is_persistent=is_persistent,
        is_oscillating=is_oscillating,
    )


def test_is_chronic_or_worse_is_true_for_persistent_profiles_that_are_not_chronic():
    """Persistent solo (no chronic, no oscillating): protege especificamente
    la clausula ``or profile.is_persistent``."""
    profile = _profiles([occ(h) for h in range(0, 24 * 50, 6)])[0]

    assert profile.recurrence_label == "one_off"
    assert profile.is_persistent is True
    assert profile.is_oscillating is False
    assert is_chronic_or_worse(profile) is True


def test_is_chronic_or_worse_is_true_for_oscillating_profiles_that_are_not_chronic_or_persistent():
    """Oscillating solo (no chronic, no persistent): protege especificamente
    la clausula ``or profile.is_oscillating``, aislada de is_persistent porque
    build_profiles no puede producir esta combinacion (ver
    ``_synthetic_profile``)."""
    profile = _synthetic_profile(
        recurrence_label="one_off", is_persistent=False, is_oscillating=True
    )

    assert is_chronic_or_worse(profile) is True


# --------------------------------------------------------------------------
# build_profiles: filtro de ventana [window_start, window_end]
# --------------------------------------------------------------------------


def test_duty_cycle_never_exceeds_100_even_with_occurrences_outside_the_window():
    """150 dias de ocurrencias (cada 7h, antes/dentro/despues de una ventana
    de 90 dias) sin filtro darian un duty_cycle de ~166% -- el mismo numero
    sin sentido que motivo el arreglo. Filtradas a la ventana, el duty cycle
    queda acotado."""
    occs = [occ(h) for h in range(-720, 2880, 7)]

    profile = _profiles(occs)[0]

    assert profile.duty_cycle <= 100.0
    assert profile.duty_cycle == pytest.approx(99.86, abs=0.01)


def test_an_occurrence_outside_the_window_does_not_inflate_episode_count():
    """Una ocurrencia 100h antes de window_start, si no se filtrara, abriria
    un episodio propio (el hueco hasta la primera ocurrencia dentro de la
    ventana es >= episode_gap_hours). Filtrada, el perfil ve solo las 2
    ocurrencias dentro de la ventana en UN episodio."""
    occs = [occ(-100), occ(0), occ(1)]

    profile = _profiles(occs)[0]

    assert profile.n_occurrences == 2
    assert profile.n_episodes == 1


def test_window_boundaries_are_inclusive():
    """Una ocurrencia justo en window_start y otra justo en window_end tienen
    que contar las dos -- el filtro es <= en los dos extremos, no exclusivo."""
    occs = [occ(0), occ(24 * 90)]

    profile = _profiles(occs)[0]

    assert profile.n_occurrences == 2


def test_an_entity_with_no_occurrences_inside_the_window_produces_no_profile():
    """Si TODAS las ocurrencias de una entidad caen fuera de la ventana, no
    tiene que aparecer en el resultado -- ni como perfil vacio."""
    occs = [occ(-100), occ(-50)]

    profiles = _profiles(occs)

    assert profiles == []
