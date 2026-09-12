"""Procedencia de libro para un entregable: libro, pagina del PDF y un fragmento corto.

QUE ES ESTO Y QUE NO ES. Existe un indice de lectura aparte (44 libros de supply chain
partidos en 6.929 pasajes con su pagina, repo `kern-lectura`) servido por un MCP stdio.
Un pasaje de ese indice **no es una cita gateada de Kern**: no tiene `node_id`, y
`filter_citations` lo rechazaria con razon. Por eso este modulo va RIO ABAJO del gate,
como seccion de procedencia aparte -- nada de lo que devuelve entra al pool de
candidatas ni al campo `citations`. El grafo L3 sigue siendo la unica fuente de citas.

POR QUE EL TOPE DE LONGITUD VIVE DE ESTE LADO. Los PDF del indice son copias de libgen,
y lo que Kern renderiza es lo que llega al cliente. Kern hoy no tiene ningun tope de
longitud sobre lo que renderiza, asi que el tope lo pone este codigo y no el otro repo:
:data:`MAX_EXCERPT_CHARS`.

EL CABLE, MEDIDO A MANO, NO ASUMIDO. La tool contesta un STRING de markdown envuelto en
``structuredContent = {"result": <str>}``, no filas -- por eso `decode_tool_result` de
``mcp_source`` no sirve aca (levanta `McpToolError` sobre un str) y lo unico que se
reusa de ese modulo es :func:`sync_tool_caller`. Peor: ``isError`` es False tambien
cuando no hubo resultados; el servidor devuelve el string ``"(sin pasajes)"`` como exito
y sus fallos como ``"error: <mensaje interno>"``. Traducir esos strings a estado es la
mitad del trabajo: un adaptador ingenuo le embarcaria al cliente ``"error: storage ...
esta corrupto"`` como si fuera un pasaje de libro.

DISPONIBLE ES LA EXCEPCION, NO LA REGLA. El indice pide ~1,6 GB de RSS y la VM de
produccion tiene 512 MB (``fly.toml``, ``[[vm]] memory = "512mb"``), asi que en prod
esto nunca esta disponible -- y eso NO es un error: se contesta ``available=False`` con
el motivo en una linea. Ningun fallo de lectura se propaga al llamador: un entregable no
se cae porque falto una lectura.
"""

from __future__ import annotations

import os
import re
import tempfile
import unicodedata
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from .mcp_source import sync_tool_caller

#: Tope de copyright del lado de Kern. Deliberadamente MAS CORTO que el del otro repo
#: (600 en `kern_lectura.format.format_chunks`): alla el consumidor es un agente que
#: lee para decidir, aca el consumidor es el entregable que ve el cliente. Los PDF son
#: copias de libgen: lo que sale tiene que ser cita con pagina y fragmento corto, nunca
#: un pasaje largo textual, y el unico lugar donde ese limite se puede hacer cumplir es
#: este (Kern no tiene ningun tope de longitud sobre lo que renderiza).
MAX_EXCERPT_CHARS = 320

#: Tope del slug de la fuente. Misma razon que el de arriba, otra puerta: el slug sale
#: de la cabecera que manda el servidor y nadie lo recorta, asi que sin un maximo
#: ``1. **<900 caracteres>** PDF p.5`` publica 900 caracteres de texto ajeno como si
#: fueran el nombre de un libro. El slug mas largo del indice mide 50.
MAX_SOURCE_CHARS = 120

#: Lo que el emisor pone cuando NO sabe de que libro salio el pasaje
#: (``format_chunks``: ``c.get("file_path", "?")``). Una entrada asi trae pagina pero
#: no trae libro: es procedencia que el cliente no puede verificar, asi que se descarta
#: igual que una etiqueta invalida.
FUENTE_DESCONOCIDA = "?"

#: Variable de entorno con la ruta ABSOLUTA al interprete del repo de lectura, p. ej.
#: ``C:\\Users\\...\\kern-lectura\\.venv\\Scripts\\python.exe``. Sin ella no se intenta
#: levantar nada: es el caso normal en produccion.
ENV_INTERPRETE = "KERN_LECTURA_PYTHON"

#: Como arranca el servidor de lectura: ``<interprete> -m kern_lectura.mcp_server``.
SERVIDOR_MODULO = "kern_lectura.mcp_server"

#: La unica tool que se consume. `query_books` devuelve ademas entidades y relaciones
#: del grafo de ese repo, que compiten con el grafo L3 de Kern; no es lo que se quiere.
TOOL_SEARCH = "search_passages"

