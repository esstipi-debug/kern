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


def test_fecha_imposible_del_calendario_se_rechaza() -> None:
    with pytest.raises(LoadError, match=r"fila 2, columna fecha_despacho:.*AAAA-MM-DD"):
        parse_envios(_text("E1,Rapido,Santiago,Concepcion,2026-02-30,150,CLP,100,,"))


SEMI_HEADER = HEADER.replace(",", ";")
HEADER_8 = "envio_id,transportista,origen,destino,fecha_despacho,costo,moneda,kg"
COMPUESTA = "Concepción"
DESCOMPUESTA = "Concepción"


def _fila_ok(n: int) -> str:
    return f"E{n},Rapido,Santiago,Concepcion,2026-01-05,150,CLP,100,,"


# --- C1: un punto de miles no puede leerse como decimal (45.000 => 45) ---------------------------


def test_punto_de_miles_en_costo_se_rechaza_en_archivo_con_punto_y_coma() -> None:
    row = "E1;Rapido;Santiago;Concepcion;2026-01-05;45.000;CLP;300;2026-01-07;2026-01-07"
    with pytest.raises(LoadError, match=r"fila 2, columna costo:.*parece usar el punto como separador de miles"):
        parse_envios(_text(row, header=SEMI_HEADER))


def test_punto_de_miles_en_costo_se_rechaza_tambien_en_archivo_con_comas() -> None:
    with pytest.raises(LoadError, match=r"fila 2, columna costo:.*parece usar el punto como separador de miles"):
        parse_envios(_text("E1,Rapido,Santiago,Concepcion,2026-01-05,15.000,CLP,300,,"))


def test_punto_de_miles_en_kg_se_rechaza_solo_en_archivo_con_punto_y_coma() -> None:
    semi = "E1;Rapido;Santiago;Concepcion;2026-01-05;150;CLP;1.500;;"
    with pytest.raises(LoadError, match=r"fila 2, columna kg:.*parece usar el punto como separador de miles"):
        parse_envios(_text(semi, header=SEMI_HEADER))
    # con comas el punto es decimal (convencion inglesa): 1.500 son 1,5 kg
    (envio,) = parse_envios(_text("E1,Rapido,Santiago,Concepcion,2026-01-05,150,CLP,1.500,,"))
    assert envio.kg == Decimal("1.500")


def test_decimales_legitimos_no_se_confunden_con_miles() -> None:
    (envio,) = parse_envios(_text("E1,Rapido,Santiago,Concepcion,2026-01-05,0.750,CLP,12.5,,"))
    assert (envio.costo, envio.kg) == (Decimal("0.750"), Decimal("12.5"))
    semi = "E1;Rapido;Santiago;Concepcion;2026-01-05;45.5;CLP;0.750;;"
    (envio,) = parse_envios(_text(semi, header=SEMI_HEADER))
    assert (envio.costo, envio.kg) == (Decimal("45.5"), Decimal("0.750"))


@pytest.mark.parametrize("raw", ["1e999999999", "1E3", "2e5", "1_000", "0x10"])
def test_formas_de_numero_que_decimal_acepta_pero_un_csv_no_se_rechazan(raw: str) -> None:
    with pytest.raises(LoadError, match=r"fila 2, columna costo:.*notacion cientifica"):
        parse_envios(_text(f"E1,Rapido,Santiago,Concepcion,2026-01-05,{raw},CLP,100,,"))


# --- I1: una comilla sin cerrar no puede tragarse filas ni reventar con csv.Error ---------------


def test_comilla_sin_cerrar_no_se_traga_filas_en_silencio() -> None:
    rows = ['E0,"Rapido,Santiago,Concepcion,2026-01-05,150,CLP,100,,']
    rows += [_fila_ok(n) for n in range(1, 9)]
    rows += ['E9,"Lento SA",Santiago,Concepcion,2026-01-05,150,CLP,100,,']
    with pytest.raises(LoadError, match=r"fila 2: .*mal formado"):
        parse_envios(_text(*rows))


def test_comilla_sin_cerrar_hasta_el_final_del_archivo() -> None:
    with pytest.raises(LoadError, match=r"fila 3: .*mal formado"):
        parse_envios(_text(_fila_ok(1), 'E2,"Rapido,Santiago,Concepcion,2026-01-05,150,CLP,100,,'))


def test_comilla_sin_cerrar_en_archivo_grande_no_revienta_con_csv_error() -> None:
    rows = ['E0,"Rapido,Santiago,Concepcion,2026-01-05,150,CLP,100,,'] + [_fila_ok(n) for n in range(1, 4000)]
    with pytest.raises(LoadError, match=r"fila 2: .*mal formado"):
        parse_envios(_text(*rows))


def test_comillas_bien_cerradas_siguen_leyendose() -> None:
    (envio,) = parse_envios(_text('E1,"Lento, SA",Santiago,Concepcion,2026-01-05,150,CLP,100,,'))
    assert envio.transportista == "Lento, SA"


# --- I2: una celda de mas (coma dentro de un numero sin comillas) no puede truncar el dato -------


def test_celda_de_mas_por_una_coma_sin_comillas_se_rechaza() -> None:
    with pytest.raises(LoadError, match=r"fila 2: tiene 9 celdas pero el encabezado tiene 8"):
        parse_envios(_text("E1,Rapido,Santiago,Concepcion,2026-01-05,150,CLP,1,500", header=HEADER_8))


def test_celda_de_mas_con_columnas_de_fecha_nombra_el_problema_real() -> None:
    row = "E1,Rapido,Santiago,Concepcion,2026-01-05,150,CLP,1,500,2026-01-07,2026-01-07"
    with pytest.raises(LoadError, match=r"fila 2: tiene 11 celdas pero el encabezado tiene 10"):
        parse_envios(_text(row))


def test_celdas_vacias_de_mas_al_final_se_aceptan() -> None:
    (envio,) = parse_envios(_text("E1,Rapido,Santiago,Concepcion,2026-01-05,150,CLP,100,,,", header=HEADER_8))
    assert envio.kg == Decimal("100")


# --- I5: el mismo lugar con o sin acento, o en otra forma Unicode, es el mismo nombre -----------


def test_nombres_se_comparan_sin_acentos_ni_forma_unicode() -> None:
    assert name_key("CONCEPCIÓN") == name_key("Concepcion") == name_key(DESCOMPUESTA) == name_key(COMPUESTA)
    assert name_key("Ñuñoa") == name_key("nunoa")


def test_normalize_name_compone_la_forma_unicode_y_conserva_el_acento() -> None:
    assert normalize_name(DESCOMPUESTA) == COMPUESTA
    assert normalize_name("  " + DESCOMPUESTA + "  ") == COMPUESTA


def test_el_loader_entrega_la_forma_compuesta() -> None:
    (envio,) = parse_envios(_text(f"E1,Rapido,Santiago,{DESCOMPUESTA},2026-01-05,150,CLP,100,,"))
    assert envio.destino == COMPUESTA
