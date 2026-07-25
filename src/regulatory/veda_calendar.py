"""Cruce de un titulo de producto contra el registro de vedas chilenas.

Modulo puro: no toca la red, y el unico I/O es leer los dos YAML de
``config/`` a traves de ``load_registry`` (que el llamador puede saltarse
pasando dicts ya cargados). ``yaml`` se importa dentro de esa funcion, no a
nivel de modulo -- ver la nota de ``prod-boot`` en CLAUDE.md.

LA INVARIANTE QUE ESTE MODULO EXISTE PARA SOSTENER
--------------------------------------------------
Cuatro correcciones seguidas sobre el mismo prototipo resultaron ser el
mismo error cuatro veces: rellenar un campo ausente con una suposicion.

1. Origen  -- "Ostion Sin Coral Importado" salio expuesto a veda chilena.
2. Elaborados -- una conserva de loco se clasifico igual que una empanada
   de camaron, pese a que la veda extractiva alcanza derivados y la
   biologica no.
3. Variante -- "Merluza Apanada" salio como austral (7 dias) cuando igual
   de bien podia ser comun (38 dias).
4. Region -- se afirmaba exposicion sin decir sobre que territorio.

De ahi la forma de este modulo: **todo atributo se devuelve como
``Attribution``, que carga el token exacto del titulo que lo justifica.**
Un atributo sin evidencia literal es ``declared=False`` y su valor no se
usa para subir la severidad. La consecuencia es auditable fila por fila, y
``tests/test_veda_calendar_declared_only.py`` la verifica sobre catalogos
reales: si alguna capa vuelve a afirmar algo que el titulo no dice, ese
test se pone rojo.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

# Las 15 regiones costeras, norte a sur. La Metropolitana no tiene costa y
# por eso nunca aparece en un decreto de veda.
REGIONES_COSTERAS: tuple[str, ...] = (
    "XV", "I", "II", "III", "IV", "V", "VI", "VII",
    "XVI", "VIII", "IX", "XIV", "X", "XI", "XII",
)

# Techo de severidad por (tipo de veda, origen). El origen "no_declarado"
# nunca alcanza CRITICO: sin saber si el recurso es chileno no se puede
# afirmar que la prohibicion lo alcanza.
SEVERIDAD: Mapping[tuple[str, str], str] = {
    ("extractiva", "nacional"): "CRITICO",
    ("extractiva", "no_declarado"): "ALTO",
    ("extractiva", "importado"): "BAJO",
    ("biologica", "nacional"): "PLANIFICACION",
    ("biologica", "no_declarado"): "INFORMATIVO",
    ("biologica", "importado"): "NULO",
}
ORDEN: tuple[str, ...] = (
    "CRITICO", "ALTO", "PLANIFICACION", "BAJO", "INFORMATIVO", "NULO",
)


def fold(text: str) -> str:
    """Minuscula, sin tildes, espacios colapsados -- la forma en que se
    comparan titulo y marcadores. Se expone porque el test la necesita para
    verificar que la evidencia de cada atributo esta literalmente en el
    titulo."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", plain).strip().lower()


@dataclass(frozen=True)
class Attribution:
    """Un atributo con su justificacion. ``evidence`` es el fragmento exacto
    del titulo que lo sostiene -- nunca una parafrasis, nunca un valor por
    defecto.

    TRES estados, nunca dos:

    ``declarado``     el titulo lo dice y nada lo contradice.
    ``no_declarado``  el titulo no lo dice. No se rellena con el valor mas
                      probable.
    ``contradicho``   el titulo trae dos senales de autoridad comparable que
                      se pisan -- "Camaron 36/40 nacional" afirma origen
                      nacional mientras su calibre es la convencion del
                      camaron de cultivo importado.

    El tercer estado no es un adorno. Colapsarlo contra ``no_declarado``
    pierde la unica de las tres situaciones que es *accionable sin
    preguntar*: un dato faltante es del cliente, pero una contradiccion es
    un defecto en su propia ficha, y se le puede mostrar. Y colapsarlo
    contra ``declarado`` -- eligiendo la senal "mas fuerte" en silencio --
    es exactamente la clase de relleno que este modulo existe para impedir.
    """

    value: str | None = None
    evidence: str | None = None
    conflicts: tuple[tuple[str, str], ...] = ()

    @property
    def declared(self) -> bool:
        return self.value is not None and self.evidence is not None and not self.conflicts

    @property
    def contradicted(self) -> bool:
        return bool(self.conflicts)

    @property
    def state(self) -> str:
        if self.contradicted:
            return "contradicho"
        return "declarado" if self.declared else "no_declarado"