#: Los dos strings que el servidor devuelve como EXITO y no son pasajes.
SIN_PASAJES = "(sin pasajes)"
PREFIJO_ERROR = "error:"

_ELIPSIS = "..."

#: Un corte por palabra solo vale si no deja el extracto mucho mas corto que el tope;
#: un pasaje sin espacios (formulas, tablas) se corta duro antes que devolver migajas.
_MIN_FRACCION_PALABRA = 0.6

#: Cuantos eslabones de la cadena de causas entran a la nota. Tres alcanzan para decir
#: que fallo sin convertir la nota en un traceback: la nota es una linea para un humano.
_MAX_CAUSAS = 3

#: Cuanto del stderr del subproceso entra a la nota. El interprete equivocado escribe
#: ahi una sola linea util ("No module named 'kern_lectura'"); un traceback entero no
#: entra en una nota, y el resto del stderr no es de nadie.
_MAX_STDERR_CHARS = 300

#: Centinelas de pagina del otro repo (``kern_lectura.content.PAGE_MARK``). El servidor
#: ya los saca, pero si alguno sobrevive se va ENTERO: dejar "p.83" suelto en el medio
#: de una frase es peor que no tener marca, porque parece parte del texto del libro.
_CENTINELA_RE = re.compile("\u27e6p\\.\\d+\u27e7")

#: Entrada del markdown: ``N. **slug** ETIQUETA``.
_ENTRADA_RE = re.compile(r"^\s*\d+\.\s+\*\*(?P<source>.*?)\*\*(?P<label>.*)$")

#: Las UNICAS etiquetas que el servidor produce (``kern_lectura.format``): "PDF p.83",
#: "PDF p.397-399" o "sin pagina". Se valida en vez de tomarse tal cual porque la
#: etiqueta es resto-de-linea: sin este filtro ``1. **slug** esto es texto del libro``
#: sale como procedencia FABRICADA -- una pagina que el cliente no puede verificar --
#: y ademas es la unica parte de la entrada que no pasa por ningun tope de longitud.
_ETIQUETA_RE = re.compile(r"^(?:PDF p\.\d{1,6}(?:-\d{1,6})?|sin pagina)$")

#: No-ASCII que llega en los extractos, con su equivalente legible. Va ANTES de la
#: normalizacion Unicode porque ninguno de estos se descompone solo: NFKD deja la raya
#: y las comillas curvas intactas, y convierte el micro en mu griega sin hacerla ASCII.
#: La matematica se escribe con su nombre en vez de perderse como "?": un extracto que
#: dice "sigma = 4" se sigue entendiendo, uno que dice "? = 4" no.
_REEMPLAZOS = {
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u00ab": '"', "\u00bb": '"',
    "\u2018": "'", "\u2019": "'", "\u201a": "'",
    "\u2013": "-", "\u2014": "-", "\u2212": "-", "\u2010": "-", "\u2011": "-",
    "\u00a0": " ", "\u2007": " ", "\u202f": " ", "\u2009": " ",
    "\u2022": "-", "\u00b7": "-", "\u2043": "-",
    "\u00b5": "mu", "\u03bc": "mu", "\u03c3": "sigma", "\u03a3": "Sigma",
    "\u03b1": "alpha", "\u03b2": "beta", "\u03b3": "gamma", "\u03b4": "delta",
    "\u0394": "Delta", "\u03b5": "epsilon", "\u03b8": "theta", "\u03bb": "lambda",
    "\u03c0": "pi", "\u03c1": "rho", "\u03c4": "tau", "\u03c6": "phi",
    "\u03c7": "chi", "\u03c9": "omega", "\u03a9": "Omega",
    "\u2264": "<=", "\u2265": ">=", "\u2260": "!=", "\u00d7": "x", "\u00f7": "/",
    "\u221a": "raiz", "\u2211": "suma", "\u221e": "infinito", "\u00b1": "+/-",
}


@dataclass(frozen=True)
class Reading:
    """Un pasaje de libro como PROCEDENCIA, no como cita gateada.

    ``page_label`` se conserva TEXTUAL ("PDF p.83-84", "sin pagina") y nunca se
    convierte en "p.83" pelado: es la pagina FISICA del archivo PDF y el folio impreso
    del libro difiere por obra (Vandeput: 26 paginas de corrimiento). Una cita que el
    cliente falsea al abrir el libro impreso es peor que no citar.
    """

    source: str
    page_label: str
    excerpt: str


@dataclass(frozen=True)
class ReadingLookup:
    """El resultado de una consulta al indice, disponible o no.

    Cuando ``available`` es False, ``readings`` va vacia y ``note`` dice POR QUE en una
    linea que le sirve a un humano. No hay un tercer estado ni una excepcion: el
    llamador siempre recibe esto.
    """

    available: bool
    readings: tuple[Reading, ...]
    note: str


