"""Los alias del pack por vertical llegan al mapeo de columnas.

`VerticalProfile.merged_aliases` existia, estaba probado y **solo lo llamaban los
tests**: los packs eran dato muerto. Esto los enchufa por el unico camino donde un
vertical existe en runtime -- el orquestador, que lo resuelve de
`ClientProfile.vertical` -- y lo lleva hasta `detect_columns` por
`params["column_aliases"]`.

El test que decide si esto sirve para algo es
`test_el_archivo_minero_pasa_de_no_correr_a_correr`: sin el, esto seria un
parametro que nadie pasa, que es exactamente el patron que viene a cortar.

Y una medicion que conviene tener presente al leer el resto: **el cableado solo
no cambiaba nada**. `_norm("Numero de Parte")` da `numerodeparte` y el alias que
el pack traia, `numero_parte`, da `numeroparte`. `_norm` pliega acentos y borra
puntuacion, pero NO borra conectivas. La grafia con "de" hubo que listarla.
"""
from __future__ import annotations

import copy
import inspect

import pytest

pytest.importorskip("pandas")

import pandas as pd  # noqa: E402

from jobs import intake  # noqa: E402
from jobs.intake import ALIASES, detect_columns  # noqa: E402
from jobs.intake_scan_job import _map_headers  # noqa: E402
from src.vertical_profile import list_verticals, load_vertical_profile  # noqa: E402

#: El archivo del cliente minero tal como lo exporta su ERP.
MINERO = ["Numero de Parte", "Fecha", "Cantidad", "Fabricante", "Stock Bodega"]


@pytest.fixture()
def merged():
    return load_vertical_profile("repuestos-industriales").merged_aliases(ALIASES)


# --- el que decide si esto sirve -------------------------------------------


def test_el_archivo_minero_pasa_de_no_correr_a_correr(merged):
    """Sin el pack falta `product_id` y el job devuelve `needs_data`. Con el, corre.

    Es la diferencia entre un 400 y un entregable, y es la unica razon por la que
    el resto de este modulo existe.
    """
    df = pd.DataFrame(columns=MINERO)

    sin_pack = detect_columns(df)
    assert sin_pack.unmatched_required == ["product_id"]

    con_pack = detect_columns(df, aliases=merged)
    assert con_pack.unmatched_required == []
    assert con_pack.mapping["product_id"] == "Numero de Parte"


def test_la_grafia_con_conectiva_es_la_que_hace_el_trabajo(merged):
    """`_norm` no borra el "de": `numero_parte` NO coincide con "Numero de Parte".

    Este test existe porque el pack se escribio creyendo lo contrario, y el
    comentario del YAML lo afirmaba. Si alguien "limpia" los alias con conectiva
    por redundantes, esto se pone rojo.
    """
    assert intake._norm("Numero de Parte") == "numerodeparte"
    assert intake._norm("numero_parte") == "numeroparte"
    for grafia in ("Numero de Parte", "Nro de Parte", "Codigo de Parte"):
        m = detect_columns(pd.DataFrame(columns=[grafia, "Fecha", "Cantidad"]), aliases=merged)
        assert m.mapping.get("product_id") == grafia, grafia


def test_el_camino_ancho_llega_a_on_hand_y_name(merged):
    """`_map_headers` recorre el juego canonico ancho, `detect_columns` no.

    Es el unico consumidor alcanzable desde el orquestador que mira los grupos
    `on_hand` y `name` del pack.
    """
    mapping = _map_headers(MINERO, merged)
    assert mapping["product_id"] == "Numero de Parte"
    assert mapping["on_hand"] == "Stock Bodega"


# --- la regresion que el pack habria introducido ----------------------------


