"""Twin sembrado con el catalogo real: carga con tres estados, payloads por grupo,
consolidacion valorizada. Fixture: 6 productos reales de Mariscos De La Concha
(2026-08-28), 13 variantes."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from jobs import twin_catalogo as tc

FIXTURE = Path(__file__).parent / "fixtures" / "twin_catalogo" / "catalogo_mdlc_6.json"


def test_cargar_catalogo_cuenta_los_tres_estados_del_sku():
    """MAFI vive en dos variantes de distinto peso -> las dos son 'contradicho'
    y llevan clave MAFI#<variant_id>. Sin SKU -> 'no_declarado' con clave
    id:<variant_id>. Con SKU unico -> 'declarado' con clave = sku."""
    variantes, cob = tc.cargar_catalogo(FIXTURE)
    assert len(variantes) == 13
    assert cob.sku_contradicho == 2
    assert cob.sku_no_declarado == 8
    assert cob.sku_declarado == 3
    claves = {v.clave for v in variantes}
    assert "MAFI#47628018876704" in claves and "MAFI#47628018909472" in claves
    assert "SAS454" in claves and "ZCCC" in claves and "PINB" in claves
    assert "id:52204278513952" in claves
    assert all(v.estado_sku in (tc.ESTADO_DECLARADO, tc.ESTADO_NO_DECLARADO, tc.ESTADO_CONTRADICHO)
               for v in variantes)


def test_cobertura_de_recalcula_para_un_subconjunto_de_variantes():
    """cobertura_de es el helper que cargar_catalogo usa por dentro; debe dar
    el mismo resultado para el catalogo completo, y un resultado distinto
    (mas chico) para un subconjunto filtrado por grupo -- sin recalcular
    precio_excluido, que se declara aparte porque las excluidas ya no estan
    en la lista de variantes."""
    variantes, cob = tc.cargar_catalogo(FIXTURE)
    recalculada = tc.cobertura_de(variantes, precio_excluido=cob.precio_excluido)
    assert recalculada == cob

    pescados = tuple(v for v in variantes if v.grupo == "Pescados")
    c_pescados = tc.cobertura_de(pescados, precio_excluido=cob.precio_excluido)
    assert c_pescados.sku_declarado == 1
    assert c_pescados.sku_no_declarado == 7
    assert c_pescados.sku_contradicho == 2
    assert c_pescados.precio_declarado == 10
    assert c_pescados.precio_excluido == cob.precio_excluido


def test_cargar_catalogo_funde_los_grupos_chicos_en_otros():
    """Pescados tiene 10 variantes y queda; Mariscos (1), Cerdo (1) y Otros (1)
    no llegan al minimo de 5 y se funden en 'Otros'."""
    variantes, cob = tc.cargar_catalogo(FIXTURE)
    assert dict(cob.grupos) == {"Pescados": 10, tc.GRUPO_OTROS: 3}
    assert {v.grupo for v in variantes if v.sku == "ZCCC"} == {tc.GRUPO_OTROS}


def test_cargar_catalogo_declara_precio_y_quiebre_de_hoy():
    variantes, cob = tc.cargar_catalogo(FIXTURE)
    assert cob.precio_declarado == 13 and cob.precio_excluido == 0
    assert cob.quiebre_hoy == 7   # MAFI 0.4/0.5/0.6 kg + Salmon filete x4
    mafi_1kg = next(v for v in variantes if v.clave == "MAFI#47628018909472")
    assert mafi_1kg.precio == 14450.0 and mafi_1kg.disponible is True


def test_cargar_catalogo_excluye_y_cuenta_la_variante_sin_precio(tmp_path):
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    data[0]["variants"][0]["price"] = "0"
    p = tmp_path / "cat.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    variantes, cob = tc.cargar_catalogo(p)
    assert len(variantes) == 12 and cob.precio_excluido == 1


def test_cargar_catalogo_levanta_valueerror_nombrando_el_campo_que_falta(tmp_path):
    p = tmp_path / "malo.json"
    p.write_text(json.dumps([{"id": 1, "title": "x", "product_type": "Pescados"}]), encoding="utf-8")
    with pytest.raises(ValueError, match="variants"):
        tc.cargar_catalogo(p)


def test_armar_payloads_una_red_por_grupo_y_dos_payloads_por_grupo():
    """Pescados y Otros -> 2 grupos. Pescados tiene disponibles y quebradas -> 2
    payloads; Otros solo disponibles -> 1. Cada red: PROVEEDOR-<grupo> ->
    CD-MDLC -> TIENDA-WEB."""
    variantes, _ = tc.cargar_catalogo(FIXTURE)
    pls = tc.armar_payloads(variantes, hipotesis=tc.Hipotesis(), escenario=tc.ESCENARIOS_ESTRENO[0])
    assert [(p.grupo, p.disponibles) for p in pls] == [
        (tc.GRUPO_OTROS, True), ("Pescados", False), ("Pescados", True),
    ]
    pesc = next(p for p in pls if p.grupo == "Pescados" and p.disponibles)
    assert [n["name"] for n in pesc.payload["nodes"]] == ["PROVEEDOR-Pescados", "CD-MDLC", "TIENDA-WEB"]
    assert [n["kind"] for n in pesc.payload["nodes"]] == ["supplier", "dc", "store"]
    assert pesc.payload["nodes"][1]["lead_time"] == 7 and pesc.payload["nodes"][2]["lead_time"] == 1
    assert pesc.payload["periods"] == 364 and pesc.payload["auto_size_factor"] == 1.0


def test_armar_payloads_las_quebradas_de_hoy_arrancan_vacias():
    variantes, _ = tc.cargar_catalogo(FIXTURE)
    pls = tc.armar_payloads(variantes, hipotesis=tc.Hipotesis(), escenario=tc.ESCENARIOS_ESTRENO[0])
    quebradas = next(p for p in pls if p.grupo == "Pescados" and not p.disponibles)
    sanas = next(p for p in pls if p.grupo == "Pescados" and p.disponibles)
    assert [n.get("initial_on_hand") for n in quebradas.payload["nodes"]] == [None, 0.0, 0.0]
    assert [n.get("initial_on_hand") for n in sanas.payload["nodes"]] == [None, None, None]
    assert {p["product_id"] for p in quebradas.payload["products"]} == {
        "id:50967838097696", "MAFI#47628018876704", "id:50967838130464",
        "id:51910435635488", "id:51910435799328", "id:51910435832096", "id:51910435864864",
    }


def test_armar_payloads_la_demanda_es_un_prior_declarado_y_determinista():
    """Misma semilla -> mismos payloads; distinta semilla -> distintas bases.
    La base media por grupo es la de la hipotesis (escala ABC lognormal)."""
    variantes, _ = tc.cargar_catalogo(FIXTURE)
    hip = tc.Hipotesis(base_demanda_por_grupo=8.0)
    a = tc.armar_payloads(variantes, hipotesis=hip, escenario=tc.ESCENARIOS_ESTRENO[0], seed=1)
    b = tc.armar_payloads(variantes, hipotesis=hip, escenario=tc.ESCENARIOS_ESTRENO[0], seed=1)
    c = tc.armar_payloads(variantes, hipotesis=hip, escenario=tc.ESCENARIOS_ESTRENO[0], seed=2)
    assert [p.payload for p in a] == [p.payload for p in b]
    assert [p.payload["products"] for p in a] != [p.payload["products"] for p in c]
    bases = [p["base"] for p in a[2].payload["products"]]
    assert all(x > 0 for x in bases)
    assert a[2].payload["demand"]["noise_cv"] == 0.25 and a[2].payload["demand"]["season_period"] == 364


def test_armar_payloads_el_corte_de_proveedor_apunta_al_proveedor_del_grupo():
    variantes, _ = tc.cargar_catalogo(FIXTURE)
    corte = next(e for e in tc.ESCENARIOS_ESTRENO if e.kind == "supplier_outage")
    pls = tc.armar_payloads(variantes, hipotesis=tc.Hipotesis(), escenario=corte)
    pesc = next(p for p in pls if p.grupo == "Pescados" and p.disponibles)
    d = pesc.payload["disruptions"]
    assert d == [{"kind": "supplier_outage", "target": "PROVEEDOR-Pescados", "start": 90,
                  "duration": 42, "magnitude": 2.0}]
    pico = next(e for e in tc.ESCENARIOS_ESTRENO if e.kind == "demand_surge")
    pls2 = tc.armar_payloads(variantes, hipotesis=tc.Hipotesis(), escenario=pico)
    assert pls2[2].payload["disruptions"][0]["target"] == "TIENDA-WEB"


def test_armar_payloads_respeta_el_techo_de_productos():
    variantes, _ = tc.cargar_catalogo(FIXTURE)
    muchas = tuple(
        tc.Variante(clave=f"X{i}", sku=f"X{i}", variant_id=i, producto="x", grupo="Pescados",
                    precio=1.0, disponible=True, estado_sku=tc.ESTADO_DECLARADO)
        for i in range(251)
    )
    with pytest.raises(ValueError, match="250"):
        tc.armar_payloads(muchas, hipotesis=tc.Hipotesis(), escenario=tc.ESCENARIOS_ESTRENO[0])


def test_armar_payloads_el_seed_del_payload_tambien_se_desplaza():
    """El seed a nivel payload debe desplazarse igual que el de _productos:
    seed + 1000*i + int(disponibles). Antes quedaba fijo en `seed`, lo que
    desalineaba el seed reportado del que realmente genero los productos
    (aunque el determinismo por semilla ya pasaba, porque i y disponibles
    tambien determinan el payload)."""
    variantes, _ = tc.cargar_catalogo(FIXTURE)
    pls = tc.armar_payloads(variantes, hipotesis=tc.Hipotesis(), escenario=tc.ESCENARIOS_ESTRENO[0], seed=42)
    grupos_ordenados = sorted({v.grupo for v in variantes})
    for p in pls:
        i = grupos_ordenados.index(p.grupo)
        assert p.payload["seed"] == 42 + 1000 * i + int(p.disponibles)


def _reporte(escenario=None, seed=7):
    variantes, cob = tc.cargar_catalogo(FIXTURE)
    hip = tc.Hipotesis(periods=120)
    esc = escenario or tc.ESCENARIOS_ESTRENO[0]
    pls = tc.armar_payloads(variantes, hipotesis=hip, escenario=esc, seed=seed)
    res = tc.correr(pls)
    return tc.consolidar(res, variantes, cobertura=cob, hipotesis=hip), variantes


def test_consolidar_valoriza_a_precio_de_lista_y_solo_eso():
    """venta_en_riesgo = sum(unidades no servidas x precio de la variante).
    Se verifica a mano contra las filas del twin: si se cambiara el precio por
    otro campo, o se sumara algo mas, este numero no cuadra."""
    rep, variantes = _reporte()
    precio = {v.clave: v.precio for v in variantes}
    demand = {(t, p, s): u for t, p, s, u in rep.demand_rows}
    a_mano = sum(
        (demand[(t, p, s)] - u) * precio[p] for t, p, s, u in rep.served_rows
    )
    assert rep.venta_en_riesgo_clp == pytest.approx(a_mano, rel=1e-6)
    assert rep.venta_en_riesgo_clp >= 0.0
    assert dict(rep.venta_en_riesgo_por_grupo).keys() == {"Pescados", tc.GRUPO_OTROS}


def test_consolidar_reporta_fill_y_quiebre_por_grupo_y_las_mas_golpeadas():
    rep, _ = _reporte(tc.ESCENARIOS_ESTRENO[1])   # corte de proveedor
    fills = dict(rep.fill_por_grupo)
    assert set(fills) == {"Pescados", tc.GRUPO_OTROS}
    assert all(0.0 <= f <= 1.0 for f in fills.values())
    # I3: el nombre viejo (dias_quiebre_por_grupo) mentia -- es una suma sobre
    # variantes, no sobre dias de calendario (1976 sobre un horizonte de 120
    # dias en la corrida real). El campo se llama variante_dias_quiebre_por_grupo.
    assert all(d >= 0 for _, d in rep.variante_dias_quiebre_por_grupo)
    assert not hasattr(rep, "dias_quiebre_por_grupo")
    assert 1 <= len(rep.golpeadas) <= 10
    assert rep.golpeadas == tuple(sorted(rep.golpeadas, key=lambda g: -g.venta_en_riesgo_clp))
    assert all(g.estado_sku in (tc.ESTADO_DECLARADO, tc.ESTADO_NO_DECLARADO, tc.ESTADO_CONTRADICHO)
               for g in rep.golpeadas)


def test_consolidar_desglosa_por_estado_de_arranque_c2():
    """C2 / decision 5 del spec: el reporte separa cuanto tardan en
    recuperarse las quebradas de HOY (arrancaron con initial_on_hand=0) de
    cuanto se quiebran las sanas. Sobre el escenario de corte de proveedor
    hay payloads sanos y quebrados en Pescados, y solo sanos en Otros (ver
    test_armar_payloads_una_red_por_grupo_y_dos_payloads_por_grupo)."""
    rep, variantes = _reporte(tc.ESCENARIOS_ESTRENO[1])
    pares = {(g, vacia) for g, vacia, *_ in rep.desglose_por_estado}
    # Otros solo tiene payload de disponibles (arranco_vacia=False); Pescados
    # tiene ambos (ver test_armar_payloads_una_red_por_grupo_y_dos_payloads_por_grupo).
    assert pares == {("Pescados", False), ("Pescados", True), (tc.GRUPO_OTROS, False)}
    for _, _, fill, dias, venta in rep.desglose_por_estado:
        assert 0.0 <= fill <= 1.0
        assert dias >= 0
        assert venta >= 0.0

    por_clave = {v.clave: v for v in variantes}
    for g in rep.golpeadas:
        if not por_clave[g.clave].disponible:
            assert g.arranco_vacia is True


def test_deck_c2_muestra_quebradas_de_hoy_vs_sanas():
    rep, _ = _reporte(tc.ESCENARIOS_ESTRENO[1])
    md = tc.deck(rep, lectura="lectura 2026-08-28").to_markdown()
    assert "Quebradas de hoy vs sanas" in md


def test_consolidar_reporta_n_variantes_realmente_simuladas():
    """n_variantes cuenta los product_id distintos que de verdad pasaron por
    el twin (via demand_rows), no la cobertura del catalogo completo -- son
    lo mismo en una corrida sobre todo el catalogo; el caso donde DIFIEREN
    (un grupo filtrado, mientras la cobertura sigue siendo la del catalogo
    completo) es exactamente el defecto C1 y lo cubre el test del example
    mas abajo."""
    rep, variantes = _reporte()
    assert rep.n_variantes == len(variantes) == 13


def test_consolidar_es_determinista_por_semilla():
    a, _ = _reporte(seed=3)
    b, _ = _reporte(seed=3)
    c, _ = _reporte(seed=4)
    assert a.venta_en_riesgo_clp == b.venta_en_riesgo_clp and a.fill_por_grupo == b.fill_por_grupo
    assert (a.venta_en_riesgo_clp, a.fill_por_grupo) != (c.venta_en_riesgo_clp, c.fill_por_grupo)


def test_consolidar_las_filas_llevan_la_clave_real_de_la_variante():
    rep, variantes = _reporte()
    claves = {v.clave for v in variantes}
    assert {p for _, p, _, _ in rep.demand_rows} == claves
    assert {p for _, p, _, _ in rep.inventory_rows} <= claves


def test_deck_abre_con_la_frase_de_hipotesis_y_declara_cobertura_y_priors():
    rep, _ = _reporte()
    d = tc.deck(rep, lectura="lectura 2026-08-28")
    md = d.to_markdown()
    assert md.startswith("# ") and tc.FRASE_APERTURA in d.summary
    assert "Hipótesis de demanda (no observadas)" in md
    assert "declarado" in md and "no_declarado" in md and "contradicho" in md
    assert "venta en riesgo a precio de lista" in md.lower()
    assert "proveedor: no declarado" in md.lower()
    assert "Cobertura · SKU" in md
    assert "declarado=3 · no_declarado=8 · contradicho=2" in md
    assert "## " not in "\n".join(line for line in md.splitlines() if line.startswith("- "))


def test_deck_m6_el_titulo_no_repite_el_cliente():
    """M6: Deliverable.to_markdown ya le agrega '- {client}' al titulo
    (src/deliverable.py); si deck() tambien lo mete en el titulo, el
    renderizado repite el nombre del cliente dos veces."""
    rep, _ = _reporte()
    d = tc.deck(rep, lectura="lectura 2026-08-28")
    assert d.title == f"Twin del catálogo — escenario {rep.escenario.nombre}"
    assert d.client not in d.title


def test_deck_m7_el_cliente_por_defecto_es_client():
    rep, _ = _reporte()
    d = tc.deck(rep, lectura="lectura 2026-08-28")
    assert d.client == "Client"


def test_deck_i5_la_procedencia_no_tiene_default_y_se_imprime(tmp_path):
    """I5: antes deck() escribia 'lectura 2026-08-28' fijo sin importar
    cuando se leyo el catalogo de verdad. Ahora `lectura` es un parametro
    obligatorio (sin default -- una procedencia faltante no se inventa
    sola) y aparece tal cual en el markdown."""
    rep, _ = _reporte()
    with pytest.raises(TypeError):
        tc.deck(rep)   # falta el kwarg obligatorio `lectura`
    md = tc.deck(rep, lectura="lectura 2099-01-01").to_markdown()
    assert "lectura 2099-01-01" in md


def test_escribir_deja_los_csv_del_twin_con_fecha_diaria_y_clave_real(tmp_path):
    rep, variantes = _reporte()
    paths = tc.escribir(rep, tmp_path, lectura="lectura 2026-08-28")
    assert set(paths) == {"demand_history", "served", "inventory", "orders", "cobertura", "golpeadas", "deck"}
    import pandas as pd
    dem = pd.read_csv(paths["demand_history"])
    assert list(dem.columns) == ["date", "product_id", "location", "units"]
    assert dem["date"].iloc[0] == "2025-01-06" and dem["date"].iloc[1] == "2025-01-07"
    assert set(dem["product_id"]) == {v.clave for v in variantes}
    assert paths["deck"].read_text(encoding="utf-8").startswith("# ")


def test_escribir_m9_los_archivos_llevan_el_prefijo_twin_catalogo(tmp_path):
    """M9: los nombres de archivo llevan el prefijo `twin_catalogo_` (para no
    colisionar con los `twin_*.csv` que jobs/digital_twin_job.py ya escribe
    en el mismo `deliverables/`); las CLAVES del dict devuelto no cambian."""
    rep, _ = _reporte()
    paths = tc.escribir(rep, tmp_path, lectura="lectura 2026-08-28")
    assert paths["demand_history"].name == "twin_catalogo_demand_history.csv"
    assert paths["served"].name == "twin_catalogo_served.csv"
    assert paths["inventory"].name == "twin_catalogo_inventory.csv"
    assert paths["orders"].name == "twin_catalogo_orders.csv"
    assert paths["cobertura"].name == "twin_catalogo_cobertura.csv"
    assert paths["golpeadas"].name == "twin_catalogo_golpeadas.csv"
    assert paths["deck"].name == "twin_catalogo_deck.md"
    for p in paths.values():
        assert p.exists()


def test_escribir_i4_defusa_formulas_en_product_id_y_location(tmp_path):
    """I4: _con_fecha escribia product_id/location crudos via to_csv,
    saltandose la defensa contra CSV-injection que el resto del repo aplica
    (src/sanitize.defuse_formula, ya usado por write_summary_csv). Un
    product_id que empiece con '=' debe llegar defuseado a la celda."""
    rep, variantes = _reporte()
    maligno = "=1+1;cmd.exe"
    v0 = variantes[0]
    demand_malo = tuple(
        (t, maligno, s, u) if p == v0.clave else (t, p, s, u) for t, p, s, u in rep.demand_rows
    )
    rep_malo = tc.CatalogTwinReport(
        escenario=rep.escenario, hipotesis=rep.hipotesis, cobertura=rep.cobertura,
        n_variantes=rep.n_variantes, fill_por_grupo=rep.fill_por_grupo,
        variante_dias_quiebre_por_grupo=rep.variante_dias_quiebre_por_grupo,
        desglose_por_estado=rep.desglose_por_estado,
        venta_en_riesgo_clp=rep.venta_en_riesgo_clp,
        venta_en_riesgo_por_grupo=rep.venta_en_riesgo_por_grupo, golpeadas=rep.golpeadas,
        demand_rows=demand_malo, served_rows=rep.served_rows,
        inventory_rows=rep.inventory_rows, order_rows=rep.order_rows,
    )
    paths = tc.escribir(rep_malo, tmp_path, lectura="lectura 2026-08-28")
    import pandas as pd
    dem = pd.read_csv(paths["demand_history"])
    celdas = dem["product_id"].astype(str)
    assert not any(c.startswith("=") for c in celdas)
    assert any(c.startswith("'=") or "1+1" in c for c in celdas)


def test_example_corre_los_cuatro_escenarios_sobre_la_fixture(tmp_path, capsys):
    import examples.run_twin_catalogo as cli
    rc = cli.main(["--catalogo", str(FIXTURE), "--grupo", "Pescados", "--escenario", "todos",
                   "--out", str(tmp_path), "--periodos", "60", "--seed", "1"])
    assert rc == 0
    out = capsys.readouterr().out
    for nombre in ("base", "corte_proveedor", "plazo_estirado", "pico_demanda"):
        assert nombre in out
        assert (tmp_path / nombre / "twin_catalogo_deck.md").exists()
    assert "venta en riesgo" in out.lower()
    # I3: el header de consola no puede seguir llamando "dias" a una suma
    # sobre variantes.
    assert "var-dias quiebre" in out


def test_example_filtrado_por_grupo_no_sobrestima_el_alcance_en_el_deck(tmp_path):
    """C1: si el example filtra `variantes` por --grupo pero le pasa al deck
    la cobertura del catalogo completo, el deck dice "sobre 216 variantes"
    (o, en la fixture, el total del catalogo) y muestra filas de cobertura
    del catalogo completo, mientras que solo el grupo filtrado (Pescados,
    10 variantes) fue realmente simulado. El deck debe reflejar SOLO lo
    simulado."""
    import examples.run_twin_catalogo as cli
    rc = cli.main(["--catalogo", str(FIXTURE), "--grupo", "Pescados", "--escenario", "todos",
                   "--out", str(tmp_path), "--periodos", "60", "--seed", "1"])
    assert rc == 0
    md = (tmp_path / "base" / "twin_catalogo_deck.md").read_text(encoding="utf-8")
    assert "sobre 10 variantes" in md
    assert "sobre 13 variantes" not in md
    assert "declarado=1 · no_declarado=7 · contradicho=2" in md


def test_example_imprime_solo_ascii_en_consola(tmp_path, capsys):
    """La consola de Windows (cp1252) revienta con acentos: todo lo que el
    example imprime es ASCII, aunque los archivos de salida sigan en UTF-8."""
    import examples.run_twin_catalogo as cli
    assert cli._ascii("Frutas y Verduras · café") == "Frutas y Verduras ? caf?"
    rc = cli.main(["--catalogo", str(FIXTURE), "--grupo", "todos", "--escenario", "base",
                   "--out", str(tmp_path), "--periodos", "40", "--seed", "1"])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.isascii(), out
    assert "| en quiebre hoy=" in out
