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
import sys
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
    lookup_from_text,
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


def test_ni_siquiera_un_disco_lleno_tira_el_entregable(monkeypatch, tmp_path):
    """La captura del stderr del servidor es una comodidad, no una precondicion.

    Antes de este test el `with tempfile.TemporaryFile(...)` quedaba FUERA del try, asi que
    un TMP no escribible o un disco lleno hacian que `read_passages` levantara OSError y se
    cayera el entregable entero -- justo lo contrario de lo que promete su docstring.
    """
    falso_python = tmp_path / "python.exe"
    falso_python.write_text("no soy un interprete", encoding="utf-8")
    monkeypatch.setenv(ENV_INTERPRETE, str(falso_python))

    def _sin_disco(*a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(lectura.tempfile, "TemporaryFile", _sin_disco)

    r = read_passages(["safety stock"])
    assert r.available is False and r.readings == ()
    assert "No space left on device" in r.note


def test_una_excepcion_de_la_tool_no_se_propaga():
    """El servidor contesta isError: se traduce a estado, no a traceback.

    La nota tiene que NOMBRAR el fallo. `assert r.note` lo satisface cualquier string
    no vacio, incluso uno que no diga que paso: una nota asi no le sirve al humano que
    tiene que ir a arreglar el indice, que es la unica razon por la que la nota existe.
    """
    srv = _Servidor(revienta=True)
    with srv as call:
        r = read_with_caller(call, ["safety stock"])
    assert r.available is False and r.readings == ()
    assert "el indice no arranco" in r.note


def test_un_call_tool_que_levanta_se_traduce_a_estado():
    def _revienta(name, arguments):
        raise TimeoutError("se acabo el tiempo")

    r = read_with_caller(_revienta, ["safety stock"])
    assert r.available is False and r.readings == ()
    assert "se acabo el tiempo" in r.note


def test_una_causa_envuelta_en_exceptiongroup_llega_nombrada():
    """Lo que sube de anyio NO es la causa: es un `ExceptionGroup` que dice "unhandled
    errors in a TaskGroup (1 sub-exception)" y nada mas. Una nota asi no le sirve al
    humano que tiene que ir a arreglar el indice, que es la unica razon por la que la
    nota existe: el grupo es plomeria y lo que se nombra es la hoja."""

    def _revienta(name, arguments):
        raise ExceptionGroup(
            "unhandled errors in a TaskGroup (1 sub-exception)",
            [ExceptionGroup("unhandled errors in a TaskGroup (1 sub-exception)", [RuntimeError("el socket se cerro")])],
        )

    r = read_with_caller(_revienta, ["safety stock"])
    assert r.available is False and r.readings == ()
    assert "el socket se cerro" in r.note
    assert "TaskGroup" not in r.note  # el envoltorio no ocupa la linea


def test_una_causa_encadenada_con_from_tambien_se_nombra():
    def _revienta(name, arguments):
        try:
            raise ModuleNotFoundError("No module named 'kern_lectura'")
        except ModuleNotFoundError as raiz:
            raise RuntimeError("no se pudo importar el servidor") from raiz

    r = read_with_caller(_revienta, ["safety stock"])
    assert "no se pudo importar el servidor" in r.note  # el eslabon de arriba no se pierde
    assert "kern_lectura" in r.note  # y la causa de verdad tampoco


def test_el_transporte_real_dice_por_que_no_arranco(monkeypatch):
    """El UNICO modo de falla que existe fuera de produccion, sin monkeypatch.

    Los dos tests de arriba reemplazan el transporte por una excepcion sintetica, asi
    que ninguno ejercita `_stdio_caller`. Este lo corre de verdad: la variable apunta a
    un interprete VALIDO que no tiene el modulo del servidor -- la variable mal seteada,
    que es como esto se rompe en una maquina de desarrollo. Del lado del cliente solo
    llega "Connection closed"; la causa (`No module named 'kern_lectura'`) sale por el
    stderr del subproceso, que este conector captura en vez de tirarlo a la consola.

    Cuesta ~3 s porque levanta un subproceso real. No levanta el indice: el modulo no
    existe y el interprete muere en el arranque, asi que no pide 1,6 GB de nada.
    """
    monkeypatch.setenv(ENV_INTERPRETE, sys.executable)

    r = read_passages(["safety stock"], timeout_s=20.0)

    assert r.available is False and r.readings == ()
    assert "kern_lectura" in r.note  # la nota NOMBRA el fallo, no lo insinua
    assert "ModuleNotFoundError" in r.note
    assert "TaskGroup" not in r.note
    assert r.note.isascii() and r.note.count(chr(10)) == 0  # una linea, imprimible en cp1252
    r.note.encode("cp1252")


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


def test_un_error_pegado_a_pasajes_buenos_tampoco_viaja_al_cliente():
    """La puerta de al lado del test anterior, y la falla que este PR tenia abierta.

    `lookup_from_text` solo miraba la respuesta ENTERA, asi que una respuesta PARCIAL
    (pasajes + error) pasaba el control, y el parser mandaba toda linea que no fuera
    una entrada al cuerpo de la ultima: `error: interno revento` terminaba dentro de un
    excerpt, con nombre de libro y numero de pagina. El lote se corta en esa linea.
    """
    r, _ = _leer("1. **slug-x** PDF p.5\n   texto\nerror: interno revento")
    assert r.available is True
    assert [dataclasses.astuple(x) for x in r.readings] == [("slug-x", "PDF p.5", "texto")]
    assert not any("interno revento" in x.excerpt for x in r.readings)


def test_un_error_indentado_tampoco_entra_al_extracto():
    """Indentado tiene forma de extracto; sigue siendo el mensaje interno del servidor."""
    r, _ = _leer("1. **slug-x** PDF p.5\n   texto\n   error: storage corrupto")
    assert [x.excerpt for x in r.readings] == ["texto"]


def test_un_pie_de_pagina_del_servidor_no_se_atribuye_a_un_libro():
    """No hace falta que sea un 'error:'. Cualquier linea suelta es texto que el
    servidor no saco de ningun libro, y firmarla con un libro y una pagina es peor que
    perderla: el cliente no tiene como saber que esa parte no es del libro."""
    payload = (
        "1. **slug-x** PDF p.5\n"
        "   texto del libro\n"
        "RESUMEN INTERNO DEL SERVIDOR: no mostrar"
    )
    r, _ = _leer(payload)
    assert [dataclasses.astuple(x) for x in r.readings] == [
        ("slug-x", "PDF p.5", "texto del libro")
    ]


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
    """Media cita no se inventa, pero una linea rota no se lleva puestas las buenas.

    Se afirma la TERNA completa y no solo los slugs: mirar `[x.source ...]` deja pasar
    el bug de verdad, que es el ruido del servidor ("ruido suelto que no es una
    entrada") colandose DENTRO del excerpt de la entrada de al lado, firmado con un
    libro y una pagina. Un assert que no mira el excerpt no cubre el excerpt.
    """
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
    assert [dataclasses.astuple(x) for x in r.readings] == [
        ("liu-supply-chain-analytics", "PDF p.7", "El que si sirve."),
        ("vandeput-inventory-optimization-models-simulations", "PDF p.81-82", "Y el otro."),
    ]
    assert not any("huerfano" in x.excerpt or "ruido suelto" in x.excerpt for x in r.readings)


def test_una_etiqueta_que_no_es_una_pagina_descarta_la_entrada():
    """Procedencia fabricada es peor que ninguna: si la cabecera no trae una pagina
    verificable, no hay entrada. Antes, el resto de la linea entraba tal cual como
    `page_label` y el cliente leia una pagina que no existe en ningun PDF."""
    r, _ = _leer("1. **slug-x** esto no es una pagina, es texto del libro")
    assert r.available is False and r.readings == ()


def test_una_etiqueta_larga_no_es_una_puerta_para_saltear_el_tope():
    """`page_label` era el unico campo que no pasaba por ningun tope de longitud: 900
    caracteres de texto del servidor salian intactos por ahi, sin recorte y con el
    excerpt vacio."""
    r, _ = _leer("1. **slug-x** PDF p.5 " + "a" * 900)
    assert r.available is False and r.readings == ()


def test_un_slug_desmedido_tampoco_pasa():
    """Misma puerta, otro campo: el slug mas largo del indice mide 50 caracteres."""
    r, _ = _leer("1. **" + "b" * 900 + "** PDF p.5\n   texto")
    assert r.available is False and r.readings == ()


def test_un_pasaje_sin_libro_no_es_procedencia():
    """`format_chunks` del otro repo rotula `?` cuando el chunk no trae `file_path`.
    Pagina sin libro no se puede verificar: no se publica."""
    r, _ = _leer("1. **?** PDF p.12\n   texto de un libro que no sabemos cual es")
    assert r.available is False and r.readings == ()


def test_una_etiqueta_rota_no_se_lleva_puestas_las_entradas_buenas():
    payload = (
        "1. **slug-x** pagina cualquiera inventada\n"
        "   texto que no se publica\n"
        "2. **liu-supply-chain-analytics** PDF p.7\n"
        "   El que si sirve."
    )
    r, _ = _leer(payload)
    assert [dataclasses.astuple(x) for x in r.readings] == [
        ("liu-supply-chain-analytics", "PDF p.7", "El que si sirve.")
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


#: Los cortes de linea por nombre: el archivo se mantiene ASCII y el test dice cual
#: caracter esta probando en vez de esconderlo en un literal invisible.
NL, CR, VT, FF = chr(10), chr(13), chr(11), chr(12)
LS = chr(0x2028)  # separador de linea Unicode
EM = chr(0x2014)  # em dash: no-ASCII que `_sanear` tiene que bajar a un guion


# --- 8. el piso de legibilidad: se cae el EXTRACTO, nunca el libro ni la pagina ------
#
# El agujero que tapa esta seccion. Un pasaje con el glyph-mapping del PDF destruido pasa
# TODAS las demas puertas -- slug valido, etiqueta que matchea, una sola linea, bajo el
# tope -- y se publica textual entre comillas bajo un libro y una pagina REALES. Peor: el
# propio `_sanear` lo disfraza de prosa, porque el caracter que manda el indice roto es
# U+00B7 (middle dot, 159 veces en una sola respuesta medida) y `_REEMPLAZOS` lo pasa a
# "-". O sea que Kern convierte texto irrecuperable en algo con forma de prosa en vez de
# descartarlo, y el cliente que abre Vandeput en la p.298 no encuentra nada de eso.
#
# Los cuatro cuerpos de abajo son TEXTUALES del indice vivo (recortados, no editados).
# Son la frontera del umbral medido sobre 400 pasajes de 15 libros, y por eso estan los
# sanos tambien: un piso que solo se prueba con basura no prueba nada sobre lo que tiene
# que dejar pasar.

_MEDIO = "\u00b7"  # U+00B7: el glyph que el indice roto manda donde iba una letra

# vandeput-data-science-supply-chain-forecasting, PDF p.298-299. El caso del hallazgo.
CUERPO_GLYPH_ROTO = (
    "y\u00b7 \u00b7 \u00b7 \u00b7up\u00b7lk \u00b7h\u00b7l\u00b7m\u00b7k\u00b7gbl "
    "\u00b7f\u00b7j is one of the most used libraries in \u00b7ython (it was created by "
    "\u00b7es \u00b7c\u00b7inney in 2008). \u00b7he name comes from h\u00b7f el \u00b7 ta, "
    "because it helps to order data into ta\u00b7 bles.\u00b7hink\u00b7xcel\u00b7meets"
    "\u00b7databasesin\u00b7ython."
)

# vandeput-inventory-optimization-models-simulations, PDF p.271-272. El pasaje SANO mas
# denso en rayas que se midio (0,030): un listado de codigo, legible y citable.
CUERPO_CODIGO = (
    "13.1 Safety Stock Optimization | 245 unit_shorts[t] = max(0,d[t] - max(0,hand[t-1] "
    "+ transit[t -1,0])) hand[t] = hand[t-1] - d[t] + transit[t-1,0] "
    "stockout_period[t] = hand[t] < 0 transit[t,:-1] = transit[t-1,1:] if t%R==0: "
    "actual_L = np.random.choice(L_x, 1, p=L_pmf) net = hand[t] + transit[t].sum()"
)

# heizer-render-operations-management, PDF p.519. Prosa REAL cargada de "?" -- la puerta
# que se abre al contar el "?" como relleno, porque `_sanear` lo fabrica para todo lo que
# no pudo mapear. Si el umbral se comiera esto, se estaria comiendo prosa de verdad.
CUERPO_PREGUNTAS = (
    "480 PART 3 | MANAGING OPERATIONS Discussion Questions 1. What is the difference "
    "between \u201cunique-event\u201d risk and \u201csuper-event\u201d risk? 2. If the "
    "probability of a \u201csuper-event\u201d increases, does the \u201cunique-event\u201d "
    "risk increase or decrease in importance? Why?"
)

# vollmann-manufacturing-planning-control, PDF p.412-413. No esta roto: es el volcado de
# una FIGURA (un reporte con rayas de cuadricula). Tampoco es una cita que alguien pueda
# leer, y el mismo piso lo saca.
CUERPO_FIGURA = (
    "FIGURE 10.18 Abbott Short-Term Shipping Information MPDR0030 DISTRIBUTION "
    "REQUIREMENTS PLAN PAGE 206 DC 21 - MONTREAL DIVISION 2 LIST/SIZE QUANTITY PALLET "
    "WEIGHT CUBE --------------- PAST DUE --------------- WEEK 1 --------------- "
    "XYZ-200 0 0.0 0.0 0.0 0 0.0 0.0 0.0"
)


def _una(source: str, etiqueta: str, cuerpo: str) -> Reading:
    """Una sola entrada leida del cable, con el cuerpo indentado como lo manda el server."""
    (leida,) = lectura.parse_readings(f"1. **{source}** {etiqueta}{NL}   {cuerpo}{NL}")
    return leida


def test_un_pasaje_con_el_glyph_mapping_roto_no_se_publica_entre_comillas():
    """El hallazgo, textual. Lo que se cae es el EXTRACTO; la procedencia queda entera."""
    leida = _una("vandeput-data-science-supply-chain-forecasting", "PDF p.298-299",
                 CUERPO_GLYPH_ROTO)

    assert leida.excerpt == ""
    assert leida.source == "vandeput-data-science-supply-chain-forecasting"
    assert leida.page_label == "PDF p.298-299"


def test_la_entrada_ilegible_sigue_siendo_procedencia_y_no_se_descarta():
    """Media procedencia no se inventa, pero libro + pagina YA es procedencia completa:
    tirar la entrada entera le sacaria al cliente una referencia que si puede abrir."""
    r = lookup_from_text(
        f"1. **vandeput-data-science-supply-chain-forecasting** PDF p.298-299{NL}"
        f"   {CUERPO_GLYPH_ROTO}{NL}"
    )
    assert r.available is True
    assert len(r.readings) == 1
    assert r.readings[0].page_label == "PDF p.298-299"


def test_el_piso_no_se_come_el_listado_de_codigo_mas_denso_que_se_midio():
    """El pasaje sano con MAS rayas de los 400 medidos (0,030 contra un umbral de 0,04).
    Es la frontera de verdad del umbral: si alguien lo baja, esto se rompe primero."""
    leida = _una("vandeput-inventory-optimization-models-simulations", "PDF p.271-272",
                 CUERPO_CODIGO)
    assert leida.excerpt.startswith("13.1 Safety Stock Optimization")
    assert "unit_shorts[t]" in leida.excerpt


def test_una_pagina_de_preguntas_no_es_ilegible_por_tener_signos_de_pregunta():
    """El "?" cuenta como relleno porque `_sanear` lo FABRICA, no porque sea sospechoso.
    Prosa real llena de preguntas tiene que pasar, y esta es prosa real."""
    leida = _una("heizer-render-operations-management", "PDF p.519", CUERPO_PREGUNTAS)
    assert "Discussion Questions" in leida.excerpt
    assert leida.excerpt.count("?") >= 3  # y sigue siendo legible con todos ellos


def test_un_volcado_de_figura_pierde_el_extracto_pero_no_el_libro():
    """No esta roto, pero tampoco es una frase que alguien pueda leer y verificar."""
    leida = _una("vollmann-manufacturing-planning-control", "PDF p.412-413", CUERPO_FIGURA)
    assert leida.excerpt == ""
    assert leida.page_label == "PDF p.412-413"


def test_la_metrica_obvia_no_habria_servido():
    """Por que el piso cuenta relleno FABRICADO y no "letras contra todo lo demas".

    Medido, no argumentado: el pasaje roto tiene MAS proporcion de letras que el listado
    de codigo sano. Ningun umbral sobre esa metrica los separa, porque estan del reves:
    o deja pasar la basura, o se come el codigo.
    """
    def letras(texto: str) -> float:
        sin_blanco = [c for c in texto if not c.isspace()]
        return sum(c.isalpha() for c in sin_blanco) / len(sin_blanco)

    roto = _una("vandeput-data-science-supply-chain-forecasting", "PDF p.1",
                CUERPO_GLYPH_ROTO)
    sano = _una("vandeput-inventory-optimization-models-simulations", "PDF p.1",
                CUERPO_CODIGO)
    assert roto.excerpt == "" and sano.excerpt  # el piso SI los separa
    # y la metrica obvia los habria ordenado al reves:
    assert letras(lectura._sanear(CUERPO_GLYPH_ROTO)) > letras(lectura._sanear(CUERPO_CODIGO))


def test_el_umbral_es_el_que_se_midio():
    """El numero sale de 400 pasajes del indice vivo (p90 sano = 0,012, p95 = 0,019;
    el libro roto arranca en 0,028). Cambiarlo es una decision, no un ajuste."""
    assert lectura.MAX_RELLENO_EXCERPT == 0.04


def test_el_umbral_corta_donde_dice_y_no_un_caracter_antes():
    """La frontera exacta, sin depender de ningun pasaje real: 4 de 100 pasa, 5 no."""
    justo = "a" * 96 + "-" * 4
    pasado = "a" * 95 + "-" * 5
    assert _una("liu-supply-chain-analytics", "PDF p.7", justo).excerpt == justo
    assert _una("liu-supply-chain-analytics", "PDF p.7", pasado).excerpt == ""


def test_un_extracto_que_es_puro_relleno_jamas_llega_al_cliente():
    assert _una("liu-supply-chain-analytics", "PDF p.7", "- - ? - ? -").excerpt == ""


def test_una_entrada_sin_cuerpo_no_es_ilegible_sino_ausente():
    """El caso vacio no puede caerse por una division por cero ni cambiar de conducta:
    ya era una entrada valida sin extracto antes de que este piso existiera."""
    (leida,) = lectura.parse_readings("1. **liu-supply-chain-analytics** PDF p.7")
    assert leida.excerpt == ""
    assert leida.source == "liu-supply-chain-analytics"


def test_el_piso_corre_sobre_el_texto_que_se_publica_no_sobre_el_crudo():
    """Se mide despues del saneo y del corte, que es lo unico que el cliente ve.

    No es un detalle: en el crudo el relleno todavia es U+00B7 y no una raya, asi que
    medir antes contaria CERO rayas sobre el pasaje mas roto del indice. Y medir despues
    del corte ayuda, porque el encabezado destruido de un PDF roto esta al principio:
    aca entra una cabeza ilegible con una cola larga y sana, se publica la cabeza, y la
    cabeza es la que no se puede leer.
    """
    cabeza = _MEDIO.join("abcdefghij") * 4
    cola = " " + "palabra sana de relleno " * 60
    assert _una("liu-supply-chain-analytics", "PDF p.7", cabeza + cola).excerpt == ""


# --- el contrato de los tipos -------------------------------------------------------


def test_los_dos_tipos_son_inmutables():
    r = Reading(source="x", page_label="PDF p.1", excerpt="y")
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.source = "z"  # type: ignore[misc]
    lookup = ReadingLookup(available=False, readings=(), note="porque si")
    with pytest.raises(dataclasses.FrozenInstanceError):
        lookup.available = True  # type: ignore[misc]


def test_un_reading_no_se_puede_construir_por_encima_del_tope_de_copyright():
    """El tope es del TIPO, no del parser: `src/deliverable.py` renderiza lo que reciba.

    Sin esto, `Reading("libro", "PDF p.1", "x" * 5000)` renderizaba 5.000 caracteres de
    un PDF de libgen entre comillas en el entregable del cliente -- el tope existia pero
    solo lo aplicaba `_cortar`, adentro de `parse_readings`. El render NO agrega un
    segundo recorte a proposito (seria una segunda verdad sobre cuanto se puede copiar),
    asi que la unica verdad se hace cumplir donde el valor se construye.
    """
    with pytest.raises(ValueError, match=str(MAX_EXCERPT_CHARS)):
        Reading(source="libro", page_label="PDF p.1", excerpt="x" * (MAX_EXCERPT_CHARS + 1))
    justo = Reading(source="libro", page_label="PDF p.1", excerpt="x" * MAX_EXCERPT_CHARS)
    assert len(justo.excerpt) == MAX_EXCERPT_CHARS


def test_un_reading_no_se_puede_construir_con_un_salto_de_linea():
    """Una entrada de procedencia es UNA linea del markdown.

    Un salto la parte en dos: la cola sale como prosa suelta que ya no cuelga de ningun
    libro ni de ninguna pagina, con la comilla de cierre colgando en otro renglon. Se
    prueban los tres campos y tambien el salto PEGADO AL FINAL, que es el que una
    busqueda casera del caracter suele dejar pasar por venir de un ``readlines()``.
    """
    for campo in ("source", "page_label", "excerpt"):
        args = {"source": "libro", "page_label": "PDF p.1", "excerpt": "x"}
        args[campo] = args[campo] + NL + "cola desprendida"
        with pytest.raises(ValueError, match="salto de linea"):
            Reading(**args)
    for corte in (NL, CR, VT, FF, LS):  # LF, CR, tab vertical, salto de pagina, LS
        with pytest.raises(ValueError, match="salto de linea"):
            Reading(source="libro", page_label="PDF p.1", excerpt="cuerpo" + corte)


def test_un_reading_sin_libro_o_sin_pagina_no_existe():
    """Media procedencia no se cita: sin libro no hay que verificar, sin pagina no hay
    donde. El extracto SI es opcional -- libro + pagina ya es procedencia completa."""
    for source, page in (("", "PDF p.1"), ("   ", "PDF p.1"), ("libro", ""), ("libro", "  ")):
        with pytest.raises(ValueError, match="media procedencia"):
            Reading(source=source, page_label=page, excerpt="x")
    assert Reading(source="libro", page_label="PDF p.5", excerpt="").excerpt == ""


def test_un_reading_no_publica_texto_ajeno_como_nombre_de_libro():
    """Misma puerta que el tope del extracto, otro campo: el slug sale de la cabecera
    que manda el servidor. `1. **<900 caracteres>** PDF p.5` publica 900 caracteres de
    texto ajeno en negrita como si fueran el titulo de un libro."""
    with pytest.raises(ValueError, match=str(lectura.MAX_SOURCE_CHARS)):
        Reading(source="L" * (lectura.MAX_SOURCE_CHARS + 1), page_label="PDF p.1", excerpt="x")


def test_el_parser_nunca_construye_un_reading_invalido_por_mas_sucio_que_venga_el_cable():
    """La validacion del tipo NO rompe el "nunca levanta" del modulo.

    El unico productor vivo es `parse_readings`, que corta, sanea y descarta ANTES de
    construir. Se le tira encima lo peor que puede llegar del cable -- un pasaje enorme,
    partido en varias lineas, con centinelas y no-ASCII -- y tiene que salir un lote
    valido, sin excepcion.

    La densidad de rayas del cuerpo es REALISTA a proposito (una en 320 caracteres; el
    p95 medido del indice vivo es 0,019). La version anterior repetia "palabra EM
    larguisima", o sea 5% de rayas, y con el piso de legibilidad
    (`MAX_RELLENO_EXCERPT`) ese cuerpo ya no llega al extracto: el test dejaba de probar
    el corte por longitud, que es para lo que existe.
    """
    cuerpo = ("El margen " + EM + " medido " + "palabra larguisima de relleno " * 300).strip()
    crudo = NL.join(["1. **vandeput** PDF p.9", "   " + cuerpo,
                     "   segunda linea del cuerpo", ""])
    (leida,) = lectura.parse_readings(crudo)
    assert len(leida.excerpt) <= MAX_EXCERPT_CHARS
    assert leida.excerpt.splitlines() == [leida.excerpt]
    assert lookup_from_text(crudo).available is True


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
