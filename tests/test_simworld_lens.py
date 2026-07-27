import numpy as np
import pandas as pd
import pytest

from jobs.intake import prepare_tracked
from src.simworld.lens import (
    CASE_SIZE,
    EPOCH,
    LEVELS,
    UNKNOWN_SUPPLIER,
    apply_dirt,
    export_history,
    history_frame,
    inject_calendar_gaps,
    inject_duplicate_rows,
    inject_missing_lead_time,
    inject_mixed_date_formats,
    inject_mixed_units,
    inject_renamed_supplier,
    inject_returns_as_negatives,
    inject_split_sku,
    inject_thousand_separators,
    inject_unmarked_promo,
    inject_whitespace_case,
    inject_zero_cost,
)
from src.simworld.world import advance, generate_world, initial_state


def _run_weeks(n_weeks=8, seed=5, n_skus=4):
    w = generate_world(seed=seed, n_skus=n_skus, n_suppliers=2, weeks=n_weeks,
                       regime="seasonal_trend")
    st = initial_state(w)
    outs = []
    for _ in range(n_weeks):
        st, out = advance(w, st, {})
        outs.append(out)
    return w, outs


def test_level_zero_has_the_canonical_columns_intake_expects():
    w, outs = _run_weeks()
    df = history_frame(w, outs)
    for col in ("date", "product_id", "quantity", "unit_cost", "lead_time_days"):
        assert col in df.columns


def test_level_zero_row_count_is_skus_times_weeks():
    w, outs = _run_weeks(n_weeks=8, n_skus=4)
    assert len(history_frame(w, outs)) == 4 * 8


def test_level_zero_quantity_is_the_sold_quantity():
    w, outs = _run_weeks(n_weeks=3, n_skus=2)
    df = history_frame(w, outs)
    total = sum(float(o.sold.sum()) for o in outs)
    assert df["quantity"].sum() == pytest.approx(total)


def test_export_writes_a_readable_csv(tmp_path):
    w, outs = _run_weeks()
    p = export_history(w, outs, tmp_path / "h.csv", level=0)
    assert p.exists()
    back = pd.read_csv(p)
    assert len(back) == len(history_frame(w, outs))


def test_unknown_level_raises(tmp_path):
    w, outs = _run_weeks()
    with pytest.raises(ValueError, match="nivel"):
        export_history(w, outs, tmp_path / "h.csv", level=9)


def test_intake_reads_level_zero_without_dropping_rows(tmp_path):
    w, outs = _run_weeks(n_weeks=10, n_skus=5)
    p = export_history(w, outs, tmp_path / "clean.csv", level=0)
    frame, quality = prepare_tracked(p)
    assert quality.n_dropped_bad_date == 0
    assert quality.n_dropped_bad_quantity == 0
    assert quality.synthesized == ()
    assert not frame.empty


# --- Tests added beyond the brief, closing gaps found by the discrimination
# question ("against which incorrect implementation does this test fail?").
# See task-3-report.md for the per-test rationale.


def test_level_zero_values_match_world_truth_not_just_column_names():
    """The brief's column-presence test would pass even if every cell were
    garbage (zeros, swapped fields, wrong SKU). This pins the actual values --
    including `quantity` against a (product_id, date) -> sold mapping built
    independently from the outcomes, cell by cell. `quantity >= 0.0` alone
    (the original assertion here) is trivially true for every fixture and
    would not catch a rotated/transposed SKU index: a `history_frame` that
    wrote `out.sold[(i + 1) % n_skus]` into product_id `i`'s row preserves
    every per-week total and every column, and still passes that check. The
    per-cell lookup below fails on that mutation as long as SKUs differ in
    their sold quantities at a given date, which a fresh random seed almost
    certainly gives us; see task-3-report.md for the deliberate-break proof."""
    w, outs = _run_weeks(n_weeks=4, n_skus=3)
    df = history_frame(w, outs)
    cost_by_sku = {s.sku: s.unit_cost for s in w.skus}
    lead_by_sku = {s.sku: float(w.suppliers[s.supplier_idx].promised_lead_weeks * 7) for s in w.skus}
    sold_by_key = {}
    for out in outs:
        date = (EPOCH + pd.Timedelta(weeks=out.week)).strftime("%Y-%m-%d")
        for i, sku in enumerate(w.skus):
            sold_by_key[(sku.sku, date)] = float(out.sold[i])
    for _, row in df.iterrows():
        key = (row["product_id"], row["date"])
        assert row["unit_cost"] == pytest.approx(cost_by_sku[row["product_id"]])
        assert row["lead_time_days"] == pytest.approx(lead_by_sku[row["product_id"]])
        assert row["quantity"] == pytest.approx(sold_by_key[key])
    # una fecha compartida por periodo (una por semana, no una por SKU)
    assert df["date"].nunique() == 4