class _RespuestaIlegible(RuntimeError):
    """El servidor contesto algo que este adaptador no sabe leer."""


# --- saneo y corte -------------------------------------------------------------------


def _sanear(texto: str) -> str:
    """Texto imprimible en una consola cp1252, sin centinelas y en una sola linea.

    Cuatro pasos, en este orden: sacar los centinelas; reemplazar lo que no se
    descompone solo (comillas, rayas, griegas); normalizar (ligaduras fl/fi/ff y
    acentos caen aca); y como ULTIMO recurso ``ascii``/``replace``, que degrada a "?"
    en vez de romper. Al final se colapsa el espacio en blanco, lo que ademas junta un
    extracto que venia partido en varias lineas.
    """
    t = _CENTINELA_RE.sub(" ", texto)
    for malo, bueno in _REEMPLAZOS.items():
        if malo in t:
            t = t.replace(malo, bueno)
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.encode("ascii", "replace").decode("ascii")
    return " ".join(t.split())


def _cortar(texto: str) -> str:
    """El extracto hasta :data:`MAX_EXCERPT_CHARS`, sin partir una palabra si se puede.

    Se aplica DESPUES del saneo a proposito: el reemplazo puede ALARGAR el texto (un
    solo caracter sigma pasa a cinco), asi que un corte previo dejaria el tope mintiendo.
    """
    if len(texto) <= MAX_EXCERPT_CHARS:
        return texto
    presupuesto = MAX_EXCERPT_CHARS - len(_ELIPSIS)
    cabeza = texto[:presupuesto]
    corte = cabeza.rfind(" ")
    if corte >= presupuesto * _MIN_FRACCION_PALABRA:
        cabeza = cabeza[:corte]
    return cabeza.rstrip(" ,;:.") + _ELIPSIS


def _es_linea_de_error(texto: str) -> bool:
    """El servidor reporta sus fallos como texto plano con este prefijo, no como isError."""
    return texto.lstrip().lower().startswith(PREFIJO_ERROR)


# --- parser del markdown -------------------------------------------------------------


def parse_readings(crudo: str) -> tuple[Reading, ...]:
    """Las entradas ``N. **slug** ETIQUETA`` + extracto indentado que trae el markdown.

    Tolerante con lo que FALTA, cerrado con lo que SOBRA. El extracto puede no estar
    (libro + pagina ya es procedencia completa; el fragmento es el extra) o venir
    partido en varias lineas, y una entrada rota se descarta sola sin llevarse el lote.
    Pero una linea que no es una entrada NI un extracto indentado se tira: pegarla al
    cuerpo de la entrada anterior -- que es lo que hacia la primera version -- le
    atribuye a un libro y a una pagina texto que el servidor no saco de ningun libro
    (un pie de pagina interno, o directamente ``error: <mensaje interno>``).

    Por eso el cuerpo exige indentacion, que es lo que usa el formato real (tres
    espacios), y por eso el lote se CORTA en la primera linea que empieza con
    ``error:``, indentada o no: preferimos perder la cola de un extracto antes que
    embarcarle al cliente el mensaje interno del servidor como si fuera un libro.
    """
    entradas: list[tuple[str, str, list[str]]] = []
    for linea in crudo.splitlines():
        pelada = linea.strip()
        if _es_linea_de_error(pelada):
            break
        m = _ENTRADA_RE.match(linea)
        if m:
            entradas.append((m.group("source"), m.group("label"), []))
        elif pelada and entradas and linea[:1].isspace():
            entradas[-1][2].append(pelada)
        # Cualquier otra linea es texto suelto del servidor: se descarta. NUNCA se pega
        # al cuerpo de la entrada anterior.

    lecturas = []
    for source, label, cuerpo in entradas:
        slug = _sanear(source)
        etiqueta = _sanear(label)
        sin_libro = not slug or slug == FUENTE_DESCONOCIDA or len(slug) > MAX_SOURCE_CHARS
        if sin_libro or not _ETIQUETA_RE.match(etiqueta):
            continue  # media cita no se inventa: sin slug creible o sin pagina creible, no hay entrada
        lecturas.append(
            Reading(source=slug, page_label=etiqueta, excerpt=_cortar(_sanear(" ".join(cuerpo))))
        )
    return tuple(lecturas)


# --- del cable a un estado -----------------------------------------------------------


def _no_disponible(motivo: str) -> ReadingLookup:
    return ReadingLookup(available=False, readings=(), note=motivo)


