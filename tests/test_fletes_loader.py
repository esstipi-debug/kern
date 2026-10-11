"""Contrato del CSV de envios: lo que se lee, lo que se rechaza y con que mensaje."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from packs.fletes.loader import LoadError, load_envios, name_key, normalize_name, parse_envios

HEADER = "envio_id,transportista,origen,destino,fecha_despacho,costo,moneda,kg,fecha_prometida,fecha_entrega"
ROW = "E1,Rapido,Santiago,Concepcion,2026-01-05,150,CLP,100,2026-01-07,2026-01-07"


def _text(*rows: str, header: str = HEADER) -> str:
    return "\n".join([header, *rows]) + "\n"


def test_lee_una_fila_valida_con_tipos() -> None:
    (envio,) = parse_envios(_text(ROW))
    assert envio.envio_id == "E1"
    assert envio.transportista == "Rapido"
    assert envio.origen == "Santiago"
    assert envio.destino == "Concepcion"
    assert envio.fecha_despacho == date(2026, 1, 5)
    assert envio.costo == Decimal("150")
    assert envio.moneda == "CLP"
    assert envio.kg == Decimal("100")
    assert envio.fecha_prometida == date(2026, 1, 7)
    assert envio.fecha_entrega == date(2026, 1, 7)


def test_fechas_opcionales_en_blanco_son_none() -> None:
    (envio,) = parse_envios(_text("E1,Rapido,Santiago,Concepcion,2026-01-05,150,CLP,100,,"))
    assert envio.fecha_prometida is None
    assert envio.fecha_entrega is None


def test_columnas_opcionales_ausentes_son_none() -> None:
    header = "envio_id,transportista,origen,destino,fecha_despacho,costo,moneda,kg"
    (envio,) = parse_envios(_text("E1,Rapido,Santiago,Concepcion,2026-01-05,150,CLP,100", header=header))
    assert envio.fecha_prometida is None and envio.fecha_entrega is None


def test_falta_columna_obligatoria_la_nombra() -> None:
    header = "envio_id,transportista,origen,destino,fecha_despacho,moneda,kg"
    with pytest.raises(LoadError, match="costo"):
        parse_envios(_text("E1,Rapido,Santiago,Concepcion,2026-01-05,CLP,100", header=header))


def test_encabezado_ignora_mayusculas_espacios_y_columnas_extra() -> None:
    header = " Envio_ID ,Transportista,origen,destino,fecha_despacho, Costo ,moneda,kg,comentario"
    (envio,) = parse_envios(_text("E1,Rapido,Santiago,Concepcion,2026-01-05,150,CLP,100,nada", header=header))
    assert envio.costo == Decimal("150")


def test_coma_decimal_se_rechaza_con_la_regla() -> None:
    with pytest.raises(LoadError, match=r"fila 2, columna costo:.*punto decimal"):
        parse_envios(_text('E1,Rapido,Santiago,Concepcion,2026-01-05,"1.234,50",CLP,100,,'))


def test_nan_e_inf_se_rechazan() -> None:
    with pytest.raises(LoadError, match="fila 2, columna costo"):
        parse_envios(_text("E1,Rapido,Santiago,Concepcion,2026-01-05,nan,CLP,100,,"))
    with pytest.raises(LoadError, match="fila 2, columna kg"):
        parse_envios(_text("E1,Rapido,Santiago,Concepcion,2026-01-05,10,CLP,inf,,"))


def test_kg_cero_y_costo_negativo_se_rechazan() -> None:
    with pytest.raises(LoadError, match="fila 2, columna kg"):
        parse_envios(_text("E1,Rapido,Santiago,Concepcion,2026-01-05,150,CLP,0,,"))
    with pytest.raises(LoadError, match="fila 2, columna costo"):
        parse_envios(_text("E1,Rapido,Santiago,Concepcion,2026-01-05,-1,CLP,100,,"))


def test_fecha_mal_formada_se_rechaza() -> None:
    with pytest.raises(LoadError, match=r"fila 2, columna fecha_despacho:.*AAAA-MM-DD"):
        parse_envios(_text("E1,Rapido,Santiago,Concepcion,05/01/2026,150,CLP,100,,"))
    with pytest.raises(LoadError, match="fila 2, columna fecha_entrega"):
        parse_envios(_text("E1,Rapido,Santiago,Concepcion,2026-01-05,150,CLP,100,2026-01-07,2026-01-07T10:00"))


def test_celda_obligatoria_vacia_se_rechaza() -> None:
    with pytest.raises(LoadError, match="fila 2, columna transportista"):
        parse_envios(_text("E1,,Santiago,Concepcion,2026-01-05,150,CLP,100,,"))


def test_envio_id_repetido_nombra_las_dos_filas() -> None:
    with pytest.raises(LoadError, match=r"fila 3.*E1.*fila 2"):
        parse_envios(_text(ROW, ROW))


def test_dos_monedas_se_rechazan_nombrandolas() -> None:
    otra = "E2,Rapido,Santiago,Concepcion,2026-01-05,150,USD,100,,"
    with pytest.raises(LoadError, match=r"CLP, USD"):
        parse_envios(_text(ROW, otra))


def test_entrega_antes_del_despacho_se_rechaza() -> None:
    with pytest.raises(LoadError, match="fila 2, columna fecha_entrega"):
        parse_envios(_text("E1,Rapido,Santiago,Concepcion,2026-01-05,150,CLP,100,2026-01-07,2026-01-04"))


def test_archivo_sin_envios_se_rechaza() -> None:
    with pytest.raises(LoadError, match="no tiene envios"):
        parse_envios(_text())
    with pytest.raises(LoadError, match="vacio"):
        parse_envios("")


def test_lineas_en_blanco_se_saltean_sin_contar_como_envio() -> None:
    assert len(parse_envios(_text(ROW, "", "E2,Lento,Santiago,Concepcion,2026-01-05,120,CLP,100,,"))) == 2


def test_acepta_separador_punto_y_coma() -> None:
    text = _text(ROW.replace(",", ";"), header=HEADER.replace(",", ";"))
    (envio,) = parse_envios(text)
    assert envio.costo == Decimal("150")


def test_acepta_bom_de_excel(tmp_path) -> None:
    path = tmp_path / "envios.csv"
    path.write_text(_text(ROW), encoding="utf-8-sig")
    (envio,) = load_envios(path)
    assert envio.envio_id == "E1"


def test_archivo_no_utf8_pide_guardar_como_csv_utf8(tmp_path) -> None:
    path = tmp_path / "envios.csv"
    path.write_bytes(_text(ROW.replace("Concepcion", "Concepción")).encode("cp1252"))
    with pytest.raises(LoadError, match="UTF-8"):
        load_envios(path)


def test_archivo_inexistente_es_load_error(tmp_path) -> None:
    with pytest.raises(LoadError, match="no se pudo leer"):
        load_envios(tmp_path / "no-existe.csv")


def test_moneda_se_guarda_en_mayusculas() -> None:
    (envio,) = parse_envios(_text("E1,Rapido,Santiago,Concepcion,2026-01-05,150,clp,100,,"))
    assert envio.moneda == "CLP"


def test_nombres_normalizados() -> None:
    assert normalize_name("  Chile   Express ") == "Chile Express"
    assert name_key("  CHILE   express ") == name_key("Chile Express")
    (envio,) = parse_envios(_text("E1,  Rapido   Sur ,Santiago,Concepcion,2026-01-05,150,CLP,100,,"))
    assert envio.transportista == "Rapido Sur"