def test_lead_time_days_is_promised_never_real_even_when_they_differ():
    """Guards the resolved ambiguity: lead_time_days must stay the supplier's
    promise even for the exact seed/shape known to produce real != promised
    (mirrors test_promised_lead_time_differs_from_real_at_least_once in
    test_simworld_world.py). A history_frame that leaked real_lead_weeks in
    would still pass every other test in this file."""
    w = generate_world(seed=3, n_skus=12, n_suppliers=4, weeks=52, regime="seasonal_trend")
    promised = np.array([w.suppliers[s.supplier_idx].promised_lead_weeks for s in w.skus])
    assert (w.real_lead_weeks != promised[:, None]).any()  # sanity: seed still produces a gap

    st = initial_state(w)
    outs = []
    for _ in range(w.weeks):
        st, out = advance(w, st, {})
        outs.append(out)

    df = history_frame(w, outs)
    expected = {s.sku: float(w.suppliers[s.supplier_idx].promised_lead_weeks * 7) for s in w.skus}
    for product_id, group in df.groupby("product_id"):
        assert (group["lead_time_days"] == expected[product_id]).all()


def test_export_round_trip_preserves_values_exactly(tmp_path):
    """The brief's CSV test only checks row COUNT, so it would pass even if
    export_history wrote an extra index column or corrupted values on the way
    out. This compares the written-and-reread frame cell-for-cell."""
    w, outs = _run_weeks(n_weeks=5, n_skus=3)
    df = history_frame(w, outs)
    p = export_history(w, outs, tmp_path / "h.csv", level=0)
    back = pd.read_csv(p)
    assert list(back.columns) == list(df.columns)
    pd.testing.assert_frame_equal(
        back.sort_values(["product_id", "date"]).reset_index(drop=True),
        df.sort_values(["product_id", "date"]).reset_index(drop=True),
        check_dtype=False,
    )


def test_lens_never_mutates_outcomes_or_real_lead_weeks(tmp_path):
    """history_frame's only ndarray read is out.sold (via world.skus and
    world.suppliers, which are immutable dataclasses, not ndarrays); it does
    not read world.demand, world.real_lead_weeks, or out.demand at all. The
    out.sold check below is what actually kills a live bug -- an
    implementation that rounds/sorts/clips sold in place would sail through
    every other test in this file. The world.demand/real_lead_weeks/
    out.demand snapshots are inert today (no plausible wrong implementation
    trips them); they're kept as defence-in-depth against a future
    history_frame that starts reading those fields too, so a regression there
    fails a test instead of shipping silently."""
    w, outs = _run_weeks(n_weeks=6, n_skus=3)
    demand_before = w.demand.copy()
    real_lead_before = w.real_lead_weeks.copy()
    sold_before = [o.sold.copy() for o in outs]
    demand_out_before = [o.demand.copy() for o in outs]

    history_frame(w, outs)
    export_history(w, outs, tmp_path / "h.csv", level=0)

    assert np.array_equal(w.demand, demand_before)
    assert np.array_equal(w.real_lead_weeks, real_lead_before)
    for out, before in zip(outs, sold_before):
        assert np.array_equal(out.sold, before)
    for out, before in zip(outs, demand_out_before):
        assert np.array_equal(out.demand, before)


def test_apply_dirt_level_zero_returns_an_independent_copy():
    """apply_dirt(level=0) must be safe for later dirt injectors (Tasks 4-5)
    to build on: returning the SAME frame object would let a level-1/2/3
    injector mutate the caller's level-0 frame in place. Content equality
    alone (what a naive test would check) does not catch that."""
    w, outs = _run_weeks(n_weeks=2, n_skus=2)
    df = history_frame(w, outs)
    dirty = apply_dirt(df, level=0)
    assert dirty is not df
    pd.testing.assert_frame_equal(dirty, df)


def test_apply_dirt_unknown_level_raises():
    w, outs = _run_weeks(n_weeks=2, n_skus=2)
    df = history_frame(w, outs)
    with pytest.raises(ValueError, match="nivel"):
        apply_dirt(df, level=9)


# --- Task 4: inyectores de suciedad nivel 1 (cosmetico) y nivel 2 (semantico) --


def _clean_frame(n_weeks=12, n_skus=6):
    w, outs = _run_weeks(n_weeks=n_weeks, n_skus=n_skus)
    return history_frame(w, outs)


def _canon(series):
    return series.astype(str).str.strip().str.upper()


def test_whitespace_case_changes_only_product_id():
    """No basta con "algo cambio en product_id": pin la transformacion exacta
    (prefijo espacio + minusculas) en las filas tocadas, y que las filas NO
    tocadas -- y las demas columnas, en todas las filas -- queden bit a bit
    identicas. Una implementacion que tambien tocara quantity/date/lead_time_days,
    o que reescribiera TODAS las filas en vez de una fraccion, pasaria el
    assert original de "no cambio quantity" + "algo cambio en product_id" pero
    falla aca."""
    df = _clean_frame()
    out = inject_whitespace_case(df, np.random.default_rng(0))
    assert (out["quantity"].to_numpy() == df["quantity"].to_numpy()).all()
    assert (out["unit_cost"].to_numpy() == df["unit_cost"].to_numpy()).all()
    assert (out["date"].to_numpy() == df["date"].to_numpy()).all()
    assert (out["lead_time_days"].to_numpy() == df["lead_time_days"].to_numpy()).all()
    changed = out["product_id"] != df["product_id"]
    assert changed.any()
    assert changed.sum() < len(out)  # cosmetico parcial, no el 100% de las filas
    assert (out.loc[changed, "product_id"] == " " + df.loc[changed, "product_id"].str.lower()).all()
    assert (out.loc[~changed, "product_id"] == df.loc[~changed, "product_id"]).all()