@pytest.mark.parametrize("descriptiva", ["Repuesto", "Insumo"])
def test_un_sustantivo_del_rubro_no_le_gana_a_la_columna_de_codigo(merged, descriptiva):
    """`repuesto` bajo `product_id` mapeaba el IDENTIFICADOR a la descripcion.

    Y nada fallaba: `mapping.ok` daba True, `_normalize_core` agrupaba por esa
    columna, y cada texto distinto pasaba a ser un SKU. El conteo de SKU, las
    clases ABC y el entregable entero quedaban construidos sobre texto libre.
    Prependar convierte cada alias en "en este rubro X es SIEMPRE esta canonica,
    incluso si el archivo trae una columna de codigo": un sustantivo pelado no
    aguanta esa prueba, y por eso viven bajo `name`.
    """
    df = pd.DataFrame(columns=["Fecha", "Codigo", descriptiva, "Cantidad"])
    assert detect_columns(df, aliases=merged).mapping["product_id"] == "Codigo"
    ancho = _map_headers(list(df.columns), merged)
    assert ancho["product_id"] == "Codigo"
    assert ancho["name"] == descriptiva


def test_ningun_pack_introduce_una_colision_entre_canonicas():
    """Lint de packs: dos canonicas no pueden reclamar la misma clave normalizada.

    La tabla base ya tiene cuatro colisiones conocidas y aceptadas; ningun pack
    puede agregar una quinta sin que esto se ponga rojo.
    """
    base_owners: dict[str, set[str]] = {}
    for canon, entries in ALIASES.items():
        for alias in entries:
            base_owners.setdefault(intake._norm(alias), set()).add(canon)
    esperadas = {k for k, v in base_owners.items() if len(v) > 1}

    for slug in list_verticals():
        tabla = load_vertical_profile(slug).merged_aliases(ALIASES)
        owners: dict[str, set[str]] = {}
        for canon, entries in tabla.items():
            for alias in entries:
                owners.setdefault(intake._norm(alias), set()).add(canon)
        assert {k for k, v in owners.items() if len(v) > 1} == esperadas, slug


# --- precedencia ------------------------------------------------------------


def test_un_override_le_gana_al_pack(merged):
    df = pd.DataFrame(columns=["Fecha", "Codigo Interno", "Numero de Parte", "Cantidad"])
    m = detect_columns(df, {"product_id": "Codigo Interno"}, aliases=merged)
    assert m.mapping["product_id"] == "Codigo Interno"


def test_el_pack_le_gana_a_la_tabla_base_en_un_archivo_que_trae_las_dos(merged):
    df = pd.DataFrame(columns=["Existencia", "Stock Bodega"])
    m = detect_columns(df, required=(), optional=("on_hand",), aliases=merged)
    assert m.mapping["on_hand"] == "Stock Bodega"


# --- guardias ---------------------------------------------------------------


def test_una_canonica_que_la_tabla_no_tiene_no_levanta_KeyError():
    """El corchete `ALIASES[canon]` era un 500 esperando.

    `KeyError` no es `ValueError`, y los endpoints capturan `ValueError`: pedir
    una canonica del pack con la tabla base devolvia un 500, no un 400.
    """
    m = detect_columns(pd.DataFrame(columns=["Patente"]), required=("equipment_id",))
    assert m.unmatched_required == ["equipment_id"]


def test_una_tabla_vacia_es_un_error_explicito(merged):
    """Pasar `VerticalProfile.aliases` crudo tiene la forma correcta y no falla."""
    with pytest.raises(ValueError, match="merged_aliases"):
        detect_columns(pd.DataFrame(columns=["Fecha"]), aliases={})


def test_un_override_fuera_de_required_u_optional_ya_no_se_evapora():
    """El branch de overrides vive DENTRO del bucle: la capa de mayor precedencia
    fallaba de la forma mas callada."""
    with pytest.raises(ValueError, match="equipment_id"):
        detect_columns(pd.DataFrame(columns=["Patente"]), {"equipment_id": "Patente"})


