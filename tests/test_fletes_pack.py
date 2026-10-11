"""El pack fletes completo: contrato del Tool, entregables, opciones y un caso integral."""

from __future__ import annotations

import csv
from dataclasses import replace
from decimal import Decimal

from _fletes_fixtures import lote

from nosilo_core.guided import HANDOFF, OPTIONS, verify_guided
from nosilo_core.registry import ToolRegistry
from nosilo_core.types import STATUS_NEEDS_CLARIFICATION, STATUS_NEEDS_DATA, STATUS_OK, JobRequest
from packs.fletes import build_fletes_tools, register
from packs.fletes.engine import (
    RUTA_MENOS_DE_DOS,
    RUTA_SIN_ALTERNATIVA,
    Comparacion,
    Pesos,
)
from packs.fletes.tool import CARRIER_LANE_COMPARE, deliver, options, prepare, qa, run

HEADER = "envio_id,transportista,origen,destino,fecha_despacho,costo,moneda,kg,fecha_prometida,fecha_entrega"


def _csv(tmp_path, envios) -> str:
    path = tmp_path / "envios.csv"
    lines = [HEADER]
    for e in envios:
        lines.append(",".join([
            e.envio_id, e.transportista, e.origen, e.destino, e.fecha_despacho.isoformat(),
            str(e.costo), e.moneda, str(e.kg),
            e.fecha_prometida.isoformat() if e.fecha_prometida else "",
            e.fecha_entrega.isoformat() if e.fecha_entrega else "",
        ]))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def _caso_integral():
    """3 rutas, 5 transportistas, 60 envios.

    Santiago -> Concepcion: Rapido (actual, 15 env, 1.5/kg, OTD 0.8) vs Lento (12 env, 1.2/kg, OTD 1.0)
        => recomienda Lento.
    Santiago -> Antofagasta: solo Norte => sin_alternativa.
    Valparaiso -> Santiago: Costa (actual, 12 env x 50 kg, 2.0/kg) vs Express (11 env, solo 8 fechados)
        => menos_de_dos_elegibles.
    """
    return (
        lote("Rapido", 15, a_tiempo=12, costo="150", paso=4)
        + lote("Lento", 12, costo="120", paso=4)
        + lote("Norte", 10, destino="Antofagasta", paso=3)
        + lote("Costa", 12, origen="Valparaiso", destino="Santiago", costo="100", kg="50", paso=2)
        + lote("Express", 11, fechados=8, origen="Valparaiso", destino="Santiago", costo="90", kg="50", paso=2)
    )


def _prepared(tmp_path, envios=None, **params):
    request = JobRequest(brief="comparar transportistas", data_path=_csv(tmp_path, envios or _caso_integral()),
                         params=params)
    return prepare(request, provider=None)


def _comparacion(tmp_path, envios=None) -> Comparacion:
    prepared = _prepared(tmp_path, envios)
    assert prepared.status == STATUS_OK, prepared.messages
    return run(prepared.payload, {}).report


def _informe(tmp_path, comp) -> str:
    return deliver(comp, tmp_path / "out", "Cliente X")["informe"].read_text(encoding="utf-8")


def test_se_registra_con_una_llamada() -> None:
    registry = register(ToolRegistry())
    assert [t.key for t in registry.list()] == ["carrier_lane_compare"]
    assert registry.get("carrier_lane_compare").requires_data


def test_declara_su_contrato_de_columnas() -> None:
    assert "transportista" in CARRIER_LANE_COMPARE.required_columns
    assert CARRIER_LANE_COMPARE.optional_columns == ("fecha_prometida", "fecha_entrega")


def test_un_brief_en_castellano_rutea_al_tool() -> None:
    ranked = build_fletes_tools().match("quiero comparar transportistas por ruta y ver el costo de flete")
    assert ranked[0][0].key == "carrier_lane_compare"
    assert ranked[0][1] > 0  # con un solo tool en el registry, el puesto 1 no prueba nada sin el puntaje


def test_un_brief_con_tilde_tambien_rutea() -> None:
    ranked = build_fletes_tools().match("necesito una comparación de transportistas")
    assert ranked[0][1] > 0


def test_un_brief_en_ingles_rutea_al_tool() -> None:
    ranked = build_fletes_tools().match("compare carriers by freight cost per lane")
    assert ranked[0][0].key == "carrier_lane_compare"
    assert ranked[0][1] > 0


def test_un_brief_ajeno_no_rutea() -> None:
    ranked = build_fletes_tools().match("calcular el punto de reorden de mis productos")
    assert ranked[0][1] == 0


def test_prepare_sin_archivo_pide_datos() -> None:
    prepared = prepare(JobRequest(brief="comparar transportistas"), provider=None)
    assert prepared.status == STATUS_NEEDS_DATA
    assert "envio_id" in prepared.messages[0]


def test_prepare_devuelve_el_error_exacto_del_loader(tmp_path) -> None:
    path = tmp_path / "envios.csv"
    path.write_text(HEADER + "\nE1,A,S,C,2026-01-05,abc,CLP,10,,\n", encoding="utf-8")
    prepared = prepare(JobRequest(brief="x", data_path=str(path)), provider=None)
    assert prepared.status == STATUS_NEEDS_DATA
    assert "fila 2, columna costo" in prepared.messages[0]