def test_mixed_date_formats_produces_two_distinct_patterns():
    """Ademas del conteo parcial del brief: el dia calendario no cambia (solo
    el formato de texto), y las filas no marcadas quedan exactamente en el
    formato ISO original. Descarta una implementacion que corrompa la fecha
    al reformatear, o que reformatee todas las filas."""
    df = _clean_frame()
    out = inject_mixed_date_formats(df, np.random.default_rng(0))
    slashed = out["date"].astype(str).str.contains("/")
    assert 0 < slashed.sum() < len(out)
    # las marcadas cambiaron de formato (%d/%m/%Y) pero no de dia calendario
    reparsed_slashed = pd.to_datetime(out.loc[slashed, "date"], format="%d/%m/%Y")
    original_slashed = pd.to_datetime(df.loc[slashed, "date"], format="%Y-%m-%d")
    assert (reparsed_slashed.to_numpy() == original_slashed.to_numpy()).all()
    # las no marcadas quedan exactamente en el formato ISO original
    assert (out.loc[~slashed, "date"] == df.loc[~slashed, "date"]).all()
    assert (out["quantity"].to_numpy() == df["quantity"].to_numpy()).all()


def test_thousand_separators_make_quantity_a_string_column():
    """Ademas de "hay comas": las filas formateadas recuperan, parseadas, el
    mismo numero original (no NaN, no un valor distinto), y las filas no
    tocadas siguen valiendo lo mismo. Sin esto, un inyector que formatee mal
    (o que trunque en vez de formatear) pasaria igual con solo "contains(',')"."""
    df = _clean_frame()
    out = inject_thousand_separators(df, np.random.default_rng(0))
    assert out["quantity"].dtype == object
    formatted = out["quantity"].astype(str).str.contains(",")
    assert 0 < formatted.sum() < len(out)
    recovered = pd.to_numeric(
        out.loc[formatted, "quantity"].str.replace(".", "", regex=False).str.replace(",", ".", regex=False),
        errors="coerce",
    )
    assert recovered.notna().all()
    assert recovered.to_numpy() == pytest.approx(df.loc[formatted, "quantity"].to_numpy())
    untouched = pd.to_numeric(out.loc[~formatted, "quantity"])
    assert untouched.to_numpy() == pytest.approx(df.loc[~formatted, "quantity"].to_numpy())


def test_split_sku_creates_a_second_spelling_and_preserves_total_quantity():
    """El brief solo pide "aparece una grafia nueva" + "el total no cambia" --
    ambas se cumplirian igual si el inyector "partiera" cero filas en la
    practica (nunique no subiria) o si les asignara cantidad 0 (el total
    seguiria cuadrando). Se pin que la grafia nueva efectivamente exista Y
    cargue cantidad positiva -- si no, el split es cosmetico, no una SKU
    partida de verdad."""
    df = _clean_frame()
    out = inject_split_sku(df, np.random.default_rng(0))
    assert out["product_id"].nunique() > df["product_id"].nunique()
    assert out["quantity"].sum() == pytest.approx(df["quantity"].sum())
    new_ids = set(out["product_id"]) - set(df["product_id"])
    assert new_ids
    new_qty = out.loc[out["product_id"].isin(new_ids), "quantity"].sum()
    assert new_qty > 0


def test_split_sku_works_on_a_non_sku_dash_catalog():
    """El reviewer verifico que, con el prefijo hardcodeado ``"SKU-"``, un
    catalogo con otra convencion de nombres (p.ej. ``"P1000"``, sin guion)
    hace que el inyector devuelva el frame intacto -- silenciosamente, sin
    ninguna senal de que no hizo nada -- reduciendo el nivel 2 a cuatro
    inyectores. Se repite el mismo fixture pero renombrando el catalogo a
    ids ``P1000..P1005`` (sin ``"SKU-"``) para confirmar que ahora SI aparece
    una segunda grafia, con las mismas garantias que la prueba de arriba:
    cantidad total preservada, la grafia nueva carga parte de esa cantidad, y
    la nueva grafia no colapsa con ninguna original bajo strip().upper()."""
    df = _clean_frame()
    rename = {old: f"P{1000 + i}" for i, old in enumerate(sorted(df["product_id"].unique()))}
    df = df.assign(product_id=df["product_id"].map(rename))
    out = inject_split_sku(df, np.random.default_rng(0))
    assert out["product_id"].nunique() > df["product_id"].nunique()
    assert out["quantity"].sum() == pytest.approx(df["quantity"].sum())
    new_ids = set(out["product_id"]) - set(df["product_id"])
    assert new_ids
    new_qty = out.loc[out["product_id"].isin(new_ids), "quantity"].sum()
    assert new_qty > 0
    canon_new = _canon(pd.Series(sorted(new_ids)))
    canon_original = set(_canon(df["product_id"]))
    assert not canon_new.isin(canon_original).any()


