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
import re
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


def _primeras(payload: str, k: int) -> str:
    """Las primeras ``k`` entradas del payload, como hace el servidor de verdad.

    El fake TIENE que respetar `k` o la holgura no se puede probar: con un fake que
    devuelve el payload entero pase lo que pase, `read_with_caller` recibe los seis
    pasajes aunque haya pedido tres, y el test de la holgura pasa igual con
    ``HOLGURA_PEDIDO = 0`` -- que es exactamente el mutante que tiene que morir.
    Los strings que no son una lista de pasajes ("(sin pasajes)", "error: ...") viajan
    tal cual: no tienen entradas que recortar.
    """
    bloques = re.split(r"(?m)^(?=\d+\.\s)", payload)
    entradas = [b for b in bloques if re.match(r"^\d+\.\s", b)]
    if not entradas:
        return payload
    cabecera = "" if re.match(r"^\d+\.\s", bloques[0]) else bloques[0]
    return cabecera + "".join(entradas[:k])


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
            return _primeras(payload, k)

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
                           "k": 8, "sources": None}]  # 5 pedidos + 3 de holgura, literal


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

# Los TRES indices siguientes son el estilo DOMINANTE del corpus, textuales del indice
# vivo (2026-09-12) y recortados, no editados: un termino, coma, su pagina, y de vuelta.
# Ninguno de los tres lo reconocia la primera version de este piso, que pedia cuatro
# paginas con coma detras de un mismo termino -- una forma que solo tiene Liu.

# chopra-meindl-supply-chain-management, PDF p.522. La primera pagina del indice.
INDICE_CHOPRA = (
    "INDEX A Abbott, 510 Absolute deviation, 205 Accuracy of information, 269\u2013270 "
    "Acid Rain Program, 508 Acquisition costs, 455 Actual average flow/cycle time, 61 "
    "Adaptability, distribution networks, 112\u2013113 Adaptive forecasting, 199\u2013204"
)

# heizer-render-operations-management, PDF p.894. Arranca con el texto literal
# "GENERAL INDEX" y usa subentradas ("... and, 411").
INDICE_HEIZER = (
    "GENERAL INDEX I15 scheduling services and, 618 zero-wait ER guarantee, 766 "
    "Hotels MRP and, 584 site selection, location strategies and, 351 technology "
    "changes, 298 House of quality, 166 \u2013 169 HP Project, 66 Human resource, job "
    "design, work management and, 407 \u2013 440"
)

# ivanov-global-supply-chain-operations, PDF p.593. Termina el abecedario y trae
# entradas con parentesis antes de la coma ("(VMI), 21").
INDICE_IVANOV = (
    "Value stream mapping, 280 Vehicle routing, 427 \u2013439 Vendor-managed inventory "
    "(VMI), 21, 48, 95\u201398 Virtual enterprises, 62 Virtual reality, 504 Virtual "
    "store, 37 Visibility, 46, 47 W Wagner\u2013Whitin, 397 Waiting time, 440, 441"
)

# heizer-render-operations-management, PDF p.891. LA PAGINA DE INDICE MAS POBRE de los
# 521 pasajes medidos: 5 marcas, una sola por encima del corte. Textual del indice vivo
# (2026-09-12), y es el pin de ARRIBA de la frontera: puntua poco porque la mitad del
# extracto son remisiones ("See Walt Disney Parks and Resorts") sin numero de pagina.
INDICE_HEIZER_MAS_POBRE = (
    "I12 GENERAL INDEX Diseconomies of scale, 311 Disney Parks and Resorts. See Walt "
    "Disney Parks and Resorts Disney World. See Walt Disney Parks and Resorts "
    "Disneyland, 375 Dispatching jobs, priority rules and, 611 - 612 Distance "
    "reduction, JIT layout and, 643 Distribution management, supply chain management "
    "and, 459"
)