@dataclass(frozen=True)
class VedaAssessment:
    title: str
    especie: Attribution
    origen: Attribution
    variante: Attribution
    region: Attribution
    elaborado: Attribution
    severidad: str
    decreto: str | None
    tipo: str | None
    dias_a_ventana: int | None
    regiones_cerradas: frozenset[str]
    notas: tuple[str, ...]

    @property
    def sin_declarar(self) -> frozenset[str]:
        return frozenset(
            name for name in ("origen", "variante", "region")
            if getattr(self, name).state == "no_declarado"
        )

    @property
    def contradichos(self) -> frozenset[str]:
        """Los atributos que el propio titulo se contradice. Son distintos de
        los faltantes: un dato que falta se le pregunta al cliente, uno
        contradicho se le muestra."""
        return frozenset(
            name for name in ("origen", "variante", "region")
            if getattr(self, name).contradicted
        )


def load_registry(config_dir: str) -> tuple[dict, dict]:
    """Lee lexico y vedas. ``yaml`` se importa aqui adentro a proposito."""
    import yaml

    with open(f"{config_dir}/lexicon/es_cl_congelados.yaml", encoding="utf-8") as fh:
        lexicon = yaml.safe_load(fh)
    with open(f"{config_dir}/regulatory/cl_vedas.yaml", encoding="utf-8") as fh:
        vedas = yaml.safe_load(fh)
    return lexicon, vedas


def _find_marker(folded: str, markers: Sequence[str]) -> str | None:
    """El marcador mas largo presente, para que "merluza austral" gane a
    "merluza". Devuelve el token tal cual aparece en la lista (ya folded)."""
    hits = [fold(m) for m in markers if fold(m) in folded]
    return max(hits, key=len) if hits else None


def detect_especie(title: str, lexicon: Mapping) -> Attribution:
    folded = fold(title)
    best: tuple[int, str, str] | None = None
    for canonical, variants in lexicon["especie"].items():
        marker = _find_marker(folded, variants)
        if marker and (best is None or len(marker) > best[0]):
            best = (len(marker), canonical, marker)
    return Attribution(best[1], best[2]) if best else Attribution()


def detect_origen(title: str, especie: str | None, lexicon: Mapping) -> Attribution:
    """Origen en tres estados.

    Se recolectan TODAS las senales antes de decidir, en vez de devolver la
    primera que calza: es la unica forma de ver una contradiccion. Una senal
    lexica ("ecuatoriano", "nacional") y una regla de formato (el calibre
    36/40, convencion del camaron de cultivo importado) tienen autoridad
    comparable sobre el origen, asi que cuando apuntan a valores distintos
    el resultado es ``contradicho`` y NO se elige un ganador.

    La evidencia de una regla de formato es el texto que el patron capturo
    -- "36/40" -- nunca el patron, para que siga siendo un fragmento literal
    del titulo verificable por el test de la invariante.
    """
    folded = fold(title)
    origen = lexicon["origen"]
    senales: list[tuple[str, str]] = []
    if marker := _find_marker(folded, origen["importado"]):
        senales.append(("importado", marker))
    if marker := _find_marker(folded, origen["nacional"]):
        senales.append(("nacional", marker))
    for rule in origen.get("reglas_de_formato", ()):
        if especie in rule["aplica_a"] and (m := re.search(rule["patron"], folded)):
            senales.append((rule["implica"], m.group(0)))
    if not senales:
        return Attribution()
    valores = {value for value, _ in senales}
    if len(valores) > 1:
        return Attribution(None, None, tuple(senales))
    value, evidence = senales[0]
    return Attribution(value, evidence)


def detect_variante(title: str, especie: str | None, lexicon: Mapping) -> Attribution:
    folded = fold(title)
    for name, markers in lexicon.get("variante", {}).get(especie, {}).items():
        if marker := _find_marker(folded, markers):
            return Attribution(name, marker)
    return Attribution()


def detect_region(title: str, lexicon: Mapping) -> Attribution:
    folded = fold(title)
    for code, markers in lexicon.get("region", {}).items():
        if marker := _find_marker(folded, markers):
            return Attribution(code, marker)
    return Attribution()