def test_mixed_units_divides_some_rows_by_the_case_size():
    """Ademas de "la suma baja": pin que las filas tocadas valen exactamente
    original/CASE_SIZE (redondeado), y que las filas no tocadas no se mueven
    ni un centavo. Sin esto, cualquier corrupcion que baje la suma total
    (poner NaN, restar un numero fijo) pasaria el assert del brief."""
    df = _clean_frame()
    out = inject_mixed_units(df, np.random.default_rng(0))
    assert out["quantity"].sum() < df["quantity"].sum()
    assert len(out) == len(df)
    changed = out["quantity"].to_numpy() != df["quantity"].to_numpy()
    assert changed.any()
    expected = (df["quantity"].to_numpy()[changed] / CASE_SIZE).round(2)
    assert out["quantity"].to_numpy()[changed] == pytest.approx(expected)
    assert (out["quantity"].to_numpy()[~changed] == df["quantity"].to_numpy()[~changed]).all()


def test_returns_as_negatives_introduces_negative_quantities():
    """Ademas de "hay negativos": pin la magnitud exacta (-20% de la cantidad
    original vendida), y que las filas sin devolucion no se toquen. Sin esto,
    cualquier ruido que produzca AL MENOS un negativo por casualidad (o un bug
    que niegue el 100% de las filas) pasaria el `.any()` del brief."""
    df = _clean_frame()
    out = inject_returns_as_negatives(df, np.random.default_rng(0))
    negative = out["quantity"] < 0
    assert negative.any()
    assert negative.sum() < len(out)
    expected = -(df.loc[negative, "quantity"] * 0.2).round(2)
    assert out.loc[negative, "quantity"].to_numpy() == pytest.approx(expected.to_numpy())
    assert (out.loc[~negative, "quantity"].to_numpy() == df.loc[~negative, "quantity"].to_numpy()).all()


def test_missing_lead_time_blanks_about_a_third_of_the_lead_time_values():
    """El brief solo chequea que haya NaN y no-NaN. Eso pasaria igual si el
    blanking fuera por FILA individual al azar en vez de por VALOR declarado
    de lead time -- que es la semantica real del inyector (agrupa por valor,
    no por proveedor: dos proveedores que declaren el mismo lead time se
    blanquean o se salvan siempre juntos). Se pin que cada valor original de
    lead_time_days queda enteramente NaN o enteramente intacto, nunca
    mezclado dentro del mismo grupo."""
    df = _clean_frame()
    out = inject_missing_lead_time(df, np.random.default_rng(0))
    assert out["lead_time_days"].isna().any()
    assert out["lead_time_days"].notna().any()
    for _value, group in df.groupby("lead_time_days"):
        status = out.loc[group.index, "lead_time_days"].isna()
        assert status.all() or (~status).all()
    assert (out["quantity"].to_numpy() == df["quantity"].to_numpy()).all()


def test_calendar_gaps_drops_whole_dates_not_partial_rows():
    """La semantica real es "la semana entera no se cargo", no "algunas filas
    de esa semana se perdieron". Se pin que cada fecha remanente sigue
    teniendo todas sus SKUs, y que las fechas eliminadas desaparecen del todo
    -- un inyector que tirara filas individuales al azar (en vez de fechas
    completas) pasaria un `len(out) < len(df)` pero fallaria aca."""
    df = _clean_frame()
    out = inject_calendar_gaps(df, np.random.default_rng(0))
    assert len(out) < len(df)
    n_skus = df["product_id"].nunique()
    original_dates = set(df["date"].astype(str))
    remaining_dates = set(out["date"].astype(str))
    dropped_dates = original_dates - remaining_dates
    assert dropped_dates
    for d in remaining_dates:
        assert (out["date"].astype(str) == d).sum() == n_skus
    for d in dropped_dates:
        assert (out["date"].astype(str) == d).sum() == 0


def test_calendar_gaps_always_drops_at_least_one_date():
    """`inject_missing_lead_time` fuerza un blanking cuando el sorteo da cero
    (ver el fallback ``if not mask.any()``); `inject_calendar_gaps` no tenia
    ese mismo resguardo -- con 12 fechas y 12% de probabilidad por fecha, el
    sorteo de la semilla 10 no tira NINGUNA fecha (medido directamente sobre
    esta misma fixture), asi que sin el fallback el nivel 2 de esa semilla no
    contendria huecos de calendario en absoluto. Se fija la semilla exacta
    que reproduce ese sorteo vacio y se confirma que, con el fallback, sigue
    cayendo al menos una fecha -- deterministicamente la primera en orden
    ascendente."""
    df = _clean_frame(n_weeks=12, n_skus=6)
    dates = sorted(df["date"].astype(str).unique())
    out = inject_calendar_gaps(df, np.random.default_rng(10))
    assert len(out) < len(df)
    remaining_dates = set(out["date"].astype(str))
    assert dates[0] not in remaining_dates


