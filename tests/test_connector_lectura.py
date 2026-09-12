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


# -- el indice por RED: su propia app, encendida a pedido ---------------------


def test_la_url_por_red_se_usa_y_no_se_mira_el_interprete_local(monkeypatch):
    """Con ``KERN_LECTURA_URL`` el conector habla por red y ni mira el interprete.

    En produccion el indice NO vive adentro de Kern: una consulta pica en 1.595 MB
    medidos y Kern usa 377 MB, asi que co-locarlos obligaria a una VM de 4 GB las 24
    horas por un pico que solo ocurre mientras corre un entregable. El indice tiene su
    propia app, que se apaga sola. El puerto 9 (descarte) nunca escucha: lo que se
    afirma es cual de los dos caminos se tomo, no que el servidor conteste.
    """
    monkeypatch.delenv(lectura.ENV_INTERPRETE, raising=False)
    monkeypatch.setenv(lectura.ENV_URL, "http://127.0.0.1:9/mcp")

    r = lectura.read_passages(["safety", "stock"], timeout_s=5.0)

    assert r.available is False
    assert r.readings == ()
    assert lectura.ENV_INTERPRETE not in r.note, (
        "se quejo del interprete local teniendo URL de red: " + r.note
    )
    # Sin esto el test pasa igual con el caller ROTO: un TypeError por construir mal el
    # cliente HTTP tambien termina en available=False, y se ve identico a un servidor
    # apagado. Paso de verdad al migrar al API nuevo de `streamable_http_client`, que
    # cambio `timeout=` por `http_client=`. La nota tiene que hablar de la CONEXION.
    for defecto in ("TypeError", "AttributeError", "NameError", "ImportError"):
        assert defecto not in r.note, (
            f"el camino por red no fallo por conexion sino por codigo ({defecto}): {r.note}"
        )


def test_sin_url_ni_interprete_la_nota_nombra_los_dos_caminos(monkeypatch):
    """La nota tiene que decirle a un humano COMO se configura, y hay dos formas."""
    monkeypatch.delenv(lectura.ENV_INTERPRETE, raising=False)
    monkeypatch.delenv(lectura.ENV_URL, raising=False)

    r = lectura.read_passages(["safety", "stock"])

    assert r.available is False
    assert lectura.ENV_URL in r.note
    assert lectura.ENV_INTERPRETE in r.note


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
    r, _ = _leer("1. **slug-x** PDF p.5\n   texto del libro\nerror: interno revento")
    assert r.available is True
    assert [dataclasses.astuple(x) for x in r.readings] == [
        ("slug-x", "PDF p.5", "texto del libro")
    ]
    assert not any("interno revento" in x.excerpt for x in r.readings)