def test_pesos_y_min_envios_vienen_de_params(tmp_path) -> None:
    prepared = _prepared(tmp_path, peso_costo="0.3", peso_servicio="0.7", min_envios=5)
    assert prepared.status == STATUS_OK
    assert prepared.payload["pesos"] == Pesos(Decimal("0.3"), Decimal("0.7"))
    assert prepared.payload["min_envios"] == 5


def test_pesos_que_no_suman_uno_piden_aclaracion(tmp_path) -> None:
    prepared = _prepared(tmp_path, peso_costo="0.5")
    assert prepared.status == STATUS_NEEDS_CLARIFICATION
    assert "sumar 1" in prepared.messages[0]


def test_parametros_ilegibles_piden_aclaracion(tmp_path) -> None:
    assert _prepared(tmp_path, peso_costo="mucho", peso_servicio="0.4").status == STATUS_NEEDS_CLARIFICATION
    assert _prepared(tmp_path, min_envios=0).status == STATUS_NEEDS_CLARIFICATION
    assert _prepared(tmp_path, min_envios="diez").status == STATUS_NEEDS_CLARIFICATION


def test_caso_integral_recomienda_lo_esperado(tmp_path) -> None:
    comp = _comparacion(tmp_path)
    assert comp.envios == 60
    por_ruta = {r.ruta: r for r in comp.rutas}
    assert por_ruta["Santiago -> Antofagasta"].motivo == RUTA_SIN_ALTERNATIVA
    assert por_ruta["Valparaiso -> Santiago"].motivo == RUTA_MENOS_DE_DOS
    (rec,) = comp.recomendaciones
    assert (rec.ruta, rec.actual.transportista, rec.mejor.transportista) == (
        "Santiago -> Concepcion", "Rapido", "Lento")
    esperado_periodo = (Decimal(150) / Decimal(100) - Decimal(120) / Decimal(100)) * Decimal(15 * 100)
    assert rec.ahorro_periodo == esperado_periodo
    dias = 14 * 4 + 1  # Rapido va del dia 0 al 56
    assert rec.ahorro_anual == esperado_periodo * Decimal(365) / Decimal(dias)


def test_run_resume_la_corrida(tmp_path) -> None:
    prepared = _prepared(tmp_path)
    produced = run(prepared.payload, {})
    assert "3 ruta" in produced.summary
    assert "1 recomendacion" in produced.summary


def test_qa_pasa_el_caso_integral(tmp_path) -> None:
    assert qa(_comparacion(tmp_path)) == []


def test_qa_veta_cuando_ninguna_ruta_es_comparable(tmp_path) -> None:
    comp = _comparacion(tmp_path, lote("A", 12) + lote("B", 12, destino="Antofagasta"))
    issues = qa(comp)
    assert issues and "problema de dato" in issues[0]


def test_qa_detecta_kg_que_no_cuadran(tmp_path) -> None:
    comp = _comparacion(tmp_path)
    roto = replace(comp, kg_total=comp.kg_total + 1)
    assert any("kg" in issue for issue in qa(roto))


def test_deliver_escribe_matriz_e_informe(tmp_path) -> None:
    comp = _comparacion(tmp_path)
    out = deliver(comp, tmp_path / "out", "Cliente X")
    assert set(out) == {"matriz", "informe"}
    with out["matriz"].open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 5
    express = next(r for r in rows if r["transportista"] == "Express")
    assert express["elegible"] == "no" and express["motivo"] == "pocos_envios"
    assert express["puntaje"] == ""          # vacio, nunca 0
    lento = next(r for r in rows if r["transportista"] == "Lento")
    assert lento["peso_costo"] == "0.6" and lento["peso_servicio"] == "0.4"
    assert lento["indice_costo"] and lento["indice_servicio"] and lento["puntaje"]
    informe = out["informe"].read_text(encoding="utf-8")
    assert "Cliente X" in informe
    assert "Santiago -> Concepcion" in informe and "Lento" in informe
    assert "OTD" in informe and "OTIF" not in informe.replace("no OTIF", "")
    assert "sin alternativa" in informe
    assert "al año" in informe and "/ano" not in informe  # "ano" en castellano es otra palabra


def test_el_informe_anota_el_transportista_con_costo_cero(tmp_path) -> None:
    informe = _informe(tmp_path, _comparacion(tmp_path, lote("A", 12, costo="0") + lote("B", 12, costo="100")))
    assert "## Avisos" in informe
    assert "**Santiago -> Concepcion / A**" in informe
    assert "indice de costo 1" in informe


def test_sin_costo_cero_el_informe_no_trae_avisos(tmp_path) -> None:
    assert "## Avisos" not in _informe(tmp_path, _comparacion(tmp_path))


def test_el_informe_explica_que_un_empate_no_se_mueve(tmp_path) -> None:
    informe = _informe(tmp_path, _comparacion(tmp_path, lote("Beta", 15) + lote("Alfa", 12)))
    assert "empata" in informe


def test_options_ofrece_las_recomendaciones(tmp_path) -> None:
    outcome = options(_comparacion(tmp_path))
    assert outcome.status == OPTIONS
    assert "Lento" in outcome.options[0].label
    assert verify_guided(outcome) == []


def test_sin_recomendaciones_entrega_un_handoff_que_cumple_el_contrato(tmp_path) -> None:
    comp = _comparacion(tmp_path, lote("A", 15, costo="100") + lote("B", 12, costo="150"))
    outcome = options(comp)
    assert outcome.status == HANDOFF
    assert verify_guided(outcome) == []