def test_levels_accumulate():
    """El chequeo original de "el nivel 2 hereda el desprolijo del 1" solo pedia
    un espacio en algun lado de product_id -- y `inject_split_sku` (propio del
    nivel 2, sin tocar el nivel 1) ya produce ``"Sku 1005"``, que CONTIENE un
    espacio. Un `apply_dirt` que saltara la capa cosmetica en niveles >= 2
    (contract 3 roto) pasaria ese assert igual, porque la evidencia la
    fabrica el propio inyector del nivel 2, no el del nivel 1.

    Se reemplaza por una firma que SOLO `inject_whitespace_case` puede
    producir: espacio inicial Y todo en minusculas. `inject_split_sku` nunca
    produce eso -- su segunda grafia (``"Sku 1005"``) no lleva espacio inicial
    y no es enteramente minuscula (la ``S`` queda mayuscula)."""
    df = _clean_frame()
    l1 = apply_dirt(df, level=1, seed=0)
    l2 = apply_dirt(df, level=2, seed=0)
    # el nivel 2 hereda el desprolijo del 1: firma exacta de inject_whitespace_case
    # (espacio inicial + minusculas), que inject_split_sku no puede fabricar por
    # su cuenta ("Sku 1005" no tiene espacio inicial ni es todo minuscula).
    l2_ids = l2["product_id"].astype(str)
    cosmetic_signature = l2_ids.str.startswith(" ") & (l2_ids == l2_ids.str.lower())
    assert cosmetic_signature.any()
    # el nivel 1 solo ensucia la grafia: normalizando vuelve el catalogo original
    assert _canon(l1["product_id"]).nunique() == df["product_id"].nunique()
    # el nivel 2 parte una SKU de verdad: normalizar no la recupera
    assert _canon(l2["product_id"]).nunique() > df["product_id"].nunique()


def test_value_changing_dirt_runs_before_the_cosmetic_layer():
    """Si el formato se aplicara primero, los inyectores semanticos tendrian que
    parsear "1.234,50" y coercionarian a NaN en silencio.

    El umbral original (``> 0.9``) no discrimina: replayado contra un
    `apply_dirt` cosmetico-primero (``range(1, level + 1)``), la fraccion
    recuperada da 0.9028 -- pasa con un margen de 2.8e-3, y entre las
    semillas 0-11 el cosmetico-primero limpia 0.9 en 10 de 12. Con el orden
    correcto (como esta shippeado) la fraccion es exactamente 1.0, asi que
    ``> 0.99`` SI separa los dos ordenes. Ademas se pin la corrupcion real
    que el orden correcto existe para evitar: ninguna celda de ``quantity``
    queda en NaN despues del nivel 2 -- eso es precisamente lo que produce
    parsear "1.234,50" como numero europeo cuando el separador de miles
    europeo ya se aplico antes que el inyector semantico."""
    df = _clean_frame()
    l2 = apply_dirt(df, level=2, seed=0)
    qty = pd.to_numeric(
        l2["quantity"].astype(str).str.replace(".", "", regex=False).str.replace(",", ".", regex=False),
        errors="coerce",
    )
    assert qty.notna().mean() > 0.99
    assert qty.notna().all()


def test_level_two_degrades_intake_quality(tmp_path):
    w, outs = _run_weeks(n_weeks=16, n_skus=6)
    dirty = apply_dirt(history_frame(w, outs), level=2, seed=0)
    p = tmp_path / "dirty.csv"
    dirty.to_csv(p, index=False)
    _, quality = prepare_tracked(p)
    dropped = (
        quality.n_dropped_bad_date
        + quality.n_dropped_bad_quantity
        + quality.n_dropped_negative_quantity
    )
    assert dropped > 0


def test_no_injector_touches_lead_time_std():
    """Restriccion dura del review de Task 3: jobs/intake.py usa 0.0 como
    default cuando la columna falta, asi que un inyector que la vaciara
    seria numericamente invisible ahi -- pero IntakeQuality.synthesized (la
    senal que Task 8 lee) SI lo notaria, y si la corrupcion no fuera pareja
    entre niveles, las deltas de degradacion nivel-N vs nivel-0 dejan de ser
    comparables. Se corre en los cuatro niveles, no solo en el 2.

    ``history_frame`` escribe ``0.0`` como literal para TODAS las filas (no es
    un valor derivado por SKU), asi que comparar contra la constante original
    equivale a comparar celda a celda contra el frame limpio -- y evita que
    ``inject_calendar_gaps`` (que borra filas enteras a partir del nivel 2)
    rompa la comparacion por un simple desajuste de forma."""
    df = _clean_frame()
    assert (df["lead_time_std"].to_numpy() == 0.0).all()  # sanity: la constante que comparamos
    for level in LEVELS:
        dirty = apply_dirt(df, level=level, seed=0)
        assert "lead_time_std" in dirty.columns
        assert not dirty.empty
        assert (dirty["lead_time_std"].to_numpy() == 0.0).all()