def _causas(exc: BaseException) -> list[BaseException]:
    """Del envoltorio a la hoja: las excepciones que de verdad explican el fallo.

    Existe por el modo de falla REAL de este conector. Cuando el interprete de
    :data:`ENV_INTERPRETE` es valido pero no tiene el modulo del servidor -- la
    variable mal seteada, que es el unico modo de falla que existe fuera de produccion
    -- lo que sube no es la causa sino un ``ExceptionGroup`` de anyio cuyo texto es
    "unhandled errors in a TaskGroup (1 sub-exception)". Eso no le dice NADA al humano
    que tiene que ir a arreglar el indice, que es la unica razon por la que la nota
    existe. Los grupos son plomeria y no se renderizan: se baja por ``exceptions[0]``
    hasta una hoja, y de ahi por ``__cause__`` (el ``raise ... from`` explicito).

    No se sigue ``__context__``: eso encadena la excepcion que estaba en vuelo cuando
    se levanto esta, que muchas veces no tiene nada que ver, y la nota pasaria a
    nombrar un culpable equivocado.
    """
    cadena: list[BaseException] = []
    vistos: set[int] = set()
    actual: BaseException | None = exc
    while actual is not None and id(actual) not in vistos and len(cadena) < _MAX_CAUSAS:
        vistos.add(id(actual))
        sub = tuple(getattr(actual, "exceptions", ()) or ())
        if sub:
            actual = sub[0]
            continue
        cadena.append(actual)
        actual = actual.__cause__
    return cadena or [exc]


def _una_linea(exc: BaseException) -> str:
    """La cadena de causas en una sola linea, sin repetir el mismo eslabon dos veces."""
    partes: list[str] = []
    for e in _causas(exc):
        parte = f"{type(e).__name__}: {e}"
        if parte not in partes:
            partes.append(parte)
    return _sanear(" <- ".join(partes))


def _ultimo_grito(errlog: Any) -> str:
    """La ultima linea que el subproceso del servidor escribio por stderr.

    Es el otro eslabon que faltaba. La causa de verdad del modo de falla real
    (``No module named 'kern_lectura'``) no viaja por la excepcion -- del lado del
    cliente solo llega "Connection closed" -- sino por el stderr del subproceso, que
    por defecto va a parar a la consola de Kern y se pierde. Se captura y se pega a la
    nota. Nunca levanta: esto corre dentro del manejo de un fallo.
    """
    try:
        errlog.seek(0)
        lineas = [ln.strip() for ln in errlog.read().splitlines() if ln.strip()]
    except Exception:  # noqa: BLE001 - un stderr ilegible no puede tapar el fallo real
        return ""
    return _sanear(lineas[-1])[:_MAX_STDERR_CHARS] if lineas else ""


def _bloques_de_texto(result: Any) -> str | None:
    textos = [t for t in (getattr(b, "text", None) for b in getattr(result, "content", []) or []) if t]
    return "\n".join(textos) if textos else None


def _texto_del_resultado(result: Any) -> str:
    """El string markdown que devolvio la tool, venga por donde venga.

    Acepta un ``str`` ya decodificado para que un llamador con otro transporte (o un
    test) no tenga que fabricar un ``CallToolResult``.
    """
    if isinstance(result, str):
        return result
    if getattr(result, "isError", False):
        raise _RespuestaIlegible(f"el servidor reporto un error: {_bloques_de_texto(result) or result}")
    estructurado = getattr(result, "structuredContent", None)
    if isinstance(estructurado, dict) and isinstance(estructurado.get("result"), str):
        return estructurado["result"]
    texto = _bloques_de_texto(result)
    if texto is not None:
        return texto
    raise _RespuestaIlegible(f"la respuesta no trae texto: {type(result).__name__}")


def lookup_from_text(crudo: str) -> ReadingLookup:
    """Traduce el string del servidor a un estado. Los dos strings que NO son pasajes
    (``"(sin pasajes)"`` y ``"error: ..."``) salen como no disponible con ese mismo
    texto de motivo: jamas se convierten en un `Reading`."""
    texto = (crudo or "").strip()
    if not texto:
        return _no_disponible("el servidor de lectura contesto vacio")
    if texto == SIN_PASAJES or _es_linea_de_error(texto):
        return _no_disponible(_sanear(texto))
    lecturas = parse_readings(crudo)
    if not lecturas:
        return _no_disponible("el servidor contesto algo sin forma de pasaje; no se cita lo que no se pudo leer")
    return ReadingLookup(
        available=True,
        readings=lecturas,
        note=f"{len(lecturas)} pasaje(s) del indice de lectura (procedencia, no cita del grafo)",
    )


