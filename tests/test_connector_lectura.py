"""El carril de lectura entra a Kern como PROCEDENCIA, nunca como cita gateada.

Que se prueba aca y por que. El indice de lectura (44 libros, 6.929 pasajes con su
pagina fisica) vive en otro repo y habla MCP por stdio. Un pasaje NO tiene `node_id`,
asi que `filter_citations` lo rechazaria: este conector va RIO ABAJO del gate y lo que
devuelve jamas entra al pool de candidatas ni al campo `citations`.

Tres cosas hacen que este archivo exista en vez de un adaptador de diez lineas:

1. El cable devuelve un STRING de markdown (`structuredContent = {'result': <str>}`),
   no filas. `decode_tool_result` de `mcp_source` levanta `McpToolError` sobre eso, asi
   que lo unico reusable de ese modulo es `sync_tool_caller`.
2. `isError` es False tambien cuando no hubo resultados: el servidor contesta el string
   `'(sin pasajes)'` como exito, y los fallos vuelven como `'error: <mensaje>'`. Un
   adaptador ingenuo embarcaria ese texto al cliente como si fuera una cita.
3. Los PDF son copias de libgen y Kern no tiene hoy ningun tope de longitud sobre lo
   que renderiza. El tope lo pone este codigo: `MAX_EXCERPT_CHARS`.

Todo corre contra un servidor FAKE en memoria (`mcp.shared.memory`): el indice real
tarda ~5 s en arrancar y pide 1,6 GB de RSS.
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import pytest
from anyio.from_thread import start_blocking_portal
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session as _conectar

from src.connectors import lectura
from src.connectors.lectura import (
    ENV_INTERPRETE,
    MAX_EXCERPT_CHARS,
    Reading,
    ReadingLookup,
    read_passages,
    read_with_caller,
)
from src.connectors.mcp_source import sync_tool_caller

# La muestra real del cable, textual (verificada a mano contra el servidor vivo).
CABLE_REAL = (
    "1. **ivanov-global-supply-chain-operations** PDF p.397-399\n"
    "   In the further course of this Chapter, we will focus on the Alpha service level. "
    "In a situation of demand uncertainty, safety inventory is introduced with the objective "
    "to ensure product availability even in the case of demand fluctuations.\n"
    "2. **vandeput-inventory-optimization-models-simulations** PDF p.81-82\n"
    "   4.2 Stochastic Demand | 55 4.2.3 Safety Stocks We can now compute how much inventory we need."
)


class _Servidor:
    """Un servidor de lectura FALSO que devuelve el string que se le configure.

    FastMCP y ClientSession son los de verdad; lo unico que se evita es el transporte
    (pareo en memoria del propio paquete `mcp`), porque lo que se prueba es como Kern
    lee el cable, no como viajan los bytes.
    """

    def __init__(self, payload: str = "", *, revienta: bool = False) -> None:
        self.payload = payload
        self.revienta = revienta
        self.vistos: list[dict] = []

    def __enter__(self):
        srv = FastMCP("lectura-fake")
        vistos, payload, revienta = self.vistos, self.payload, self.revienta

        @srv.tool()
        def search_passages(query: str, k: int = 8, sources: list[str] | None = None) -> str:
            vistos.append({"query": query, "k": k, "sources": sources})
            if revienta:
                raise RuntimeError("el indice no arranco")
            return payload

        self._portal_cm = start_blocking_portal()
        portal = self._portal_cm.__enter__()
        self._ses_cm = portal.wrap_async_context_manager(_conectar(srv))
        ses = self._ses_cm.__enter__()
        return sync_tool_caller(ses, portal)

    def __exit__(self, *exc):
        self._ses_cm.__exit__(*exc)
        self._portal_cm.__exit__(*exc)
        return False


def _leer(payload: str, **kwargs) -> tuple[ReadingLookup, list[dict]]:
    srv = _Servidor(payload)
    with srv as call:
        return read_with_caller(call, ["safety stock"], **kwargs), srv.vistos


# --- 1. configuracion por entorno: en produccion NO esta, y eso no es un error ------


def test_sin_la_variable_de_entorno_no_hay_lectura(monkeypatch):
    """El camino de PRODUCCION. El indice pide 1,6 GB y la VM tiene 512 MB
    (`fly.toml`), asi que alla nunca esta disponible: la ausencia es el default."""
    monkeypatch.delenv(ENV_INTERPRETE, raising=False)
    r = read_passages(["safety stock"])
    assert r.available is False
    assert r.readings == ()
    assert ENV_INTERPRETE in r.note


def test_un_interprete_inexistente_no_intenta_levantar_nada(monkeypatch, tmp_path):
    """Ni un subproceso: si la ruta no existe, se contesta sin tocar el transporte."""
    abiertos: list[str] = []
    monkeypatch.setattr(lectura, "_stdio_caller", lambda *a, **k: abiertos.append("boom"))
    monkeypatch.setenv(ENV_INTERPRETE, str(tmp_path / "no-existe" / "python.exe"))

    r = read_passages(["safety stock"])

    assert abiertos == []
    assert r.available is False and r.readings == ()
    assert "no-existe" in r.note or "no existe" in r.note


# --- 2. ningun fallo del servidor se propaga al llamador ---------------------------


def test_un_fallo_al_abrir_la_sesion_no_tira_el_entregable(monkeypatch, tmp_path):
    """Un entregable no se cae porque falto una lectura."""
    falso_python = tmp_path / "python.exe"
    falso_python.write_text("no soy un interprete", encoding="utf-8")
    monkeypatch.setenv(ENV_INTERPRETE, str(falso_python))

    def _explota(*a, **k):
        raise OSError("el proceso no arranco")

    monkeypatch.setattr(lectura, "_stdio_caller", _explota)

    r = read_passages(["safety stock"])
    assert r.available is False and r.readings == ()
    assert "el proceso no arranco" in r.note


def test_una_excepcion_de_la_tool_no_se_propaga():
    """El servidor contesta isError: se traduce a estado, no a traceback."""
    srv = _Servidor(revienta=True)
    with srv as call:
        r = read_with_caller(call, ["safety stock"])
    assert r.available is False and r.readings == ()
    assert r.note


def test_un_call_tool_que_levanta_se_traduce_a_estado():
    def _revienta(name, arguments):
        raise TimeoutError("se acabo el tiempo")

    r = read_with_caller(_revienta, ["safety stock"])
    assert r.available is False and r.readings == ()
    assert "se acabo el tiempo" in r.note


# --- 3. '(sin pasajes)' y 'error:' NO son citas ------------------------------------


def test_sin_pasajes_no_es_una_lectura():
    r, _ = _leer("(sin pasajes)")
    assert r.available is False and r.readings == ()
    assert "(sin pasajes)" in r.note


def test_un_error_del_servidor_jamas_se_convierte_en_cita():
    """El bug que este test existe para impedir: embarcar el mensaje interno del
    servidor al cliente como si fuera un pasaje de libro."""
    r, _ = _leer("error: storage 'kv_store_full_docs.json' esta corrupto")
    assert r.available is False and r.readings == ()
    assert r.note.startswith("error:")


def test_una_respuesta_vacia_tampoco_es_una_lectura():
    r, _ = _leer("   \n  ")
    assert r.available is False and r.readings == ()
    assert r.note


# --- 4. el parser del markdown ------------------------------------------------------


def test_el_parser_lee_la_muestra_real_del_cable():
    r, _ = _leer(CABLE_REAL)
    assert r.available is True
    assert len(r.readings) == 2
    assert isinstance(r.readings, tuple) and isinstance(r.readings[0], Reading)

    uno, dos = r.readings
    assert uno.source == "ivanov-global-supply-chain-operations"
    assert uno.page_label == "PDF p.397-399"
    assert uno.excerpt.startswith("In the further course of this Chapter")

    assert dos.source == "vandeput-inventory-optimization-models-simulations"
    assert dos.page_label == "PDF p.81-82"
    assert dos.excerpt.startswith("4.2 Stochastic Demand")


def test_el_rotulo_pdf_se_conserva_textual():
    """El rotulo "PDF p.397" nunca se convierte en "p.397": la pagina es la FISICA del
    archivo y el folio impreso difiere por obra (Vandeput: 26 paginas de corrimiento).
    Una cita que el cliente falsea abriendo el libro impreso es peor que no citar."""
    r, _ = _leer(CABLE_REAL)
    assert all(x.page_label.startswith("PDF p.") for x in r.readings)


def test_una_pagina_ausente_se_dice_asi():
    r, _ = _leer("1. **liu-supply-chain-analytics** sin pagina\n   Un parrafo sin marca.")
    assert r.available is True
    assert r.readings[0].page_label == "sin pagina"


def test_una_entrada_sin_extracto_sigue_siendo_procedencia():
    """Libro + pagina ya es una cita completa; el fragmento es un extra."""
    payload = (
        "1. **ivanov-global-supply-chain-operations** PDF p.12\n"
        "2. **liu-supply-chain-analytics** PDF p.7\n"
        "   Con extracto."
    )
    r, _ = _leer(payload)
    assert [(x.source, x.excerpt) for x in r.readings] == [
        ("ivanov-global-supply-chain-operations", ""),
        ("liu-supply-chain-analytics", "Con extracto."),
    ]


def test_una_entrada_rota_se_descarta_sin_tirar_el_lote():
    """Media cita no se inventa, pero una linea rota no se lleva puestas las buenas."""
    payload = (
        "1. ** ** \n"
        "   huerfano\n"
        "2. **liu-supply-chain-analytics** PDF p.7\n"
        "   El que si sirve.\n"
        "ruido suelto que no es una entrada\n"
        "3. **vandeput-inventory-optimization-models-simulations** PDF p.81-82\n"
        "   Y el otro."
    )
    r, _ = _leer(payload)
    assert [x.source for x in r.readings] == [
        "liu-supply-chain-analytics",
        "vandeput-inventory-optimization-models-simulations",
    ]


def test_un_extracto_partido_en_varias_lineas_se_junta():
    payload = (
        "1. **liu-supply-chain-analytics** PDF p.7\n"
        "   Primera linea del extracto\n"
        "   y la segunda."
    )
    r, _ = _leer(payload)
    assert r.readings[0].excerpt == "Primera linea del extracto y la segunda."


def test_un_lote_sin_ninguna_entrada_legible_no_queda_disponible():
    r, _ = _leer("no hay ni una entrada con forma de pasaje")
    assert r.available is False and r.readings == ()
    assert r.note


# --- 5. saneo: nada de lo que salga puede romper una consola cp1252 ------------------

# Escapes en vez de los caracteres, para que este archivo fuente quede ASCII: cuando un
# assert falla, pytest imprime el valor, y una consola cp1252 de Windows revienta sobre
# un centinela. Lo que hay abajo, en orden: los centinelas de pagina que usa el otro
# repo, comillas curvas de apertura y cierre, raya, sigma, signo micro, la ligadura
# fl y los puntos suspensivos.
SUCIO = (
    "1. **vandeput-inventory-optimization-models-simulations** PDF p.83\n"
    "   \u27e6p.83\u27e7 The \u201cservice\u201d level \u2014 with \u03c3 = 4 and \u00b5 = 10 "
    "\u2014 drives the \u27e6p.84\u27e7 over\ufb02ow of safety stock\u2026"
)


def test_los_centinelas_de_pagina_no_llegan_al_cliente():
    r, _ = _leer(SUCIO)
    assert "\u27e6" not in r.readings[0].excerpt
    assert "p.83" not in r.readings[0].excerpt  # el centinela se va entero, no a medias


def test_el_extracto_se_imprime_en_una_consola_cp1252():
    """CLAUDE.md prohibe no-ASCII en prints de consola (Windows rompe en cp1252)."""
    r, _ = _leer(SUCIO)
    texto = r.readings[0].excerpt
    texto.encode("cp1252")  # no debe levantar UnicodeEncodeError
    assert texto.isascii()


def test_el_saneo_conserva_el_sentido_del_texto():
    r, _ = _leer(SUCIO)
    texto = r.readings[0].excerpt
    assert "overflow" in texto  # la ligadura fl no se comio la palabra
    assert '"service"' in texto  # comillas curvas -> rectas
    assert "sigma" in texto and "mu" in texto  # matematica legible, no "?"


def test_la_etiqueta_de_pagina_tambien_sale_ascii():
    r, _ = _leer(SUCIO)
    assert r.readings[0].page_label.isascii()


# --- 6. el tope de copyright --------------------------------------------------------


def test_el_tope_es_mas_corto_que_el_del_otro_repo():
    """320 < 600 a proposito: lo que Kern renderiza llega al cliente."""
    assert MAX_EXCERPT_CHARS == 320


def test_el_extracto_se_corta_sin_partir_palabras():
    largo = " ".join(f"palabra{i:02d}" for i in range(80))
    r, _ = _leer(f"1. **liu-supply-chain-analytics** PDF p.7\n   {largo}")

    texto = r.readings[0].excerpt
    assert len(texto) <= MAX_EXCERPT_CHARS
    assert texto.endswith("...")
    palabras = texto[:-3].split()
    assert palabras, texto
    assert all(p.startswith("palabra") and len(p) == 9 for p in palabras), palabras


def test_un_extracto_corto_no_se_toca():
    r, _ = _leer("1. **liu-supply-chain-analytics** PDF p.7\n   Corto y entero.")
    assert r.readings[0].excerpt == "Corto y entero."


def test_el_corte_va_despues_del_saneo():
    """Si se cortara antes, el tope quedaria mentiroso: el reemplazo puede ALARGAR el
    texto (un solo caracter sigma pasa a cinco)."""
    sigma = "\u03c3" * 200  # 200 caracteres que al sanear pasan a 1000
    r, _ = _leer(f"1. **liu-supply-chain-analytics** PDF p.7\n   {sigma}")
    assert len(r.readings[0].excerpt) <= MAX_EXCERPT_CHARS


# --- 7. como viaja la consulta ------------------------------------------------------


def test_las_keywords_viajan_unidas_por_espacio():
    srv = _Servidor(CABLE_REAL)
    with srv as call:
        read_with_caller(call, ["safety", "stock", "alpha service level"], k=5)
    assert srv.vistos == [{"query": "safety stock alpha service level", "k": 5, "sources": None}]


def test_sources_viaja_tal_cual_cuando_viene():
    slugs = ["vandeput-inventory-optimization-models-simulations"]
    srv = _Servidor(CABLE_REAL)
    with srv as call:
        read_with_caller(call, ["safety stock"], k=3, sources=slugs)
    assert srv.vistos[0]["sources"] == slugs


def test_sin_keywords_no_se_molesta_al_indice():
    """Levantar 1,6 GB para una consulta vacia es gasto puro."""
    srv = _Servidor(CABLE_REAL)
    with srv as call:
        r = read_with_caller(call, [])
    assert srv.vistos == []
    assert r.available is False and r.note


# --- el contrato de los tipos -------------------------------------------------------


def test_los_dos_tipos_son_inmutables():
    r = Reading(source="x", page_label="PDF p.1", excerpt="y")
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.source = "z"  # type: ignore[misc]
    lookup = ReadingLookup(available=False, readings=(), note="porque si")
    with pytest.raises(dataclasses.FrozenInstanceError):
        lookup.available = True  # type: ignore[misc]


def test_no_disponible_nunca_trae_lecturas(monkeypatch):
    monkeypatch.delenv(ENV_INTERPRETE, raising=False)
    for r in (read_passages(["x"]), read_with_caller(lambda n, a: "(sin pasajes)", ["x"])):
        assert r.available is False
        assert r.readings == ()
        assert r.note.strip()


def test_el_modulo_no_toca_el_gate_de_citas():
    """Nada de esto entra al pool de candidatas ni al campo `citations`: un pasaje no
    tiene `node_id` y `filter_citations` lo rechazaria. Esto va RIO ABAJO del gate.

    Se mira el AST y no el texto. La primera version buscaba la cadena
    "filter_citations" en el archivo y fallaba contra el docstring del modulo, que
    NOMBRA al gate justamente para explicar por que no lo toca: un import o una llamada
    es la conducta que hay que prohibir, mencionarlo en prosa es documentacion.
    """
    arbol = ast.parse(Path(lectura.__file__).read_text(encoding="utf-8"))

    importados: set[str] = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            importados |= {a.name for a in nodo.names}
        elif isinstance(nodo, ast.ImportFrom):
            importados |= {nodo.module or ""} | {f"{nodo.module or ''}.{a.name}" for a in nodo.names}
    assert not [m for m in importados if "citation" in m or "knowledge" in m], sorted(importados)

    llamadas = {
        n.func.id if isinstance(n.func, ast.Name) else getattr(n.func, "attr", "")
        for n in ast.walk(arbol)
        if isinstance(n, ast.Call)
    }
    assert "filter_citations" not in llamadas