def test_apply_dirt_is_deterministic_for_a_fixed_seed():
    """Misma semilla, misma suciedad, siempre -- si algun inyector usara
    aleatoriedad no gobernada por el `rng` recibido (p.ej. np.random global),
    dos corridas con seed=0 divergirian."""
    df = _clean_frame()
    for level in LEVELS:
        first = apply_dirt(df, level=level, seed=0)
        second = apply_dirt(df, level=level, seed=0)
        pd.testing.assert_frame_equal(first, second)


# --- Task 5: inyectores de suciedad nivel 3 (caotico) -----------------------


def test_duplicate_rows_inflates_row_count_and_quantity():
    df = _clean_frame()
    out = inject_duplicate_rows(df, np.random.default_rng(0))
    assert len(out) > len(df)
    assert out["quantity"].sum() > df["quantity"].sum()


def test_duplicate_rows_added_rows_are_exact_duplicates_of_existing_rows():
    """"Mas filas y mas cantidad total" (el assert del brief) tambien lo
    cumpliria un inyector que agregara filas BASURA con valores nuevos --
    eso no es "el archivo se exporto dos veces", que es especificamente que
    las filas de mas son copias exactas de filas que ya estaban. Se pin que
    el bloque original (las primeras ``len(df)`` filas) no cambia un bit, y
    que cada fila agregada coincide, celda a celda, con alguna fila del
    frame original -- no una fila nueva que por casualidad tenga sentido."""
    df = _clean_frame()
    out = inject_duplicate_rows(df, np.random.default_rng(0))
    assert len(out) > len(df)
    original_block = out.iloc[: len(df)].reset_index(drop=True)
    pd.testing.assert_frame_equal(original_block, df.reset_index(drop=True))
    extra = out.iloc[len(df):]
    original_rows = {tuple(row) for row in df.itertuples(index=False)}
    for row in extra.itertuples(index=False):
        assert tuple(row) in original_rows


def test_duplicate_rows_fallback_fires_when_the_draw_marks_no_row():
    """El fallback "al menos una" (``if not mask.any()``) es codigo muerto
    si ningun test lo ejecuta -- y con el 5% de tasa de muestreo sobre las
    72 filas de este fixture, el sorteo de la semilla 0 (la unica que usan
    las dos pruebas de arriba) SI marca filas, asi que nunca lo dispara.
    Medido directamente sobre las semillas 0..399: el fallback dispara para
    exactamente 10 de ellas, siendo la semilla 10 la primera. Se fija esa
    semilla y se pin la forma exacta del fallback: se duplica una unica
    fila (no cero, no mas de una)."""
    df = _clean_frame()
    rng = np.random.default_rng(10)
    mask = rng.random(len(df)) < 0.05
    assert not mask.any()  # sanity: esta semilla es realmente el sorteo vacio
    out = inject_duplicate_rows(df, np.random.default_rng(10))
    assert len(out) == len(df) + 1


def test_duplicate_rows_on_an_empty_frame_returns_it_unchanged():
    """El guard ``if out.empty: return out`` tampoco lo ejercita ningun test
    con el fixture estandar (72 filas nunca esta vacio). Sin este guard, un
    frame vacio pasado a `rng.random(0)` seguiria funcionando por
    casualidad, pero el guard existe a proposito -- se lo ejercita
    directamente."""
    empty = pd.DataFrame(
        columns=["date", "product_id", "quantity", "unit_cost", "lead_time_days", "lead_time_std"]
    )
    out = inject_duplicate_rows(empty, np.random.default_rng(0))
    assert out.empty
    assert list(out.columns) == list(empty.columns)


def test_zero_cost_zeroes_some_unit_costs_but_not_all():
    df = _clean_frame()
    out = inject_zero_cost(df, np.random.default_rng(0))
    assert (out["unit_cost"] == 0).any()
    assert (out["unit_cost"] > 0).any()


def test_zero_cost_only_changes_masked_unit_cost_rows_and_nothing_else():
    """Un `.any()` de cada lado sobre 72 filas es casi seguro pase lo que
    pase (incluso con un inyector que zafara al azar sin relacion con el
    sorteo real, o que ademas corrompiera otra columna). Se pin la
    transformacion sobre las filas que el propio resultado dice haber
    tocado: nunca fueron 0 en origen (el mundo genera unit_cost en
    ``[2, 90]``, ver ``world.py``), son una fraccion real de las filas (ni
    todas ni ninguna), ninguna otra columna se mueve en esas filas, y las
    filas NO tocadas conservan su unit_cost original intacto."""
    df = _clean_frame()
    out = inject_zero_cost(df, np.random.default_rng(0))
    touched = out["unit_cost"].to_numpy() == 0.0
    assert touched.any()
    assert touched.sum() < len(out)
    assert (df["unit_cost"].to_numpy()[touched] != 0.0).all()
    for col in ("date", "product_id", "quantity", "lead_time_days", "lead_time_std"):
        assert (out[col].to_numpy() == df[col].to_numpy()).all()
    assert (out["unit_cost"].to_numpy()[~touched] == df["unit_cost"].to_numpy()[~touched]).all()