def test_las_canonicas_del_pack_siguen_invisibles_bajo_los_juegos_por_defecto(merged):
    """`merged_aliases` CREA claves; que existan no las hace detectables."""
    df = pd.DataFrame(columns=["Patente", "Equipo", "Fecha", "Cantidad", "SKU"])
    m = detect_columns(df, aliases=merged)
    assert "equipment_id" not in m.mapping
    assert "work_order" not in m.mapping


def test_la_tabla_del_modulo_nunca_se_muta(merged, tmp_path):
    """El pack llega por el parametro. Un `ALIASES.update()` global seria un bug
    de concurrencia: un proceso, un pool de hilos, un dict de modulo."""
    antes = copy.deepcopy(ALIASES)
    csv = tmp_path / "d.csv"
    csv.write_text(
        "Numero de Parte,Fecha,Cantidad\nA-1,2026-01-01,5\nA-1,2026-01-08,7\n", encoding="utf-8"
    )
    intake.prepare(csv, aliases=merged)
    assert ALIASES == antes
    # Y la corrida siguiente sin pack vuelve al comportamiento base.
    assert detect_columns(pd.DataFrame(columns=MINERO)).unmatched_required == ["product_id"]


# --- la misma tabla para elegir la hoja y para mapear ------------------------


def test_la_hoja_y_el_encabezado_se_encuentran_solo_con_la_tabla_del_pack(tmp_path, merged):
    """Una fila 100% del rubro puntua 0 contra la tabla base.

    Y con 0 en todas partes `detect_sheet_and_header` devuelve `(0, 0)`, o sea
    toma el titulo del ERP como encabezado y convierte cada columna real en
    `Unnamed: N`. El pack quedaria invisible justo donde el scan existe.

    Ojo con generalizar: una fila minera que ADEMAS traiga `Fecha` y `Cantidad`
    ya puntua 2 con la tabla base y se encuentra sola. El caso que la necesita
    es el de encabezados enteramente del rubro.
    """
    openpyxl = pytest.importorskip("openpyxl")
    solo_rubro = ["Numero de Parte", "Patente", "Fabricante", "Stock Bodega"]
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Reporte de Consumos 2026"])
    ws.append([])
    ws.append(solo_rubro)
    ws.append(["A-1", "ABCD-12", "ACME", 120])
    path = tmp_path / "minero.xlsx"
    wb.save(path)

    assert intake.detect_sheet_and_header(path) == (0, 0)
    assert intake.detect_sheet_and_header(path, aliases=merged)[1] == 2


def test_prepare_pasa_la_misma_tabla_a_las_dos_funciones(tmp_path, monkeypatch, merged):
    """Una fila de encabezado elegida con una tabla y columnas mapeadas con otra
    da un frame plausible y equivocado."""
    vistas = []
    real_load, real_detect = intake.load_table, intake.detect_columns
    monkeypatch.setattr(intake, "load_table",
                        lambda p, *, aliases=None: (vistas.append(aliases), real_load(p, aliases=aliases))[1])
    monkeypatch.setattr(intake, "detect_columns",
                        lambda raw, ov=None, **kw: (vistas.append(kw.get("aliases")), real_detect(raw, ov, **kw))[1])
    csv = tmp_path / "d.csv"
    csv.write_text(
        "Numero de Parte,Fecha,Cantidad\nA-1,2026-01-01,5\nA-1,2026-01-08,7\n", encoding="utf-8"
    )
    intake.prepare(csv, aliases=merged)
    assert len(vistas) == 2 and vistas[0] is merged and vistas[1] is merged


def test_un_alias_de_dos_letras_no_vota_por_la_fila_de_encabezado(tmp_path, merged):
    """`ot` aparece como VALOR en cualquier columna "Tipo Documento": cada fila de
    datos se llevaria un punto y los empates van a la de mas arriba."""
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["OT", "algo", "otra"])            # fila de datos disfrazada
    ws.append(["Numero de Parte", "Fecha", "Cantidad"])
    ws.append(["A-1", "2026-01-01", 5])
    path = tmp_path / "corta.xlsx"
    wb.save(path)
    assert intake.detect_sheet_and_header(path, aliases=merged)[1] == 1


