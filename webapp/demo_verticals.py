"""La pregunta unica del embudo -- "que mueves?" -- y el orden que decide.

El embudo es autoservicio y sin humano: no hay nadie que mire el archivo del
visitante y diga "ah, tu eres una ferreteria". El que tiene que reconocer el
rubro es la pagina, y con una sola pregunta, porque cada pregunta adicional en
un formulario publico cuesta conversion.

Lo que esa respuesta decide, hoy, son dos cosas:

1. **Con que salida se lidera.** ``VerticalProfile.diagnostic_outputs`` es un
   ORDEN, no un conjunto. En repuestos el pooling va quinto y en distribucion va
   segundo, porque es el unico rubro donde tener mas de una bodega se cumple por
   definicion. La primera salida implementada del pack es la pestana por
   defecto de /demo.
2. **Con que tabla de alias se mapean sus columnas.** El export de una
   constructora trae `partida` y `faena`; el de un distribuidor trae `local` y
   `guia de despacho`. Ninguno de los dos esta ni puede estar en una tabla
   generica.

Modulo puro: sin I/O mas alla de leer los packs, sin FastAPI, sin estado. La
ruta que lo expone es cuatro lineas en webapp/app.py.

Un pack roto NO se traga: ``verticals_payload`` deja subir el ValueError del
loader. Eso es un defecto de repo, no una condicion de runtime, y ya tiene su
test (``test_shipped_pack_loads_and_validates``). Tragarselo aqui convertiria un
archivo mal escrito en un embudo que calla y sirve numeros genericos.
"""

from __future__ import annotations

from pathlib import Path

from src.vertical_profile import (
    DEFAULT_VERTICALS_CONFIG_DIR,
    VerticalProfile,
    list_verticals,
    load_vertical_profile,
)

QUESTION = "¿Qué mueves?"

# Cual de las cinco salidas de 05-GTM-KERN §5 tiene motor en el embudo, y que
# modo del /demo la entrega. DOS DE CINCO, y se dice en vez de disimularse:
#
#   lead_time_gap        -> modo "lead"   (webapp/demo_lead_time_scan.py)
#   overstock_valued     -> modo "stock"  (webapp/demo_scan.py, E&O)
#   dead_stock_12m       -> modo "stock"  (mismo scan, mismo artefacto)
#   below_reorder_point  -> sin motor en el embudo
#   pooling_savings      -> sin motor en el embudo, y es la que ningun
#                           competidor puede calcular
#
# Una salida que un pack pide primero pero que no esta implementada se SALTA, y
# lidera la siguiente que si lo esta. No se inventa una pestana vacia, y no se
# reordena en silencio a espaldas del pack: el orden que se aplica es el del
# pack filtrado, que es lo mas parecido a su intencion que se puede entregar hoy.
DEMO_MODE_BY_OUTPUT: dict[str, str] = {
    "lead_time_gap": "lead",
    "overstock_valued": "stock",
    "dead_stock_12m": "stock",
}

# El orden canonico del §5 para el visitante que no declara rubro -- que es el
# caso por defecto y seguira siendolo para la mayoria. Plazos primero: es un
# hecho sobre sus datos, es lo mas barato de calcular y es lo que se gana el
# derecho a seguir hablando.
DEFAULT_MODES: tuple[str, ...] = ("lead", "stock")


def demo_modes_for(profile: VerticalProfile | None) -> tuple[str, ...]:
    """Modos del /demo en el orden que pide el pack, sin repetir.

    ``None`` (visitante anonimo) devuelve el orden canonico del §5.

    Devuelve una tupla VACIA cuando ninguna de las salidas del pack tiene motor
    todavia. No cae al orden por defecto: un pack en esa situacion no se puede
    servir, y ofrecerlo igual seria entregarle al visitante la salida de OTRO
    rubro con la etiqueta del suyo. El llamador lo omite de la pregunta.
    """
    if profile is None:
        return DEFAULT_MODES
    modes: list[str] = []
    for output in profile.diagnostic_outputs:
        mode = DEMO_MODE_BY_OUTPUT.get(output)
        if mode is not None and mode not in modes:
            modes.append(mode)
    return tuple(modes)


def verticals_payload(*, config_dir: Path | str = DEFAULT_VERTICALS_CONFIG_DIR) -> dict:
    """Lo que /demo necesita para armar la pregunta, en un solo GET.

    Se lee del directorio y no de una lista escrita a mano, que es lo que
    mantiene cierta la metrica de control: un tercer vertical es un archivo,
    no un cambio de codigo ni de HTML.
    """
    options = []
    for slug in list_verticals(config_dir=config_dir):
        profile = load_vertical_profile(slug, config_dir=config_dir)
        modes = demo_modes_for(profile)
        if not modes:
            continue
        options.append({
            "slug": profile.vertical,
            "label": profile.demo_question_label or profile.display_name,
            "display_name": profile.display_name,
            "modes": list(modes),
        })
    return {
        "question": QUESTION,
        "options": options,
        "default_modes": list(DEFAULT_MODES),
    }


def aliases_for(
    slug: str | None, *, config_dir: Path | str = DEFAULT_VERTICALS_CONFIG_DIR
) -> dict[str, list[str]] | None:
    """Tabla de alias del rubro para ``jobs.intake.detect_columns``, o ``None``.

    ``None`` significa "usa la global", que es el caso del visitante que no
    declaro rubro. Un slug que no existe tambien devuelve ``None`` y NO levanta:
    a diferencia del orquestador -- donde el rubro sale de un perfil que un
    operador escribio y una discrepancia es un error real -- aqui el valor llega
    de un formulario publico, y un 500 por un campo manipulado desde el
    navegador es una superficie de ataque, no una garantia de correccion. El
    peor caso al ignorarlo es mapear con la tabla generica, que es exactamente
    lo que pasaba antes de que los packs existieran.
    """
    if not slug:
        return None
    from jobs.intake import ALIASES  # import local: evita jalar pandas en el import del modulo

    try:
        profile = load_vertical_profile(slug, config_dir=config_dir)
    except (LookupError, ValueError):
        return None
    return profile.merged_aliases(ALIASES)