def test_zero_cost_fallback_fires_when_the_draw_marks_no_row():
    """Mismo problema que el fallback de ``inject_duplicate_rows``, mas raro
    todavia: con 8% de tasa de muestreo sobre 72 filas, medido directamente
    sobre las semillas 0..399, el fallback dispara para una sola semilla
    (30) -- un orden de magnitud menos frecuente que el 5% de
    ``inject_duplicate_rows`` (10 semillas), porque la probabilidad de
    sorteo vacio cae mientras mas alta es la tasa por fila. Se fija esa
    semilla y se pin la forma exacta: se zera exactamente la primera fila,
    ninguna otra."""
    df = _clean_frame()
    rng = np.random.default_rng(30)
    mask = rng.random(len(df)) < 0.08
    assert not mask.any()  # sanity: esta semilla es realmente el sorteo vacio
    out = inject_zero_cost(df, np.random.default_rng(30))
    touched = out["unit_cost"].to_numpy() == 0.0
    assert touched.sum() == 1
    assert touched[0]


def test_zero_cost_on_an_empty_frame_returns_it_unchanged():
    empty = pd.DataFrame(
        columns=["date", "product_id", "quantity", "unit_cost", "lead_time_days", "lead_time_std"]
    )
    out = inject_zero_cost(empty, np.random.default_rng(0))
    assert out.empty
    assert list(out.columns) == list(empty.columns)


def test_renamed_supplier_splits_one_lead_time_into_two():
    df = _clean_frame()
    out = inject_renamed_supplier(df, np.random.default_rng(0))
    assert "supplier" in out.columns
    assert out["supplier"].nunique() > df.get(
        "supplier", pd.Series(["x"] * len(df))
    ).nunique()