# --- neutralidad ------------------------------------------------------------


def test_sin_pack_todo_se_comporta_igual_que_antes(tmp_path):
    a = intake.prepare("data/sample_demand_portfolio.csv")
    b = intake.prepare("data/sample_demand_portfolio.csv", aliases=None)
    pd.testing.assert_frame_equal(a, b)


def test_normalize_no_conoce_los_alias():
    """Toman un `ColumnMapping` ya resuelto: el conocimiento de alias se gasta antes."""
    for fn in (intake.normalize, intake.normalize_tracked):
        assert "aliases" not in inspect.signature(fn).parameters


# --- el consumidor real, de punta a punta -----------------------------------


def _archivo_minero(tmp_path):
    csv = tmp_path / "consumo.csv"
    filas = ["Numero de Parte,Fecha,Cantidad,Fabricante,Stock Bodega"]
    for semana in range(1, 30):
        for sku in ("A-1", "A-2"):
            filas.append(
                f"{sku},2026-{1 + semana // 8:02d}-{1 + (semana * 3) % 27:02d},"
                f"{5 + semana % 4},ACME,120"
            )
    csv.write_text("\n".join(filas) + "\n", encoding="utf-8")
    return csv


def test_el_pack_llega_hasta_el_job_por_el_orquestador(tmp_path):
    """EL TEST QUE DECIDE SI ESTO SIRVE.

    El mismo archivo y la misma herramienta: sin rubro declarado el job no corre
    porque no encuentra `product_id`; con el rubro declarado corre. Si este test
    se pone verde por accidente cuando el cableado se rompa, no queda nada que
    distinga esta funcionalidad de `merged_aliases`, que existia, estaba probada,
    y no la llamaba nadie.
    """
    pytest.importorskip("nosilo_core")
    from nosilo_core import llm

    from scm_agent import tools
    from scm_agent.orchestrator import Orchestrator
    from src import client_profile

    root = tmp_path / "clients"
    client_profile.upsert_profile(
        "Mina X", "Mina X", root=root, vertical="repuestos-industriales"
    )
    csv = _archivo_minero(tmp_path)
    orch = Orchestrator(tools.build_default_registry(), llm.RulesFallback(), clients_root=root)
    kwargs = {
        "data_path": str(csv),
        "job_type": "inventory_optimization",
        "out_dir": tmp_path / "out",
    }

    sin_rubro = orch.run("optimiza el inventario", client="Otro", **kwargs)
    assert sin_rubro.status == "needs_data"
    assert any("product_id" in c for c in sin_rubro.clarifications)

    con_rubro = orch.run("optimiza el inventario", client="Mina X", **kwargs)
    assert con_rubro.status == "ok", con_rubro.summary


def test_el_orquestador_no_deja_que_el_llamador_pise_la_tabla_del_rubro(tmp_path):
    """La tabla la resuelve el proceso desde el rubro declarado; no es una perilla.

    `merge_params` pone los params del llamador ultimos y por lo tanto mas altos,
    asi que la asignacion va DESPUES.
    """
    pytest.importorskip("nosilo_core")
    from nosilo_core import llm

    from scm_agent import tools
    from scm_agent.orchestrator import Orchestrator
    from src import client_profile

    root = tmp_path / "clients"
    client_profile.upsert_profile(
        "Mina X", "Mina X", root=root, vertical="repuestos-industriales"
    )
    csv = _archivo_minero(tmp_path)
    orch = Orchestrator(tools.build_default_registry(), llm.RulesFallback(), clients_root=root)
    r = orch.run(
        "optimiza el inventario",
        data_path=str(csv),
        job_type="inventory_optimization",
        client="Mina X",
        overrides={"column_aliases": {"product_id": ["no_existe"]}},
        out_dir=tmp_path / "out",
    )
    assert r.status == "ok", r.summary