def detect_elaborado(title: str, lexicon: Mapping) -> Attribution:
    folded = fold(title)
    marker = _find_marker(folded, lexicon.get("elaborado", ()))
    return Attribution("elaborado", marker) if marker else Attribution()


def dias_a_ventana(veda: Mapping, today: date) -> int | None:
    """0 = vigente hoy. None = no aplica en el horizonte. Un entero = dias
    hasta que abra la proxima ventana."""
    if veda["tipo"] == "extractiva":
        return 0 if veda["desde"] <= today <= veda["hasta"] else None
    spans = veda["ventana_anual"]
    spans = spans if isinstance(spans, list) else [spans]
    best: int | None = None
    for span in spans:
        start_md, end_md = span.split("/")
        for year in (today.year, today.year + 1):
            start = date(year, *map(int, start_md.split("-")))
            end_year = year if end_md > start_md else year + 1
            end = date(end_year, *map(int, end_md.split("-")))
            if start <= today <= end:
                return 0
            if start > today:
                delta = (start - today).days
                best = delta if best is None else min(best, delta)
    return best


def assess(title: str, lexicon: Mapping, registry: Mapping, today: date) -> VedaAssessment | None:
    """None cuando el titulo no nombra una especie con veda en el registro."""
    especie = detect_especie(title, lexicon)
    if not especie.declared:
        return None
    sin_veda = {row["especie"] for row in registry.get("sin_veda_vigente", ())}
    if especie.value in sin_veda:
        return None
    candidates = [v for v in registry["vedas"] if v["especie"] == especie.value]
    if not candidates:
        return None

    origen = detect_origen(title, especie.value, lexicon)
    variante = detect_variante(title, especie.value, lexicon)
    region = detect_region(title, lexicon)
    elaborado = detect_elaborado(title, lexicon)

    if variante.declared:
        candidates = [v for v in candidates if v.get("variante") in (None, variante.value)]
    if region.declared:
        candidates = [v for v in candidates if region.value in v["regiones"]]
    scored = [(v, d) for v in candidates if (d := dias_a_ventana(v, today)) is not None]
    if not scored:
        return None
    scored.sort(key=lambda pair: pair[1])
    veda, dias = scored[0]

    # Un origen contradicho no sube la severidad: si el titulo se pisa a si
    # mismo, no hay base para afirmar que la prohibicion alcanza al producto.
    # Toma el mismo techo que un origen ausente, pero se reporta distinto.
    origen_value = origen.value if origen.declared else "no_declarado"
    severidad = SEVERIDAD[(veda["tipo"], origen_value)]
    cerradas = frozenset(r for v, d in scored if d == 0 for r in v["regiones"])

    notas: list[str] = []
    if origen.contradicted:
        choque = " vs ".join(f"{val} ({ev!r})" for val, ev in origen.conflicts)
        notas.append(
            f"ORIGEN CONTRADICHO: el titulo afirma {choque}. No es un dato que falte "
            "-- es un defecto en su propia ficha, y se le puede mostrar sin preguntarle nada"
        )
    variantes_posibles = {v.get("variante") for v, _ in scored}
    if not variante.declared and len(variantes_posibles) > 1 and severidad != "NULO":
        detalle = " o ".join(f"{v.get('variante')} en {d}d" for v, d in scored)
        notas.append(f"VARIANTE SIN DECLARAR: {detalle}")
    if not region.declared and cerradas and origen_value != "importado":
        n = len(cerradas)
        if n == len(REGIONES_COSTERAS):
            notas.append(f"REGION SIN DECLARAR: cerrada en las {n} regiones costeras -- la region no cambia la conclusion")
        else:
            faltan = len(REGIONES_COSTERAS) - n
            notas.append(
                f"REGION SIN DECLARAR: cerrada en {n}/{len(REGIONES_COSTERAS)} regiones costeras; "
                f"para {'la otra' if faltan == 1 else f'las otras {faltan}'} no hay decreto verificado "
                "(desconocido, no abierto)"
            )
    if veda["tipo"] == "extractiva" and elaborado.declared and severidad in ("CRITICO", "ALTO"):
        notas.append("Es un elaborado y la veda extractiva alcanza los productos derivados")

    return VedaAssessment(
        title=title, especie=especie, origen=origen, variante=variante,
        region=region, elaborado=elaborado, severidad=severidad,
        decreto=veda["decreto"], tipo=veda["tipo"], dias_a_ventana=dias,
        regiones_cerradas=cerradas, notas=tuple(notas),
    )