def test_renamed_supplier_split_is_a_real_rename_at_a_date_boundary():
    """"nunique subio" (el assert del brief) tambien lo cumpliria una columna
    'supplier' con nombres al azar sin relacion con el lead time, o que
    rebautizara TODOS los proveedores en vez de uno solo -- ninguna de esas
    dos cosas es "un proveedor renombrado a mitad del historico". Se
    replica la logica del inyector (derivar supplier de lead_time_days,
    tomar el mas comun, cortar en la mediana de fechas) para pin: (1) antes
    del corte, el proveedor blanco conserva su nombre original; (2) despues
    del corte, ESE proveedor -- y solo ese -- pasa a "<original> SpA"; y (3)
    cualquier OTRO proveedor no cambia de nombre en ninguna fila."""
    df = _clean_frame()
    out = inject_renamed_supplier(df, np.random.default_rng(0))

    leads = pd.to_numeric(df["lead_time_days"], errors="coerce")
    derived = leads.map(lambda v: f"Prov-{int(v)}d" if pd.notna(v) else UNKNOWN_SUPPLIER)
    dates = sorted(df["date"].astype(str).unique())
    cut = dates[len(dates) // 2]
    target = derived.mode().iloc[0]

    before_cut = derived.eq(target) & (df["date"].astype(str) < cut)
    after_cut = derived.eq(target) & (df["date"].astype(str) >= cut)
    other_supplier = ~derived.eq(target)

    assert before_cut.any()
    assert after_cut.any()
    assert (out.loc[before_cut, "supplier"] == target).all()
    assert (out.loc[after_cut, "supplier"] == f"{target} SpA").all()
    assert (out.loc[other_supplier, "supplier"] == derived[other_supplier]).all()


def test_renamed_supplier_never_renames_the_unknown_bucket_on_the_production_path():
    """Las dos pruebas de arriba llaman al inyector directo sobre
    ``_clean_frame()``, donde ``lead_time_days`` nunca es NaN y
    ``UNKNOWN_SUPPLIER`` no existe. Pero ``inject_renamed_supplier`` vive en
    ``DIRT_BY_LEVEL[3]``, y ``apply_dirt`` siempre corre el nivel 2 antes --
    donde ``inject_missing_lead_time`` puede blanquear a NaN un grupo
    entero de lead times. Con solo dos valores de lead time distintos en
    este fixture, blanquear uno alcanza para volver ``UNKNOWN_SUPPLIER`` la
    moda de toda la columna: medido directamente, 6 de las semillas 0..7 --
    incluida la semilla 0, la unica que usa cada prueba de nivel 3 de este
    archivo -- renombraban el bucket desconocido en vez de un proveedor
    real antes del fix. El 100% de las invocaciones reales (que siempre
    pasan por el nivel 2 primero dentro de ``apply_dirt``) ejercitaban el
    0% de esa cobertura. Esta prueba corre el punto de entrada real de
    produccion, ``apply_dirt(df, level=3, seed=...)``, en las semillas 0..7
    y fija que el proveedor renombrado sea siempre uno real, nunca el
    bucket desconocido. (`inject_renamed_supplier` no toca `product_id`,
    `date` ni `quantity`, asi que la capa cosmetica del nivel 1 -- que
    corre despues, al final de `apply_dirt` -- no puede alterar la columna
    `supplier` que se inspecciona aca.)"""
    df = _clean_frame()
    for seed in range(8):
        dirty = apply_dirt(df, level=3, seed=seed)
        renamed = sorted(n for n in dirty["supplier"].unique() if n.endswith(" SpA"))
        assert renamed, f"seed {seed}: no se detecto ningun rename en el path de produccion"
        assert all(name != f"{UNKNOWN_SUPPLIER} SpA" for name in renamed), (
            f"seed {seed}: se renombro el bucket desconocido en vez de un proveedor real: {renamed}"
        )


def test_unmarked_promo_creates_a_spike_without_a_flag_column():
    """El assert literal del brief (``out["quantity"].max() > df["quantity"].max()``)
    es FALSO contra la implementacion de referencia con el fixture estandar
    de este archivo: medido directamente, con seed=0 ``rng.integers`` cae en
    la fecha 2025-03-17, donde las 6 SKUs vendieron exactamente 0 unidades
    esa semana -- 4x de cero sigue siendo cero, asi que el maximo global
    (47.0, en otra fecha) no se mueve y el assert original revienta. Ademas
    de invalidar la prueba tal como estaba escrita, es precisamente el no-op
    silencioso que la nota de asimetria del brief pide evitar, asi que se
    agrego un fallback a ``inject_unmarked_promo``: si la fecha sorteada no
    tiene ninguna fila con cantidad positiva, usa la primera fecha (orden
    ascendente) que si la tenga.

    Esta prueba reproduce esa MISMA logica de seleccion (sorteo + fallback)
    para saber de antemano cual fecha va a tocarse, y pin el invariante real:
    esa fecha sube a exactamente 4x (no "el maximo subio", que es una prueba
    indirecta y -- como acaba de demostrarse -- falsa para este seed/fixture),
    ninguna otra fecha se mueve un centavo, ninguna otra columna cambia, y no
    aparece ninguna columna 'promo'."""
    df = _clean_frame()
    dates = sorted(df["date"].astype(str).unique())
    qty_all = pd.to_numeric(df["quantity"], errors="coerce")
    rng = np.random.default_rng(0)
    week = dates[int(rng.integers(0, len(dates)))]
    if not (qty_all[df["date"].astype(str) == week] > 0).any():
        with_demand = [d for d in dates if (qty_all[df["date"].astype(str) == d] > 0).any()]
        week = with_demand[0]
    hit = df["date"].astype(str) == week

    out = inject_unmarked_promo(df, np.random.default_rng(0))

    assert "promo" not in out.columns
    assert hit.any()
    expected = (df.loc[hit, "quantity"] * 4.0).round(2)
    assert out.loc[hit, "quantity"].to_numpy() == pytest.approx(expected.to_numpy())
    assert out["quantity"].max() > df["quantity"].max()
    assert (out.loc[~hit, "quantity"].to_numpy() == df.loc[~hit, "quantity"].to_numpy()).all()
    for col in ("date", "product_id", "unit_cost", "lead_time_days", "lead_time_std"):
        assert (out[col].to_numpy() == df[col].to_numpy()).all()


def test_level_three_includes_level_two_and_one():
    """El marcador de nivel 1 del brief (" " en algun lugar de product_id) lo
    puede fabricar tambien ``inject_split_sku`` (nivel 2: "Sku 1005" tiene un
    espacio) -- exactamente el hallazgo que la Task 4 encontro en
    ``test_levels_accumulate``, y que el propio enunciado de esta tarea pide
    no repetir. Se usa en cambio la firma EXCLUSIVA de
    ``inject_whitespace_case``: espacio INICIAL y todo en minusculas.
    ``inject_split_sku`` capitaliza la primera letra de su grafia nueva
    (nunca produce algo enteramente minusculo con espacio al inicio), y
    ningun inyector de nivel 3 toca ``product_id`` en absoluto -- asi que
    esta firma solo la puede fabricar el nivel 1.

    Los otros dos marcadores si son exclusivos tal como estan: ninguna
    filas queda con ``quantity < 0`` salvo por ``inject_returns_as_negatives``
    (nivel 2 -- ningun inyector de nivel 1 o 3 niega una cantidad), y
    ``unit_cost`` nunca es exactamente 0 en el mundo generado (rango ``[2,
    90]``, ver ``world.py``) ni lo toca ningun inyector salvo
    ``inject_zero_cost`` (nivel 3)."""
    df = _clean_frame()
    l3 = apply_dirt(df, level=3, seed=0)
    ids = l3["product_id"].astype(str)
    cosmetic_signature = ids.str.startswith(" ") & (ids == ids.str.lower())
    assert cosmetic_signature.any()   # nivel 1 (firma exclusiva de inject_whitespace_case)
    assert (pd.to_numeric(l3["quantity"], errors="coerce") < 0).any()  # nivel 2
    assert (pd.to_numeric(l3["unit_cost"], errors="coerce") == 0).any()  # nivel 3


def test_dirt_is_reproducible_for_a_given_seed():
    df = _clean_frame()
    a = apply_dirt(df, level=3, seed=42)
    b = apply_dirt(df, level=3, seed=42)
    pd.testing.assert_frame_equal(a, b)