# jacobs-chase-operations-supply-chain-management, PDF p.50. CONTENIDO real -- el
# enunciado de un ejercicio de finanzas -- y el falso positivo que encontro la revision
# de la ronda 2 sobre el corpus vivo: los separadores de MILES ("$25,240,000") tienen la
# forma exacta de una lista de paginas de indice ("25, 240, 000"), y el pasaje puntuaba 6
# marcas. Lo que lo salva es que un separador de miles NO lleva espacio detras de la coma.
PROSA_CON_IMPORTES = (
    "Introduction Chapter 1 21 9. Consider the following financial data from the past "
    "year for Midwest Outdoor Equipment Corporation. (Answers in Appendix D) LO1-4 "
    "Gross income $25,240,000 Total sales 24,324,000 Total credit sales 18,785,000 "
    "Net income 2,975,000 Cost of goods sold 12,600,000 Total assets 10,550,000"
)

# nagle-strategy-tactics-pricing, PDF p.349. El OTRO estilo de indice: la pagina va
# pegada al termino SIN coma ("Rolex 28-9, 40, 68"). Es el hueco conocido del piso.
INDICE_SIN_COMA = (
    "Index328 Rolex 28\u20139, 40, 68 Rosiello, Robert 292n18 Russo, J. Edward 75n6 "
    "Ryanair 18, 137, 170, 171 S safe harbors 297, 310n8 Sales and Marketing "
    "Management 206n38 sales credit, calculation of team incentives 278"
)