def test_un_error_indentado_tampoco_entra_al_extracto():
    """Indentado tiene forma de extracto; sigue siendo el mensaje interno del servidor."""
    r, _ = _leer("1. **slug-x** PDF p.5\n   texto del libro\n   error: storage corrupto")
    assert [x.excerpt for x in r.readings] == ["texto del libro"]


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
        "   Con un extracto."
    )
    r, _ = _leer(payload)
    assert [(x.source, x.excerpt) for x in r.readings] == [
        ("ivanov-global-supply-chain-operations", ""),
        ("liu-supply-chain-analytics", "Con un extracto."),
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
    texto (un solo caracter sigma pasa a cinco).

    Los sigmas van SEPARADOS por espacio, no pegados: pegados el extracto entero seria un
    unico token y caeria por el piso de palabras (`MIN_PALABRAS_CITABLES`) antes de
    llegar al corte, que es lo que este test mide. Un extracto de un solo token de 1.000
    caracteres tampoco era representativo de nada que mande el indice.
    """
    sigma = ("\u03c3 " * 200).strip()  # 200 caracteres que al sanear pasan a 1000
    r, _ = _leer(f"1. **liu-supply-chain-analytics** PDF p.7\n   {sigma}")
    assert len(r.readings[0].excerpt) <= MAX_EXCERPT_CHARS


# --- 7. como viaja la consulta ------------------------------------------------------


def test_las_keywords_viajan_unidas_por_espacio():
    """El `k` que viaja NO es el que se pidio: es `k + HOLGURA_PEDIDO`, porque el piso de
    calidad descarta mobiliario y sin holgura cada descarte seria un pasaje menos (ver
    la seccion 9). Lo que se queda el llamador sigue siendo `k`."""
    srv = _Servidor(CABLE_REAL)
    with srv as call:
        read_with_caller(call, ["safety", "stock", "alpha service level"], k=5)
    assert srv.vistos == [{"query": "safety stock alpha service level",
                           "k": 5 + lectura.HOLGURA_PEDIDO, "sources": None}]


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
    """La frontera exacta, sin depender de ningun pasaje real: 4 de 100 pasa, 5 no.

    Los cuerpos son VARIAS palabras a proposito: uno solo de 100 caracteres cae antes por
    el piso de palabras (`MIN_PALABRAS_CITABLES`) y este test dejaria de medir la
    frontera del relleno, que es para lo que existe. Los espacios no cuentan como
    caracteres: `_es_ilegible` mide solo sobre los no-blancos, asi que la razon sigue
    siendo exactamente 4/100 y 5/100.
    """
    justo = " ".join(["a" * 12] * 8) + "-" * 4          # 96 letras + 4 rayas = 0,04
    pasado = " ".join(["a" * 12] * 7 + ["a" * 11]) + "-" * 5   # 95 + 5 = 0,05
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


# --- 9. el piso de calidad: mobiliario del libro y del sitio -------------------------
#
# POR QUE EXISTE ESTA SECCION. Una corrida REAL contra el indice devolvio 3 pasajes y
# los 3 eran mobiliario: una barra lateral de blog, un extracto con el pie de imprenta
# del maquetador adentro, y un indice onomastico del final de un libro. El repo del
# indice (`kern-lectura`) ya limpia lo suyo; esto es el CINTURON de este lado, para que
# Kern no dependa de que el otro repo este limpio. Defensa en profundidad, no duplicacion.
#
# TODOS los cuerpos de abajo son TEXTUALES del indice vivo (recortados, no editados),
# tirados con `search_passages` el 2026-09-12. Los tres primeros son los CONTROLES
# NEGATIVOS -- tablas densas en numeros que tienen que SOBREVIVIR --, y estan aca porque
# un piso que solo se prueba con basura no prueba nada sobre lo que tiene que dejar pasar.

# vollmann-manufacturing-planning-control, PDF p.262. Un MPS de planificacion de
# capacidad: 26 numeros sueltos en dos renglones y ninguna coma. Contenido citable.
TABLA_MPS = (
    "246 Chapter 7 Capacity Planning and Management Master production schedule "
    "(in units): Period End Product 1 2 3 4 5 6 7 8 9 10 11 12 13 Total "
    "A 33 33 33 40 40 40 30 30 30 37 37 37 37 457 "
    "B 17 17 17 13 13 13 25 25 25 27 27 27 27 273"
)

# jacobs-chase-operations-supply-chain-management, PDF p.625. La solucion de un problema
# de programacion de centros de trabajo -- y trae el pie de imprenta del maquetador
# ADENTRO, que es exactamente el hallazgo que motivo esta tarea.
TABLA_SCHEDULING = (
    "596 Section 4 Supply and Demand Planning and Control "
    "jac66107_ch22_590-621.indd 596 12/16/16 02:08 PM "
    "SOLUTION FCFS RULE: The FCFS rule results in the following flow times: "
    "FCFS Schedule Job Sequence Processing Time (Days) Due Date (Days Hence) "
    "Flow Time (Days) A 3 5 0 + 3 = 3 B 4 6 3 + 4 = 7 C 2 7 7 + 2 = 9"
)

# vollmann-manufacturing-planning-control, PDF p.192. Una serie de ordenes de cliente.
# La ligadura fi va escapada para que este archivo siga siendo ASCII (ver seccion 5).
TABLA_ORDENES = (
    "Booked customer orders (already con\ufb01rmed): 95 units in week 1. "
    "105 units in week 2. 70 units in week 3. 10 units in week 5. "
    "Master production schedule: 200 units to be completed at the start of week 1. "
    "200 units to be completed at the start of week 3."
)

# liu-supply-chain-analytics, PDF p.383. El indice onomastico del final del libro: un
# termino y despues la lista de paginas donde aparece. Las rayas son en-dash (U+2013).
INDICE_ONOMASTICO = (
    "372 Collaboration, 5, 6, 23, 24, 113\u2013115, 159, 164, 167, 168, 173, 179 "
    "Combination, 25, 26, 54, 56\u201361, 72, 74, 78, 79, 89, 108, 176, 209, 212, 252 "
    "Competitive advantage, 6, 8, 9, 12, 13, 17, 119, 165, 179"
)

# sop-isotracker-iso-9001-document-review, PDF p.2. La barra lateral del blog. Ojo: NO
# arranca con el marcador -- arranca con la cola de un parrafo de verdad y la navegacion
# empieza en el medio. Una regla que solo mire el principio del extracto no la ve.
BARRA_LATERAL = (
    "advice on how often to review documents for ISO 9001. Recent posts "
    "Improving Quality Management Reviews: A Practical Guide for ISO 9001 Compliance "
    "7 Quality Metrics Every Manufacturer Should Track in 2026 "
    "What Types Of Businesses Need a QMS? GFSI Requirements for Root Cause Analysis"
)

# Una formula, escrita como la escriben los libros: simbolos espaciados y la sustitucion
# numerica al lado. El control negativo del piso de palabras.
FORMULA = "EOQ = raiz(2 x D x S / H) = raiz(2 x 1200 x 75 / 3.6) = 223.6 units per order"

PROSA = (
    "Safety stock is extra inventory that is carried for protection in case the demand "
    "for an item is greater than expected during the replenishment lead time."
)


def _cable(cuerpo: str, source: str = "liu-supply-chain-analytics",
           etiqueta: str = "PDF p.7") -> str:
    """Una entrada con su cuerpo indentado, como la manda el servidor."""
    return f"1. **{source}** {etiqueta}{NL}   {cuerpo}{NL}"


# -- (a) la firma del indice onomastico ----------------------------------------------


def test_un_indice_onomastico_real_no_ocupa_un_slot():
    """El hallazgo, textual. Un indice del final del libro no es una cita: es una lista
    de numeros de pagina, y el cliente que abre la p.383 de Liu no encuentra teoria."""
    assert lectura.parse_readings(_cable(INDICE_ONOMASTICO)) == ()


def test_la_firma_del_indice_es_cuatro_numeros_y_no_tres():
    """La frontera medida, sin depender de ningun pasaje real: con tres numeros separados
    por coma todavia hay prosa normal (Jacobs & Chase: "late by 1, 2, 6, and 14 days"),
    con cuatro ya es la forma del indice."""
    assert lectura.es_inutilizable_como_cita("Collaboration, 5, 6, 23, 24 y algo mas")
    assert not lectura.es_inutilizable_como_cita("Jobs B, C y D llegaron tarde 1, 2, 6 dias")


def test_la_prosa_de_jacobs_con_tres_numeros_seguidos_sobrevive():
    """El caso que casi se rompe, textual del indice vivo: la frase que cierra la
    solucion FCFS enumera tres numeros con coma y el cuarto viene detras de un "and"."""
    cuerpo = ("Comparing the due date of each job with its flow time, we observe that "
              "only Job A will be on time. Jobs B, C, D, and E will be late by "
              "1, 2, 6, and 14 days, respectively.")
    assert not lectura.es_inutilizable_como_cita(cuerpo)
    assert lectura.parse_readings(_cable(cuerpo))[0].excerpt.startswith("Comparing")


# -- (b) mobiliario: navegacion de sitio y pie de imprenta ---------------------------


def test_la_barra_lateral_de_un_blog_no_ocupa_un_slot():
    """El otro hallazgo, textual. El marcador cae en el MEDIO del extracto, no al
    principio: la regla tiene que mirar todo el extracto."""
    assert lectura.parse_readings(_cable(BARRA_LATERAL,
                                         "sop-isotracker-iso-9001-document-review",
                                         "PDF p.2")) == ()


def test_la_cabecera_de_una_pagina_raspada_tampoco_ocupa_un_slot():
    """Textual de una corrida real de `read_passages` contra el indice vivo (2026-09-12).

    Con la lista de marcadores original -- la que sale del hallazgo -- esta consulta
    (`["iso 9001 document review", "document control", "quality manual"]`, k=3) devolvia
    DOS pasajes asi sobre tres: el encabezado de la pagina web, no el articulo. Ninguno
    de los seis marcadores de blog lo tocaba. El enlace de salto ("skip to main content")
    es lo que si esta siempre, y no existe en la prosa de ningun libro.
    """
    cabecera = (
        "Managing Controlled Documents ISO 9001 Skip to main content Skip to footer "
        "info@compliantltd.com Call: 0333 456 5000 Search About Us Services ISO Services"
    )
    assert lectura.parse_readings(_cable(
        cabecera, "sop-compliant-controlled-documents-iso-9001", "PDF p.1")) == ()


def test_cada_marcador_de_navegacion_descarta_por_su_cuenta():
    """Los seis, uno por uno: si alguien saca uno de la lista, se rompe aca."""
    for marcador in lectura.NAVEGACION_DE_SITIO:
        cuerpo = f"Texto de la nota que si es del articulo. {marcador} y lo que sigue."
        assert lectura.parse_readings(_cable(cuerpo)) == (), marcador


def test_el_pie_de_imprenta_se_saca_del_extracto_pero_no_se_lleva_el_pasaje():
    """La correccion medida, y el motivo por el que el pie de imprenta NO descarta.

    Medido contra el indice vivo el 2026-09-12: de 11 pasajes de
    `jacobs-chase-operations-supply-chain-management` tirados con dos consultas
    distintas, 10 traian el pie de imprenta completo -- esta impreso en CASI TODAS las
    paginas de ese libro. Tratarlo como motivo de descarte borraria un manual entero del
    corpus, incluida ESTA tabla, que es uno de los tres controles negativos que tienen
    que sobrevivir. El mobiliario se SACA del extracto (igual que los centinelas de
    pagina), no se usa como veredicto sobre el pasaje.
    """
    (leida,) = lectura.parse_readings(_cable(
        TABLA_SCHEDULING, "jacobs-chase-operations-supply-chain-management", "PDF p.625"))

    assert ".indd" not in leida.excerpt
    assert "12/16/16" not in leida.excerpt and "02:08 PM" not in leida.excerpt
    assert "SOLUTION FCFS RULE" in leida.excerpt      # y el contenido sigue entero
    assert leida.page_label == "PDF p.625"


def test_un_pasaje_que_es_solo_el_pie_de_imprenta_queda_como_libro_y_pagina():
    """Sacar el mobiliario nunca CREA un descarte: deja la entrada sin extracto.

    La pagina existe y tiene texto -- el chunker corto mal, nada mas --, asi que libro y
    pagina siguen siendo una referencia que el cliente puede abrir. Es la misma regla que
    ya tenia el modulo para el PDF con el glyph-mapping roto: vacio es AUSENTE, no basura.
    """
    (leida,) = lectura.parse_readings(_cable(
        "jac66107_ch22_590-621.indd 609 12/16/16 02:08 PM",
        "jacobs-chase-operations-supply-chain-management", "PDF p.638"))
    assert leida.excerpt == ""
    assert leida.page_label == "PDF p.638"


def test_un_pie_de_imprenta_a_medias_no_se_toca():
    """Las cuatro partes juntas son las que dan cero falsos positivos. Una fecha suelta o
    un nombre de archivo suelto pueden ser texto del libro, y no se tocan."""
    cuerpo = "El archivo maestro se llama pedidos.indd y se reviso el 12/16/16 sin falta."
    assert ".indd" in lectura.parse_readings(_cable(cuerpo))[0].excerpt


# -- (c) lo que queda despues de todo eso -------------------------------------------


def test_un_extracto_de_dos_palabras_no_es_una_cita():
    """Lo que se publica es el extracto ENTRE COMILLAS con el libro y la pagina al lado:
    un par de comillas alrededor de "FIGURE 10.18" afirma una cita que no dice nada."""
    for resto in ("FIGURE 10.18", "480 PART 3", "Chapter 7 133", "206 21 2"):
        assert lectura.parse_readings(_cable(resto)) == (), resto


def test_tres_palabras_ya_son_una_cita():
    assert lectura.parse_readings(_cable("Corto y entero."))[0].excerpt == "Corto y entero."


def test_el_minimo_de_palabras_es_el_que_se_escribio():
    """Sujeto, verbo y complemento: abajo de tres no hay afirmacion, hay rotulo."""
    assert lectura.MIN_PALABRAS_CITABLES == 3


def test_un_extracto_vacio_no_cae_por_el_piso_de_palabras():
    """Cero palabras NO es "muy pocas palabras": es ausencia, y ya era una entrada valida
    antes de que este piso existiera. Un extracto vacio no afirma nada y deja en pie una
    referencia que el cliente puede abrir; uno de dos palabras afirma entre comillas."""
    assert not lectura.es_inutilizable_como_cita("")
    assert not lectura.es_inutilizable_como_cita("   ")
    (leida,) = lectura.parse_readings("1. **liu-supply-chain-analytics** PDF p.7")
    assert leida.excerpt == ""


# -- los controles negativos, todos juntos -------------------------------------------


def test_los_controles_negativos_sobreviven_al_piso_entero():
    """Prosa, una formula y las TRES tablas reales densas en numeros. Si alguien endurece
    cualquiera de las tres razones, lo primero que se rompe es esto."""
    casos = {
        "prosa": PROSA,
        "formula": FORMULA,
        "mps de vollmann": TABLA_MPS,
        "scheduling de jacobs & chase": TABLA_SCHEDULING,
        "ordenes de cliente de vollmann": TABLA_ORDENES,
    }
    for nombre, cuerpo in casos.items():
        assert not lectura.es_inutilizable_como_cita(lectura._sanear(cuerpo)), nombre
        lecturas = lectura.parse_readings(_cable(cuerpo))
        assert len(lecturas) == 1 and lecturas[0].excerpt, nombre


# -- se descarta, no se degrada ------------------------------------------------------


def test_el_mobiliario_no_deja_procedencia_huerfana():
    """Un pasaje que no pasa el piso NO puede ocupar un slot, ni siquiera como libro +
    pagina sin extracto. La diferencia con el piso de legibilidad es cual es el defecto:
    alla la PAGINA tiene texto de verdad y el PDF lo destruyo (la referencia sirve), aca
    la pagina ES el mobiliario -- el indice del final, la barra lateral del sitio -- y
    mandarlo como referencia es mandar al cliente a una pagina que no habla del tema.
    """
    crudo = NL.join([
        f"1. **liu-supply-chain-analytics** PDF p.383{NL}   {INDICE_ONOMASTICO}",
        f"2. **vollmann-manufacturing-planning-control** PDF p.262{NL}   {TABLA_MPS}",
    ])
    r = lookup_from_text(crudo)

    assert r.available is True
    assert [x.page_label for x in r.readings] == ["PDF p.262"]
    assert "PDF p.383" not in str(r.readings)


def test_un_lote_entero_de_mobiliario_no_queda_disponible_y_no_levanta():
    """El caso de la corrida real: 3 de 3 eran mobiliario. Se contesta un estado, no una
    excepcion y tampoco tres citas vacias."""
    crudo = NL.join([
        f"1. **liu-supply-chain-analytics** PDF p.383{NL}   {INDICE_ONOMASTICO}",
        f"2. **sop-isotracker-iso-9001-document-review** PDF p.2{NL}   {BARRA_LATERAL}",
        f"3. **vollmann-manufacturing-planning-control** PDF p.1{NL}   FIGURE 10.18",
    ])
    r = lookup_from_text(crudo)
    assert r.available is False and r.readings == ()
    assert r.note


# -- pedir de mas, en UNA sola llamada -----------------------------------------------


def _lote(*cuerpos: str) -> str:
    return NL.join(f"{i}. **libro-{i}** PDF p.{i}{NL}   {c}"
                   for i, c in enumerate(cuerpos, start=1))


def test_se_pide_de_mas_para_que_el_descarte_no_cueste_un_pasaje_bueno():
    """El punto entero de la holgura: con 3 pedidos y 3 de mobiliario en el medio, el
    cliente igual recibe 3 pasajes buenos en vez de quedarse sin ninguno."""
    payload = _lote(INDICE_ONOMASTICO, PROSA, BARRA_LATERAL, TABLA_MPS,
                    "FIGURE 10.18", TABLA_ORDENES)
    r, _ = _leer(payload, k=3)

    assert r.available is True
    assert len(r.readings) == 3
    assert [x.page_label for x in r.readings] == ["PDF p.2", "PDF p.4", "PDF p.6"]


def test_la_holgura_viaja_en_la_misma_unica_llamada():
    """La holgura no puede convertirse en un costo escondido. Cada consulta al indice
    levanta y baja el servidor entero (16,8 s en frio, ~1,6 GB de RSS): una segunda
    llamada para reponer lo descartado costaria mas que todo lo que esto ahorra."""
    srv = _Servidor(_lote(PROSA, TABLA_MPS, TABLA_ORDENES))
    with srv as call:
        read_with_caller(call, ["safety stock"], k=3)

    assert len(srv.vistos) == 1
    assert srv.vistos[0]["k"] == 3 + lectura.HOLGURA_PEDIDO


def test_el_tope_se_respeta_aunque_el_servidor_mande_la_holgura_entera():
    """Lo que se pide de mas es para DESCARTAR, no para publicar: si vuelven seis buenos
    con k=3, salen tres."""
    payload = _lote(*([PROSA] * 6))
    r, _ = _leer(payload, k=3)
    assert len(r.readings) == 3
    assert r.note.startswith("3 pasaje")


def test_menos_de_k_no_es_un_error():
    """El servidor puede devolver menos de lo pedido (un `sources` acotado, un corpus
    chico). Dos pasajes son dos pasajes, no un fallo."""
    r, _ = _leer(_lote(PROSA, TABLA_MPS), k=3)
    assert r.available is True and len(r.readings) == 2


def test_la_holgura_es_la_que_se_escribio():
    """Va literal: escrito contra la constante el assert es una tautologia. El numero es
    el tamanio de UN pedido del orchestrator (`_LECTURAS_POR_DECK` = 3), que es lo que la
    corrida real perdio entero."""
    assert lectura.HOLGURA_PEDIDO == 3
