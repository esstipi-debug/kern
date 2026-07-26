from datetime import datetime, timedelta, timezone

import pytest

from src.patterns import (
    DenominatorThresholds,
    Occurrence,
    RecurrenceProfile,
    Thresholds,
    benjamini_hochberg,
    build_profiles,
    family_of,
    find_denominators,
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


def test_duty_cycle_is_clamped_at_100_when_the_last_occurrence_lands_exactly_on_window_end():
    """Un episodio que arranca en window_start y cuya ultima ocurrencia cae
    EXACTO en window_end empuja el span mas alla de la ventana por
    cadence_hours (el "dura 1h mas" de la ultima ocurrencia): sin clamp esto
    da ~100.05%, no 100%. Sin acotar, ese numero se vuelca tal cual a un CSV
    que ve el cliente -- un "104.6%" se lee como bug, no como caso limite."""
    occs = [occ(h) for h in range(0, 24 * 90 + 1, 6)]

    profile = _profiles(occs)[0]

    assert profile.duty_cycle == 100.0


# --------------------------------------------------------------------------
# find_denominators: el gate de cuatro pisos (exposicion, tamano de efecto,
# magnitud, evidencia)
# --------------------------------------------------------------------------

DTH = DenominatorThresholds()


def _chronic_profile(entity: str) -> RecurrenceProfile:
    occs = _failure_run(0, entity=entity) + _failure_run(30, entity=entity) + _failure_run(60, entity=entity)
    return _profiles(occs, certified_gap=lambda a, b: True)[0]


def test_benjamini_hochberg_is_monotone_and_never_below_the_raw_p():
    qs = benjamini_hochberg([0.001, 0.02, 0.5])

    assert qs == sorted(qs)
    assert all(q >= p for q, p in zip(qs, [0.001, 0.02, 0.5]))


def test_benjamini_hochberg_on_an_empty_list_returns_empty():
    assert benjamini_hochberg([]) == []


def test_benjamini_hochberg_enforces_monotonicity_even_when_raw_ranks_are_not_already_sorted():
    """[0.001, 0.02, 0.5] (el otro test de monotonia) ya viene ascendente, asi
    que el candidato crudo por rango sale ordenado incluso SIN el minimo
    acumulado -- no distingue esa clausula. Estos tres p-values muy juntos
    (0.008, 0.009, 0.01) sin el acumulado dan [0.024, 0.0135, 0.01]
    (DESCENDENTE); con el acumulado, los tres se saturan a 0.01."""
    qs = benjamini_hochberg([0.008, 0.009, 0.01])

    assert qs == sorted(qs)
    assert qs == pytest.approx([0.01, 0.01, 0.01])


def test_a_real_concentration_is_found():
    """20 de 30 SKUs del proveedor Alfa son cronicos, contra 2 de 70 del resto."""
    chronic = [f"A{i}" for i in range(20)] + [f"B{i}" for i in range(2)]
    base = {f"A{i}" for i in range(30)} | {f"B{i}" for i in range(70)}
    attributes = {e: {"proveedor": "Alfa" if e.startswith("A") else "Beta"} for e in base}
    profiles = [_chronic_profile(e) for e in chronic]

    result = find_denominators(profiles, attributes, base_entities=base, thresholds=DTH)

    assert [f.value for f in result.findings] == ["Alfa"]
    assert result.findings[0].n_v == 20
    assert result.findings[0].m_v == 30
    assert result.findings[0].lift > 1.5
    assert result.findings[0].q_value <= 0.10


def test_pure_noise_produces_no_finding():
    """Fluctuacion de conteo sin causa real, no una correlacion perfecta.

    Una dimension de 10 valores, 20 base cada uno (esperado: 2 cronicos por
    valor). El bucket 'v0' junta 5 cronicos y otros tres juntan 4 cada uno --
    los cuatro cruzan el piso de tamano de efecto (lift=2.0, con margen del
    limite 1.5 para no caer en el borde de precision de punto flotante) y
    entran a la correccion. Solo, el p crudo de 'v0' (~0.035) se leeria como
    significativo (< 0.05): eso es lo que el tool ofreceria SIN el piso de
    evidencia. Corregido por Benjamini-Hochberg junto a las otras 3
    comparaciones que tambien cruzan el piso, su q sube a ~0.122 y no
    sobrevive fdr_q=0.10 -- exactamente el caso que el piso de evidencia
    existe para cortar.
    """
    base = {f"S{i}" for i in range(200)}
    attributes = {f"S{i}": {"dim": f"v{i // 20}"} for i in range(200)}
    bucket_counts = [5, 4, 4, 4, 1, 1, 1, 0, 0, 0]
    chronic = [
        f"S{bucket * 20 + offset}"
        for bucket, count in enumerate(bucket_counts)
        for offset in range(count)
    ]
    profiles = [_chronic_profile(e) for e in chronic]

    result = find_denominators(profiles, attributes, base_entities=base, thresholds=DTH)

    assert result.findings == ()
    assert result.n_comparisons == 10


def test_a_value_below_the_exposure_floor_cannot_be_a_finding():
    """3 de 3 SKUs de un proveedor chiquito: lift altisimo, evidencia nula."""
    base = {f"A{i}" for i in range(3)} | {f"B{i}" for i in range(97)}
    chronic = [f"A{i}" for i in range(3)]
    attributes = {e: {"proveedor": "Chico" if e.startswith("A") else "Grande"} for e in base}
    profiles = [_chronic_profile(e) for e in chronic]

    result = find_denominators(profiles, attributes, base_entities=base, thresholds=DTH)

    assert result.findings == ()


def test_a_value_below_the_lift_floor_cannot_be_a_finding():
    """X: 28 de 200 cronicos contra 12 de 200 en Y (400 base, 40 cronicos).

    El lift de X (1.4) queda justo BAJO el piso de magnitud (1.5) -- pero con
    N=400 la asociacion es estadisticamente muy fuerte (p~0.0058): sin el
    piso de lift, esto SI pasaria el gate de evidencia y seria un hallazgo.
    Aisla la clausula de magnitud de las otras tres (expone que min_base,
    min_chronics y fdr_q por si solos NO alcanzan para cortar este caso).
    """
    base = {f"X{i}" for i in range(200)} | {f"Y{i}" for i in range(200)}
    chronic = [f"X{i}" for i in range(28)] + [f"Y{i}" for i in range(12)]
    attributes = {e: {"proveedor": "X" if e.startswith("X") else "Y"} for e in base}
    profiles = [_chronic_profile(e) for e in chronic]

    result = find_denominators(profiles, attributes, base_entities=base, thresholds=DTH)

    assert result.findings == ()


def test_a_value_below_the_chronics_floor_cannot_be_a_finding():
    """"target" tiene m_v=15 (bien arriba de min_base=10) y lift=6.67 (bien
    arriba de min_lift=1.5) -- solo 2 cronicos de un total de 2 (n_v=2, bajo
    min_chronics=3). El p crudo (~0.021) pasaria fdr_q de estar solo en la
    correccion: aisla la clausula de tamano de efecto de las otras tres, que
    por si solas no alcanzan para cortar este caso."""
    base = {f"T{i}" for i in range(15)} | {f"R{i}" for i in range(85)}
    chronic = ["T0", "T1"]
    attributes = {e: {"grupo": "target" if e.startswith("T") else "rest"} for e in base}
    profiles = [_chronic_profile(e) for e in chronic]

    result = find_denominators(profiles, attributes, base_entities=base, thresholds=DTH)

    assert result.findings == ()


def test_absolute_counts_are_published_even_when_nothing_passes_the_gate():
    base = {f"A{i}" for i in range(3)} | {f"B{i}" for i in range(97)}
    chronic = [f"A{i}" for i in range(3)]
    attributes = {e: {"proveedor": "Chico" if e.startswith("A") else "Grande"} for e in base}
    profiles = [_chronic_profile(e) for e in chronic]

    result = find_denominators(profiles, attributes, base_entities=base, thresholds=DTH)

    assert result.absolute_counts["proveedor"]["Chico"] == 3


def test_entities_without_master_data_land_in_the_sin_dato_bucket():
    base = {f"A{i}" for i in range(50)}
    chronic = [f"A{i}" for i in range(10)]
    attributes = {e: {"proveedor": "Alfa"} for e in list(base)[:25]}
    profiles = [_chronic_profile(e) for e in chronic]

    result = find_denominators(profiles, attributes, base_entities=base, thresholds=DTH)

    assert "<sin dato>" in result.absolute_counts["proveedor"]
    assert all(f.value != "<sin dato>" for f in result.findings)


def test_sin_dato_never_becomes_a_finding_even_when_it_is_the_strongest_signal():
    """18 de los 20 cronicos son justo las 20 entidades SIN dato de 'region'
    (m_v=20, lift=4.5, muy por encima de los tres pisos de forma/magnitud) --
    si el bucket <sin dato> no estuviera excluido de los valores candidatos,
    esto seria HOY el hallazgo mas fuerte del reporte. 'Alfa' y 'Beta' tienen
    apenas 1 cronico cada uno (bajo min_chronics), asi que el resultado
    correcto es sin ningun hallazgo -- a diferencia del otro test del bucket
    <sin dato>, aca la lista de hallazgos NO esta vacia por otro motivo
    (columna uniforme): si <sin dato> colara, este test lo atraparia."""
    base = {f"Z{i}" for i in range(20)} | {f"A{i}" for i in range(40)} | {f"B{i}" for i in range(40)}
    chronic = [f"Z{i}" for i in range(18)] + ["A0", "B0"]
    attributes = {
        e: {"region": "Alfa" if e.startswith("A") else "Beta"}
        for e in base
        if not e.startswith("Z")
    }
    profiles = [_chronic_profile(e) for e in chronic]

    result = find_denominators(profiles, attributes, base_entities=base, thresholds=DTH)

    assert result.findings == ()


def test_a_column_with_one_value_is_rejected_as_too_uniform():
    base = {f"A{i}" for i in range(50)}
    chronic = [f"A{i}" for i in range(10)]
    attributes = {e: {"pais": "CL"} for e in base}
    profiles = [_chronic_profile(e) for e in chronic]

    result = find_denominators(profiles, attributes, base_entities=base, thresholds=DTH)

    assert ("pais", "demasiado uniforme") in [
        (r.column, r.reason) for r in result.dimensions_rejected
    ]


def test_a_column_with_too_many_values_is_rejected_as_too_granular():
    base = {f"A{i}" for i in range(60)}
    chronic = [f"A{i}" for i in range(10)]
    attributes = {e: {"codigo_interno": e} for e in base}
    profiles = [_chronic_profile(e) for e in chronic]

    result = find_denominators(profiles, attributes, base_entities=base, thresholds=DTH)

    reasons = {r.column: r.reason for r in result.dimensions_rejected}
    assert "codigo_interno" in reasons
    assert "granular" in reasons["codigo_interno"]


def test_numeric_values_are_treated_as_discrete_labels():
    """lead_time_days {7, 45} tiene cardinalidad 2 y es la dimension mas predictiva."""
    base = {f"A{i}" for i in range(30)} | {f"B{i}" for i in range(70)}
    chronic = [f"A{i}" for i in range(20)] + [f"B{i}" for i in range(2)]
    attributes = {e: {"lead_time_days": 45 if e.startswith("A") else 7} for e in base}
    profiles = [_chronic_profile(e) for e in chronic]

    result = find_denominators(profiles, attributes, base_entities=base, thresholds=DTH)

    assert [str(f.value) for f in result.findings] == ["45"]


def test_correlated_dimensions_collapse_into_one_finding_with_aliases():
    base = {f"A{i}" for i in range(30)} | {f"B{i}" for i in range(70)}
    chronic = [f"A{i}" for i in range(20)] + [f"B{i}" for i in range(2)]
    attributes = {
        e: {
            "proveedor": "Alfa" if e.startswith("A") else "Beta",
            "marca": "Alfa-brand" if e.startswith("A") else "Beta-brand",
        }
        for e in base
    }
    profiles = [_chronic_profile(e) for e in chronic]

    result = find_denominators(profiles, attributes, base_entities=base, thresholds=DTH)

    assert len(result.findings) == 1
    assert len(result.findings[0].aliases) == 1


def test_collapse_keeps_the_larger_exposure_finding_not_whichever_is_seen_first():
    """'proveedor=Alfa' (m_v=30) y 'certificacion=Cert1' (m_v=22, subconjunto
    con los MISMOS 20 cronicos) describen el mismo grupo -- colapsan. A
    diferencia del otro test de colapso (donde los dos m_v empatan en 30 y no
    distinguen la direccion de la comparacion), aca difieren: el ganador
    tiene que ser 'proveedor' (mayor exposicion, el mas accionable), no
    'certificacion' solo por venir antes en el dict de atributos."""
    base = {f"A{i}" for i in range(30)} | {f"B{i}" for i in range(70)}
    chronic = [f"A{i}" for i in range(20)] + [f"B{i}" for i in range(2)]
    attributes = {
        e: {
            "proveedor": "Alfa" if e.startswith("A") else "Beta",
            "certificacion": (
                "Cert1" if e.startswith("A") and int(e[1:]) < 22 else "Cert2"
            ),
        }
        for e in base
    }
    profiles = [_chronic_profile(e) for e in chronic]

    result = find_denominators(profiles, attributes, base_entities=base, thresholds=DTH)

    assert len(result.findings) == 1
    winner = result.findings[0]
    assert (winner.dimension, winner.value) == ("proveedor", "Alfa")
    assert winner.aliases == (("certificacion", "Cert1"),)


def test_ranking_is_by_attributable_excess_not_by_lift():
    """BIG gana por exceso (7.2 vs 5.0) aunque SML gane por lift (6.25 vs 2.5).

    Los numeros estan elegidos para que los dos criterios se CONTRADIGAN: si el
    ranking fuera por lift (o por n*lift), este test daria SML. Con C=24 sobre
    una base de 300: BIG 12-de-60 -> esperado 4.8, exceso 7.2; SML 6-de-12 ->
    esperado 0.96, exceso 5.04.
    """
    base = (
        {f"BIG{i}" for i in range(60)}
        | {f"SML{i}" for i in range(12)}
        | {f"OTH{i}" for i in range(228)}
    )
    chronic = (
        [f"BIG{i}" for i in range(12)] + [f"SML{i}" for i in range(6)]
        + [f"OTH{i}" for i in range(6)]
    )

    def group(entity: str) -> str:
        return entity[:3]

    attributes = {e: {"proveedor": group(e)} for e in base}
    profiles = [_chronic_profile(e) for e in chronic]

    result = find_denominators(profiles, attributes, base_entities=base, thresholds=DTH)

    assert [f.value for f in result.findings][0] == "BIG"


def _one_off_profile(entity: str) -> RecurrenceProfile:
    return _profiles(_failure_run(0, entity=entity), certified_gap=lambda a, b: True)[0]


def test_only_chronic_or_worse_profiles_count_toward_the_denominator():
    """Ninguno de los otros tests mezcla perfiles cronicos con no-cronicos en
    el mismo llamado (_chronic_profile siempre da cronico) -- asi que ninguno
    prueba que find_denominators filtre por is_chronic_or_worse en vez de
    contar CUALQUIER perfil que le pasen. Un perfil one_off (una sola
    corrida-falla) no puede sumar al denominador: si el filtro no se
    aplicara, este conteo daria 11 en vez de 10."""
    base = {f"A{i}" for i in range(50)}
    attributes = {e: {"proveedor": "Alfa"} for e in base}
    chronic_profiles = [_chronic_profile(f"A{i}") for i in range(10)]
    one_off = _one_off_profile("A10")

    result = find_denominators(
        chronic_profiles + [one_off], attributes, base_entities=base, thresholds=DTH
    )

    assert result.absolute_counts["proveedor"]["Alfa"] == 10


def test_a_chronic_entity_outside_base_entities_does_not_count():
    """Un cronico de FUERA del universo base (p.ej. de otro segmento) no
    puede inflar el denominador de ESTA busqueda: si find_denominators no
    intersectara con base_entities, este conteo daria 11 en vez de 10."""
    base = {f"A{i}" for i in range(50)}
    attributes = {e: {"proveedor": "Alfa"} for e in base}
    profiles = [_chronic_profile(f"A{i}") for i in range(10)] + [_chronic_profile("Z999")]

    result = find_denominators(profiles, attributes, base_entities=base, thresholds=DTH)

    assert result.absolute_counts["proveedor"]["Alfa"] == 10


def test_no_chronics_produces_no_findings_and_no_crash():
    base = {f"A{i}" for i in range(50)}
    attributes = {e: {"proveedor": "Alfa"} for e in base}

    result = find_denominators([], attributes, base_entities=base, thresholds=DTH)

    assert result.findings == ()


def test_no_master_data_at_all_evaluates_no_dimension():
    base = {f"A{i}" for i in range(50)}
    chronic = [f"A{i}" for i in range(10)]
    profiles = [_chronic_profile(e) for e in chronic]

    result = find_denominators(profiles, {}, base_entities=base, thresholds=DTH)

    assert result.dimensions_evaluated == ()
    assert result.findings == ()