# jacobs-chase-operations-supply-chain-management, PDF p.718-719. CONTENIDO real, y el
# pasaje que mas cerca queda del umbral en todo el corpus medido: la tabla de evaluacion
# de una consultoria de operaciones lista las preguntas de cada categoria separadas por
# coma. Va tal como el tope de copyright la deja -- los 315 caracteres que sobreviven a
# `_cortar` --, que es el texto que el piso de verdad juzga; el chunk entero mide 600 y
# sigue con cinco filas mas. Es el control negativo que muere si alguien afloja el umbral.
TABLA_EVALUACION = (
    "Operations Consulting Chapter 25 689 Assessment category Relevant Questions "
    "Number of Y es\u2019s Ratio of Y es\u2019s to Number of Relevant Questions "
    "Customer focus 1, 2, 20 Safety, cleanliness, and order 3, 4, 5, 20 "
    "Visual management/Scheduling 2, 4, 6, 7, 8, 9, 10, 11, 20 "
    "Use of space, movement efficiency, and product"
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

# operations-supply-chain-management-arxiv, PDF p.89. Prosa normal que cita autor-anio
# entre parentesis: la forma "termino, numero" tambien la tiene una cita academica, y
# este es el pasaje de CONTENIDO que mas cerca quedaba del umbral antes de que el piso
# dejara los anios afuera (3 marcas sin la exclusion, 0 con ella).
PROSA_CON_CITAS = (
    "ence diagrams (Coyle, 1977), and causal loop modelling (Morecroft, 2020) can "
    "provide insight. The beer game is a popular tabletop game for teaching the "
    "dynamics of supply chains (Sterman, 1989a). Surveys and secondary data can also "
    "be used to investigate bullwhip."
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


# -- (a) el indice del final del libro, por repeticion y no por una firma ------------


def test_un_indice_onomastico_real_no_ocupa_un_slot():
    """El hallazgo, textual. Un indice del final del libro no es una cita: es una lista
    de numeros de pagina, y el cliente que abre la p.383 de Liu no encuentra teoria."""
    assert lectura.parse_readings(_cable(INDICE_ONOMASTICO)) == ()


def test_los_tres_indices_del_estilo_dominante_tampoco_ocupan_un_slot():
    """La regresion que este piso tuvo en su primera version, cerrada con los pasajes que
    la delataron.

    Esa version buscaba UNA firma -- un termino seguido de cuatro o mas paginas separadas
    por coma -- calibrada contra un unico pasaje (Liu PDF p.383). Medido despues contra el
    indice vivo, ese es el estilo RARO: Chopra, Heizer, Ivanov, Vollmann, Fleisher y
    Jacobs indexan "termino, pagina termino, pagina", con UNA pagina por termino, y los
    tres de abajo pasaban enteros. Por eso el piso ya no busca una firma sino que cuenta
    MARCAS: las entradas y las listas de paginas suman, y lo que delata a un indice es la
    repeticion.
    """
    casos = {
        "chopra p.522": (INDICE_CHOPRA, "chopra-meindl-supply-chain-management", "PDF p.522"),
        "heizer p.894": (INDICE_HEIZER, "heizer-render-operations-management", "PDF p.894"),
        "ivanov p.593": (INDICE_IVANOV, "ivanov-global-supply-chain-operations", "PDF p.593"),
    }
    for nombre, (cuerpo, slug, etiqueta) in casos.items():
        assert lectura.es_inutilizable_como_cita(lectura._sanear(cuerpo)), nombre
        assert lectura.parse_readings(_cable(cuerpo, slug, etiqueta)) == (), nombre


def test_una_enumeracion_de_numeros_con_coma_no_es_un_indice():
    """La primera version de este piso DESCARTABA contenido bueno por el lado del numero.

    Su "termino" admitia numeros adentro, asi que "Period 1, 2, 3, 4" era un termino con
    su lista de paginas y el MPS se iba en silencio -- sin nota y sin rastro, porque un
    descarte no deja marca. Lo que separa a un indice de una tabla es QUE HAY ANTES DE LA
    COMA: en un indice una letra ("Abbott, 510"), en una tabla un numero ("95, 105").
    """
    casos = {
        "periodos de un MPS": "Master production schedule. Period 1, 2, 3, 4, 5, 6, 7, 8 in units",
        "doce meses del plan": "Period 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12 monthly buckets",
        "grilla de horarios": "The jobs start at 8, 10, 12, 14, 16 hours after the shift begins",
        "serie de ordenes": "Booked customer orders by week: 95, 105, 70, 10, 0, 0 units",
        "importes con separador de miles": "Holding cost was 12,500, 14,750, 16,000 and 18,250",
        "clausulas de una norma": "See clauses 4, 5, 6, 7, 8 of the standard for the evidence",
    }
    for nombre, cuerpo in casos.items():
        assert not lectura.es_inutilizable_como_cita(cuerpo), nombre
        assert lectura.parse_readings(_cable(cuerpo))[0].excerpt, nombre


def test_la_prosa_que_enumera_palabra_y_numero_no_es_un_indice():
    """LA REGRESION DE LA RONDA 1, cerrada con los seis pasajes que la delataron.

    Esa version contaba marcas SUELTAS contra un umbral de cuatro, asi que cuatro pares
    "<palabra>, <numero>" en cualquier parte del extracto descartaban el pasaje ENTERO
    -- libro y pagina incluidos, y en silencio, porque un descarte no deja nota. Los seis
    de abajo puntuaban exactamente 4 con esa regla y los seis son contenido legitimo. Los
    dos ultimos no son fabricados: salen textuales de este mismo repo
    (`documentation/NO_SILO_KERN_LOOP.md:347` y
    `documentation/CAPABILITY_EXPANSION_PLAN.md:427`).

    Y NO SE ARREGLA CON UNA MARCA MAS AFILADA: "Abbott, 510 Accenture, 22" y "8 pallets,
    2 cases" son la misma cadena de simbolos. Lo que los separa es global -- el conteo
    por clausula y el umbral con hueco --, no un caracter alrededor de la coma.
    """
    casos = {
        "modos de transporte": (
            "Mode selection is a trade-off between cost and speed for every lane in the "
            "network, and the planner has to weigh the landed cost against the service "
            "promise before committing: ocean, 35 days; air, 5 days; rail, 12 days; "
            "truck, 3 days. The cheapest mode is rarely the one that keeps the promise."
        ),
        "costos por unidad": (
            "Costs per unit: material, 12 dollars; labor, 8 dollars; overhead, "
            "4 dollars; freight, 2 dollars."
        ),
        "contenido de un embarque": (
            "The shipment contained 8 pallets, 2 cases, 3 totes, 5 drums, 7 bags of raw "
            "material."
        ),
        "capacidad de una planta": (
            "The plant runs 3 shifts, 12 lines, 48 workstations, 96 operators, "
            "200 changeovers per week."
        ),
        "conteo de un checklist": (
            "El resultado del loop: 8 CUMPLE, 2 PARCIAL, 3 NO_CUMPLE, 2 SIN_DATO, "
            "5 FUERA_DE_ALCANCE sobre el total."
        ),
        "mensajes EDI": (
            "Los mensajes X12 856 (ASN), 214 (estado), 990 (tender), 850 (PO), "
            "810 (factura) cubren el ciclo de la orden."
        ),
    }
    for nombre, cuerpo in casos.items():
        assert lectura._marcas_de_indice(cuerpo) < lectura.MIN_MARCAS_DE_INDICE, nombre
        assert not lectura.es_inutilizable_como_cita(cuerpo), nombre
        assert lectura.parse_readings(_cable(cuerpo))[0].excerpt, nombre


def test_las_marcas_no_se_suman_de_los_dos_lados_de_un_punto_y_coma():
    """Por que el conteo es POR CLAUSULA y no total, con el caso que lo obliga.

    Un indice es un bloque continuo de entradas y no tiene punto y coma adentro: medido,
    0 de las 16 paginas de indice que hay en los 521 pasajes del indice vivo trae uno,
    mientras que 25 de esos 521 pasajes si -- o sea que la condicion no es vacua, es que
    dentro de un indice no pasa nunca. Una enumeracion en prosa es al reves: las marcas
    estan repartidas una por clausula, y sumarlas es sumar evidencia de cuatro frases
    distintas como si fuera una sola lista.
    """
    enumeracion = ("Costs per unit: material, 12 dollars; labor, 8 dollars; "
                   "overhead, 4 dollars; freight, 2 dollars.")
    sin_punto_y_coma = enumeracion.replace(";", " ")

    assert lectura._marcas_en_una_clausula(enumeracion) == 4   # lo que contaba la ronda 1
    assert lectura._marcas_de_indice(enumeracion) == 1         # una por clausula
    # Y la condicion no es vacua ni al reves: sin los punto y coma vuelven a sumar.
    assert lectura._marcas_de_indice(sin_punto_y_coma) == 4

    for indice in (INDICE_ONOMASTICO, INDICE_CHOPRA, INDICE_HEIZER, INDICE_IVANOV,
                   INDICE_HEIZER_MAS_POBRE):
        texto = lectura._sanear(indice)
        assert ";" not in texto
        assert lectura._marcas_de_indice(texto) == lectura._marcas_en_una_clausula(texto)


def test_un_separador_de_miles_no_es_una_lista_de_paginas():
    """El otro falso positivo, encontrado sobre el corpus VIVO y no fabricado.

    "$25,240,000" tiene la forma exacta de una lista de paginas de indice ("25, 240, 000")
    y el enunciado del ejercicio de finanzas de Jacobs & Chase PDF p.50 -- contenido real
    del libro, con seis importes -- puntuaba 6 marcas y se descartaba entero. Lo unico que
    los distingue es el ESPACIO: un indice escribe "5, 6, 23", un importe "25,240,000".

    Medido sobre los 521 pasajes: exigir el espacio cambia el veredicto de ese pasaje y de
    ningun otro, y deja las 16 paginas de indice con el mismo puntaje que tenian.
    """
    texto = lectura._cortar(lectura._sanear(PROSA_CON_IMPORTES))
    assert lectura._marcas_de_indice(texto) == 0
    assert not lectura.es_inutilizable_como_cita(texto)
    assert lectura.parse_readings(_cable(
        texto, "jacobs-chase-operations-supply-chain-management", "PDF p.50"))[0].excerpt

    # Y el espacio no le saca nada a un indice de verdad: los cinco siguen cayendo.
    for indice in (INDICE_ONOMASTICO, INDICE_CHOPRA, INDICE_HEIZER, INDICE_IVANOV,
                   INDICE_HEIZER_MAS_POBRE):
        assert lectura.es_inutilizable_como_cita(lectura._sanear(indice))


def test_una_lista_de_numeros_sin_termino_indexado_no_es_un_indice():
    """Una serie de datos en prosa NO puede costar el pasaje entero.

    Las listas de paginas puntuaban solas, sin exigir una sola entrada "termino, pagina".
    Un enunciado de problema resuelto -- el genero que mas abunda en estos manuales --
    llega al umbral por acumular series separadas por coma, y se pierde libro, pagina y
    extracto en silencio. Ademas contradice la tesis declarada del modulo: lo que
    discrimina es la FORMA de la coma, no cuantos numeros hay.
    """
    prosa = ("The demand series is 200, 220, 180, 250. The forecast is 210, 215, 220. "
             "The on-hand is 40, 35, 20. Safety stock is 12, 14, 16. "
             "Reorder point is 60, 65, 70.")
    assert lectura._marcas_de_indice(prosa) == 0
    assert not lectura.es_inutilizable_como_cita(prosa)


def test_la_frontera_de_marcas_esta_pinchada_por_los_dos_lados():
    """El umbral no se puede mover ni para arriba ni para abajo sin romper esto.

    Los dos casos estan a UNA marca del corte, que es lo que un test de frontera tiene que
    demostrar, y los dos son TEXTUALES del indice vivo -- ya no una construccion:

    * arriba, Heizer PDF p.891 con 5 marcas, que es la pagina de indice MAS POBRE de los
      521 pasajes medidos: si el umbral sube a 6, este indice pasa entero;
    * abajo, la tabla de evaluacion de Jacobs & Chase con 3, que es el contenido que mas
      marcas junta sin ser un indice: si el umbral baja a 3, se descarta contenido real.

    En el medio queda el 4, que es donde puntuaba la prosa que la ronda 1 tiraba (ver
    `test_la_prosa_que_enumera_palabra_y_numero_no_es_un_indice`) y donde no puntua ningun
    pasaje de los 521 medidos. El corte se apoya en aire por los dos lados.
    """
    indice_mas_pobre = lectura._cortar(lectura._sanear(INDICE_HEIZER_MAS_POBRE))
    tabla = lectura._cortar(lectura._sanear(TABLA_EVALUACION))

    assert lectura._marcas_de_indice(indice_mas_pobre) == 5
    # Con el arreglo de "las listas no puntuan solas" la tabla baja de 3 marcas a 0:
    # sobrevive con MAS margen que antes, no con menos. Lo que sostiene la frontera de
    # abajo pasa a ser el indice de Chopra, que es el que puntua justo por encima.
    assert lectura._marcas_de_indice(tabla) == 0
    # Sin esto el test seguiria verde con el umbral en cualquier cosa: lo que descarta al
    # indice tiene que ser el piso de marcas y no el de palabras.
    assert lectura._palabras_citables(indice_mas_pobre) >= lectura.MIN_PALABRAS_CITABLES

    assert lectura.es_inutilizable_como_cita(indice_mas_pobre)  # muere si el umbral sube
    assert not lectura.es_inutilizable_como_cita(tabla)         # muere si el umbral baja


def test_las_citas_autor_anio_no_son_entradas_de_indice():
    """Un anio entre parentesis no es un numero de pagina, y sin esa distincion la prosa
    academica se descarta sola.

    La primera parte es textual del indice vivo y tiene tres citas; la segunda le agrega
    una cuarta, que es lo que la llevaria al umbral si los anios contaran. El corpus tiene
    libros con bibliografia adentro del cuerpo, asi que esto no es hipotetico: cuatro
    citas entre parentesis en un parrafo es un parrafo normal, no un indice.
    """
    cuarta_cita = PROSA_CON_CITAS + " See also (Forrester, 1961) for the original model."

    assert lectura._marcas_de_indice(PROSA_CON_CITAS) == 0
    assert lectura._marcas_de_indice(cuarta_cita) == 0
    assert not lectura.es_inutilizable_como_cita(cuarta_cita)
    assert lectura.parse_readings(_cable(cuarta_cita))[0].excerpt


def test_el_minimo_de_marcas_es_el_que_se_midio():
    """Va literal: 521 pasajes del indice vivo (74 consultas, 2026-09-12), las 16 paginas
    de indice puntuan 5 a 12 y los otros 505 puntuan 0, 1 o 2. Nadie puntua 3 ni 4."""
    assert lectura.MIN_MARCAS_DE_INDICE == 5


def test_el_indice_sin_coma_despues_del_termino_es_un_hueco_conocido():
    """Lo que este piso NO ve, escrito para que no sorprenda: cuando la pagina va pegada
    al termino sin coma ("Rolex 28-9, 40, 68", Nagle PDF p.349; "reorder points (ROP) 104,
    106", Christopher PDF p.288-289) no hay entradas que contar y el indice pasa entero.

    Se deja pasar MEDIDO, no por olvido: la marca que lo atraparia -- "termino <pagina>,
    <pagina>", sin la coma -- recupera 2 de las 8 paginas que se escapan y al mismo tiempo
    empuja TABLA_EVALUACION de 3 marcas a 6, o sea descarta contenido verificado. Perder
    un indice le cuesta al cliente un pasaje; perder la tabla le cuesta el que queria.
    """
    assert not lectura.es_inutilizable_como_cita(lectura._sanear(INDICE_SIN_COMA))


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
    """Los OCHO, uno por uno, escritos ACA y no leidos de la constante que custodian.

    POR QUE LA LISTA VA LITERAL. La version anterior iteraba sobre
    `lectura.NAVEGACION_DE_SITIO`, o sea sobre lo mismo que decia proteger, y entonces
    borrar un marcador borraba tambien su caso: mutado marcador por marcador, sacar
    'related posts', 'share this post', 'subscribe to our', 'privacy policy' o 'cookie
    policy' no rompia NADA -- 5 de los 8 eran borrables con el modulo entero en verde --,
    y con la tupla vacia el test pasaba vacuo. Un test que lee la constante no la fija.

    Las dos mitades hacen falta: la igualdad de CONJUNTOS avisa cuando alguien agrega o
    saca un marcador (que es una decision, no un detalle, porque cada uno descarta
    pasajes), y el caso por marcador prueba que cada uno alcanza SOLO para descartar,
    incluso pegado a prosa de verdad.
    """
    marcadores = [
        "recent posts",
        "related posts",
        "share this post",
        "subscribe to our",
        "privacy policy",
        "cookie policy",
        "skip to main content",
        "skip to content",
    ]
    assert sorted(marcadores) == sorted(lectura.NAVEGACION_DE_SITIO)

    for marcador in marcadores:
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
        "evaluacion de consultoria de jacobs & chase": TABLA_EVALUACION,
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
    cliente igual recibe 3 pasajes buenos en vez de quedarse sin ninguno.

    El fake CORTA en `k` (ver `_primeras`), asi que este test mide lo que dice medir: con
    ``HOLGURA_PEDIDO = 0`` el servidor devuelve solo las tres primeras entradas -- indice,
    prosa, barra lateral -- y el cliente se queda con UNA. Sin ese corte, el fake
    devolveria las seis pase lo que pase y el test pasaria igual con holgura cero.
    """
    payload = _lote(INDICE_ONOMASTICO, PROSA, BARRA_LATERAL, TABLA_MPS,
                    "FIGURE 10.18", TABLA_ORDENES)
    r, vistos = _leer(payload, k=3)

    assert vistos[0]["k"] == 6          # 3 pedidos + 3 de holgura, en la unica llamada
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
    assert srv.vistos[0]["k"] == 6  # literal: escrito contra la constante seria tautologia


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