def read_with_caller(
    call_tool: Callable[[str, dict], Any],
    keywords: list[str],
    *,
    k: int = 3,
    sources: list[str] | None = None,
) -> ReadingLookup:
    """La consulta, con el transporte en manos del llamador.

    Separado de :func:`read_passages` para que el transporte sea reemplazable (un test
    con un servidor en memoria, o un llamador que ya tiene una sesion abierta) sin
    tocar la traduccion del cable, que es donde estan las trampas.
    """
    consulta = " ".join(p for p in (str(x).strip() for x in keywords) if p)
    if not consulta:
        return _no_disponible("no se pidio ninguna palabra clave: no se consulto el indice de lectura")
    argumentos: dict[str, Any] = {"query": consulta, "k": k}
    if sources is not None:
        argumentos["sources"] = sources
    try:
        crudo = _texto_del_resultado(call_tool(TOOL_SEARCH, argumentos))
    except Exception as exc:  # ningun fallo de lectura tira el entregable
        return _no_disponible(f"no se pudo leer el indice: {_una_linea(exc)}")
    return lookup_from_text(crudo)


@contextmanager
def _stdio_caller(interprete: str, timeout_s: float, errlog: Any):
    """Abre el servidor de lectura por stdio y devuelve un ``call_tool`` sincronico.

    Mismo patron que ``mcp_source``: un portal bloqueante de anyio sirve una sesion
    async desde codigo sincronico, y la sesion queda viva mientras dure el ``with`` en
    vez de un loop por llamada. Los imports son perezosos por el gotcha de arranque en
    produccion (nada de `mcp` a nivel de modulo en la cadena de boot).

    ``errlog`` es un archivo real (hace falta un descriptor: va como ``stderr`` del
    subproceso). Redirigirlo no es cosmetica -- por defecto ``stdio_client`` manda el
    stderr del servidor a ``sys.stderr``, donde ademas de perderse para la nota puede
    romper una consola cp1252 -- y es el unico lugar donde aparece por que no arranco.
    """
    from anyio.from_thread import start_blocking_portal
    from mcp import ClientSession, StdioServerParameters, stdio_client

    parametros = StdioServerParameters(command=interprete, args=["-m", SERVIDOR_MODULO])
    with start_blocking_portal() as portal:
        with portal.wrap_async_context_manager(stdio_client(parametros, errlog=errlog)) as (leer, escribir):
            sesion_cm = ClientSession(leer, escribir, read_timeout_seconds=timedelta(seconds=timeout_s))
            with portal.wrap_async_context_manager(sesion_cm) as sesion:
                portal.call(sesion.initialize)
                yield sync_tool_caller(sesion, portal)


def read_passages(
    keywords: list[str],
    k: int = 3,
    sources: list[str] | None = None,
    timeout_s: float = 30.0,
) -> ReadingLookup:
    """Pasajes de libro para acompaniar un entregable. Nunca levanta.

    Sin :data:`ENV_INTERPRETE` -- el caso normal en produccion -- contesta
    ``available=False`` sin intentar levantar nada.
    """
    interprete = (os.environ.get(ENV_INTERPRETE) or "").strip().strip('"')
    if not interprete:
        return _no_disponible(
            f"{ENV_INTERPRETE} no esta seteada: Kern corre sin indice de lectura. Es lo normal en "
            f"produccion (el indice pide ~1,6 GB de RSS y la VM tiene 512 MB)"
        )
    if not Path(interprete).is_file():
        return _no_disponible(
            f"{ENV_INTERPRETE} apunta a {interprete}, que no existe: no se levanto ningun servidor de lectura"
        )
    # El try de afuera cubre la captura del stderr del servidor, que es una comodidad y no
    # una precondicion: un TMP no escribible no puede tirar abajo un entregable. El de
    # adentro necesita a `errlog` vivo para contar lo que grito el servidor antes de morir.
    try:
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as errlog:
            try:
                with _stdio_caller(interprete, timeout_s, errlog) as call_tool:
                    return read_with_caller(call_tool, keywords, k=k, sources=sources)
            except Exception as exc:  # no arranca, se colgo, o la libreria levanto: es un estado
                grito = _ultimo_grito(errlog)
                dijo = f"; el servidor dijo: {grito}" if grito else ""
                return _no_disponible(
                    f"no se pudo hablar con el servidor de lectura: {_una_linea(exc)}{dijo}"
                )
    except Exception as exc:
        return _no_disponible(
            f"no se pudo preparar la lectura: {_una_linea(exc)}"
        )
