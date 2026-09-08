"""ISO operations / supply-chain catalog: floor evidence, never a conformity ruling.

Loads the committed ``knowledge/iso/catalogo.json`` pack (53 norms) and answers
three questions MONITOR (or any caller) is allowed to ask:

1. What can this Kern module actually sustain? (``evidencia_nativa`` / ``de_modulo``)
2. Which norms become applicable for this plant profile? (``detectar``)
3. Given the signals on this order (cycle, scrap, kg, t·km), what may we claim?
   (``evidencia_de_orden``)

Hard rule, enforced in one place (``claim_permitido`` / ``AVISO_DETECCION``):
Kern never says the plant *cumple* or is *certificada*. Coverage is native
floor evidence, a named gap, or out of scope.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CATALOG_PATH = _REPO_ROOT / "knowledge" / "iso" / "catalogo.json"

AVISO_CATALOGO = "Resúmenes operativos. No es el texto oficial ISO ni un dictamen de conformidad."
AVISO_DETECCION = (
    "Esto detecta aplicabilidad y potencial de evidencia. "
    "No es un dictamen de conformidad ni una certificación."
)
AVISO_ORDEN = (
    "Evidencia de piso para esta orden. No es un dictamen de conformidad "
    "ni una certificación."
)

# Exact gap copy from the detectar contract fixture. Do not paraphrase: MONITOR
# and the HTTP test pin this sentence.
HUECO_14083_TKM = "Sin t·km del 3PL o de la flota, 14083 se queda en factor genérico."

COBERTURA_NATIVO = "nativo"
COBERTURA_PARCIAL = "evidencia-parcial"
COBERTURA_FUERA = "fuera-de-alcance"

_MODULO_IDS = frozenset({"smartpro", "monitor", "energy", "huella", "cmms", "cei"})

# Profile tags (and tokens mined from ``texto``) that raise aplicabilidad for a
# concrete norm. Weights combine as 1 - Π(1-w). This is Kern's applicability
# model, not an ISO score.
_TRIGGERS: tuple[tuple[str, str, float], ...] = (
    ("automotriz", "iatf-16949", 0.55),
    ("iatf", "iatf-16949", 1.00),
    ("iatf", "iso-9001", 0.28),
    ("iatf", "iso-10005", 0.55),
    ("automotriz", "iso-9001", 0.22),
    ("automotriz", "iso-22301", 0.40),
    ("oem", "iso-22301", 0.40),
    ("oem", "iso-10005", 0.30),
    ("qms", "iso-9001", 0.45),
    ("pcf", "iso-14067", 0.80),
    ("pcf", "iso-14040", 0.55),
    ("pcf", "iso-22095", 0.55),
    ("pcf", "iso-14083", 0.22),
    ("3pl", "iso-14083", 0.70),
    ("3pl", "iso-28000", 0.40),
    ("3pl", "iso-37500", 0.55),
    ("rti", "iso-17364", 1.00),
    ("rti", "iso-15459", 0.32),
    ("rti", "iso-22095", 0.28),
    ("export", "iso-14083", 0.45),
    ("export", "iso-28000", 0.32),
    ("export", "iso-15459", 0.36),
    ("muestreo", "iso-2859-1", 0.70),
    ("maestro-sku", "iso-8000-1", 0.65),
    ("maestro-sku", "iso-15459", 0.20),
    ("otif", "iso-10004", 0.55),
    ("trazabilidad", "iso-15459", 0.45),
    ("trazabilidad", "iso-22095", 0.32),
    ("trazabilidad", "iso-9001", 0.18),
    ("bcp", "iso-22301", 0.75),
)

_TEXT_ALIASES: dict[str, tuple[str, ...]] = {
    "oem": ("automotriz", "iatf"),
    "ppap": ("iatf",),
    "apqp": ("iatf",),
    "16949": ("iatf",),
    "totes": ("rti",),
    "tote": ("rti",),
    "retornables": ("rti",),
    "retornable": ("rti",),
    "14067": ("pcf",),
    "huella": ("pcf",),
    "pcf": ("pcf",),
    "tkm": ("3pl",),
    "tercerizado": ("3pl",),
}

_CLAIM_FORBIDDEN = re.compile(
    r"\b(cumple|certificad[oa]s?|conformidad garantizada|esta certificado)\b",
    re.IGNORECASE,
)

_LIST_FIELDS = ("sectores", "clientePide", "logistica", "planta", "kern")


class PerfilInvalido(ValueError):
    """Caller sent a detectar body Kern will not guess at."""


@dataclass(frozen=True)
class PerfilIso:
    sectores: tuple[str, ...] = ()
    cliente_pide: tuple[str, ...] = ()
    logistica: tuple[str, ...] = ()
    planta: tuple[str, ...] = ()
    kern: tuple[str, ...] = ()
    texto: str = ""

    def vacio(self) -> bool:
        return not (
            self.sectores or self.cliente_pide or self.logistica or self.planta or self.kern or self.texto.strip()
        )


@dataclass(frozen=True)
class SenalesOrden:
    """What MONITOR already has on the open production order."""

    tiene_ciclo: bool = False
    tiene_scrap: bool = False
    tiene_kg: bool = False
    tiene_tkm: bool = False
    modulos: tuple[str, ...] = ("smartpro", "monitor", "energy", "huella")


@lru_cache(maxsize=4)
def _read_catalog(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict) or not isinstance(data.get("normas"), list):
        raise ValueError(f"catalogo ISO invalido: {path}")
    return data


def load_catalog(path: Path | None = None) -> dict[str, Any]:
    """Return the catalog dict. Callers must not mutate the cached object."""
    return _read_catalog(str(path or DEFAULT_CATALOG_PATH))


def normas(catalog: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    return list((catalog or load_catalog())["normas"])


def buscar_norma(norm_id: str, catalog: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    needle = (norm_id or "").strip().lower()
    if not needle:
        return None
    for norma in normas(catalog):
        if str(norma.get("id", "")).lower() == needle:
            return norma
        codigo = _fold(str(norma.get("codigo", "")))
        if codigo and codigo == _fold(needle):
            return norma
    return None


def de_modulo(modulo: str, catalog: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    mid = (modulo or "").strip().lower()
    if not mid:
        return []
    return [n for n in normas(catalog) if mid in _modulos_de(n)]


def evidencia_nativa(modulo: str, catalog: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    return [n for n in de_modulo(modulo, catalog) if _cobertura(n) == COBERTURA_NATIVO]


def tarjeta_evidencia(norma: Mapping[str, Any]) -> dict[str, Any]:
    """What a UI may print. ``claimPermitido`` is the only claim sentence."""
    kern = _kern(norma)
    codigo = str(norma.get("codigo", "")).strip()
    claim = claim_permitido(norma)
    return {
        "id": norma.get("id"),
        "codigo": codigo,
        "cobertura": kern.get("cobertura"),
        "modulos": list(kern.get("modulos") or []),
        "evidenciasKern": list(kern.get("evidenciasKern") or []),
        "limite": kern.get("limite") or "",
        "comoAyuda": kern.get("comoAyuda") or "",
        "claimPermitido": claim,
    }


def claim_permitido(norma: Mapping[str, Any]) -> str:
    """A sentence MONITOR can show. Never a conformity ruling."""
    codigo = str(norma.get("codigo", "")).strip() or "esta norma"
    kern = _kern(norma)
    cobertura = _cobertura(norma)
    detalle = _detalle_evidencia(kern.get("evidenciasKern") or [])
    if cobertura == COBERTURA_NATIVO:
        claim = f"KERN tiene evidencia de piso para {codigo}"
        if detalle:
            claim += f" ({detalle})"
        claim += "."
    elif cobertura == COBERTURA_PARCIAL:
        if str(norma.get("id")) == "iso-14083":
            claim = f"{codigo} aplica; falta t·km."
        else:
            claim = f"{codigo} aplica; la evidencia de piso es parcial."
    else:
        claim = f"{codigo} queda fuera de este tramo."
    if texto_prohibido(claim):
        raise RuntimeError("claim_permitido produced a forbidden conformity sentence")
    return claim


def texto_prohibido(text: str) -> bool:
    """True when ``text`` would read as a plant-certification ruling."""
    return bool(_CLAIM_FORBIDDEN.search(text or ""))


def parse_perfil(body: object) -> PerfilIso:
    """Validate the POST /api/detectar contract. Wrong types fail closed."""
    if not isinstance(body, Mapping):
        raise PerfilInvalido("el cuerpo tiene que ser un objeto JSON")
    fields: dict[str, tuple[str, ...]] = {}
    for key in _LIST_FIELDS:
        raw = body.get(key, [])
        if raw is None:
            raw = []
        if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
            raise PerfilInvalido(f"{key} tiene que ser una lista de textos")
        fields[key] = tuple(x.strip() for x in raw if x.strip())
    texto = body.get("texto", "")
    if texto is None:
        texto = ""
    if not isinstance(texto, str):
        raise PerfilInvalido("texto tiene que ser un texto")
    kern = fields["kern"]
    unknown = [m for m in kern if m.lower() not in _MODULO_IDS]
    if unknown:
        raise PerfilInvalido(f"modulo kern desconocido: {unknown[0]}")
    return PerfilIso(
        sectores=fields["sectores"],
        cliente_pide=fields["clientePide"],
        logistica=fields["logistica"],
        planta=fields["planta"],
        kern=tuple(m.lower() for m in kern),
        texto=texto,
    )


def detectar(perfil: PerfilIso | Mapping[str, Any], catalog: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Applicability + evidence potential. Not a certificate."""
    if not isinstance(perfil, PerfilIso):
        perfil = parse_perfil(perfil)
    packed = catalog or load_catalog()
    by_id = {str(n.get("id")): n for n in normas(packed)}
    if perfil.vacio():
        return {"aviso": AVISO_DETECCION, "detectadas": []}

    tokens = _tokens_de_perfil(perfil)
    scores: dict[str, float] = {}
    for tag, norm_id, weight in _TRIGGERS:
        if tag in tokens:
            prev = scores.get(norm_id, 0.0)
            scores[norm_id] = 1.0 - (1.0 - prev) * (1.0 - weight)

    detectadas: list[dict[str, Any]] = []
    for norm_id, aplicabilidad in scores.items():
        aplicabilidad = round(min(1.0, aplicabilidad), 2)
        if aplicabilidad < 0.55:
            continue
        norma = by_id.get(norm_id)
        if norma is None:
            continue
        huecos = _huecos(norma, perfil, tokens)
        potencial = _potencial(norma, perfil.kern, huecos)
        detectadas.append(
            {
                "id": norm_id,
                "codigo": norma.get("codigo"),
                "aplicabilidad": aplicabilidad,
                "potencial": potencial,
                "huecos": huecos,
            }
        )
    detectadas.sort(key=lambda row: (-row["aplicabilidad"], -row["potencial"], row["id"]))
    return {"aviso": AVISO_DETECCION, "detectadas": detectadas}


def evidencia_de_orden(
    senales: SenalesOrden,
    catalog: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """MONITOR panel payload for the open order.

    Native floor evidence is listed only when the order already carries the
    signal that feeds it (cycle / scrap / kg). 14083 is named as a gap unless
    t·km is present — OEE never stands in for tonne-kilometres.
    """
    packed = catalog or load_catalog()
    mods = tuple(m.lower() for m in senales.modulos if m)
    nativas: list[dict[str, Any]] = []

    def _add(norm_id: str, required_mods: Sequence[str], ok: bool) -> None:
        if not ok:
            return
        if any(m not in mods for m in required_mods):
            return
        norma = buscar_norma(norm_id, packed)
        if norma is None or _cobertura(norma) == COBERTURA_FUERA:
            return
        nativas.append(tarjeta_evidencia(norma))

    _add("iso-9001", ("smartpro", "monitor"), senales.tiene_ciclo or senales.tiene_scrap)
    _add("iso-50001", ("energy", "monitor"), senales.tiene_kg)
    _add("iso-14067", ("huella", "energy"), senales.tiene_kg and (senales.tiene_ciclo or senales.tiene_scrap))

    huecos: list[str] = []
    iso_14083 = buscar_norma("iso-14083", packed)
    tarjeta_14083 = tarjeta_evidencia(iso_14083) if iso_14083 else None
    if not senales.tiene_tkm:
        huecos.append(HUECO_14083_TKM)

    resumen = _resumen_orden(nativas, senales.tiene_tkm)
    for card in nativas:
        if texto_prohibido(str(card.get("claimPermitido", ""))):
            raise RuntimeError("orden card produced a forbidden conformity sentence")
    return {
        "aviso": AVISO_ORDEN,
        "resumen": resumen,
        "evidencias": nativas,
        "fueraDeTramo": {
            "id": "iso-14083",
            "codigo": (iso_14083 or {}).get("codigo", "ISO 14083"),
            "claimPermitido": (tarjeta_14083 or {}).get("claimPermitido", "ISO 14083 aplica; falta t·km."),
            "limite": (iso_14083 or {}).get("kern", {}).get("limite", ""),
        },
        "huecos": huecos,
        "senales": {
            "ciclo": senales.tiene_ciclo,
            "scrap": senales.tiene_scrap,
            "kg": senales.tiene_kg,
            "tkm": senales.tiene_tkm,
            "modulos": list(mods),
        },
    }


def catalogo_publico(catalog: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """GET /api/iso body: same pack, estado already ``integrado`` in the file."""
    packed = dict(catalog or load_catalog())
    packed["aviso"] = packed.get("aviso") or AVISO_CATALOGO
    packed["estado"] = packed.get("estado") or "integrado"
    return packed


def _kern(norma: Mapping[str, Any]) -> Mapping[str, Any]:
    kern = norma.get("kern") or {}
    return kern if isinstance(kern, Mapping) else {}


def _cobertura(norma: Mapping[str, Any]) -> str:
    return str(_kern(norma).get("cobertura") or "").strip()


def _modulos_de(norma: Mapping[str, Any]) -> list[str]:
    raw = _kern(norma).get("modulos") or []
    if not isinstance(raw, list):
        return []
    return [str(m).strip().lower() for m in raw if str(m).strip()]


def _detalle_evidencia(items: Iterable[Any]) -> str:
    first = next((str(x).strip() for x in items if str(x).strip()), "")
    if not first:
        return ""
    # Keep the claim short enough for a MONITOR chip.
    return first.split(":")[0].strip()


def _potencial(norma: Mapping[str, Any], mods: Sequence[str], huecos: Sequence[str]) -> float:
    base = {
        COBERTURA_NATIVO: 0.45,
        COBERTURA_PARCIAL: 0.18,
        COBERTURA_FUERA: 0.04,
    }.get(_cobertura(norma), 0.10)
    own = _modulos_de(norma)
    frac = (len(set(mods) & set(own)) / len(own)) if own else 0.0
    score = base + 0.17 * frac
    evidencias = _kern(norma).get("evidenciasKern") or []
    if _cobertura(norma) == COBERTURA_PARCIAL and len(evidencias) <= 1:
        score = min(score, 0.12)
    if huecos:
        score = min(score, 0.30)
    return round(min(1.0, score), 2)


def _huecos(norma: Mapping[str, Any], perfil: PerfilIso, tokens: set[str]) -> list[str]:
    if str(norma.get("id")) != "iso-14083":
        return []
    if "tkm" in tokens or _texto_tiene_tkm(perfil.texto):
        return []
    return [HUECO_14083_TKM]


def _texto_tiene_tkm(texto: str) -> bool:
    folded = _fold(texto).replace("·", "")
    return "tkm" in folded or "t-km" in folded or "tonelada-km" in folded or "toneladas-km" in folded


def _tokens_de_perfil(perfil: PerfilIso) -> set[str]:
    tokens: set[str] = set()
    bags: Sequence[Sequence[str]] = (
        perfil.sectores,
        perfil.cliente_pide,
        perfil.logistica,
        perfil.planta,
        perfil.kern,
    )
    for bag in bags:
        for raw in bag:
            folded = _fold(raw)
            tokens.add(folded)
            tokens.update(part for part in folded.replace("_", "-").split("-") if part)
    for raw in _tokenize_texto(perfil.texto):
        tokens.add(raw)
        tokens.update(_TEXT_ALIASES.get(raw, ()))
    if perfil.sectores or perfil.cliente_pide:
        tokens.add("qms")
    # Sector synonyms the catalog stores with capitals / extra words.
    if any("automotriz" in _fold(s) or "tier" in _fold(s) for s in perfil.sectores):
        tokens.add("automotriz")
    if "maestro" in tokens and "sku" in tokens:
        tokens.add("maestro-sku")
    return {t for t in tokens if t}


def _tokenize_texto(texto: str) -> list[str]:
    folded = _fold(texto)
    return [part for part in re.split(r"[^a-z0-9]+", folded) if part]


def _fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value or "")
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return stripped.replace("·", "").lower().strip()


def _resumen_orden(nativas: Sequence[Mapping[str, Any]], tiene_tkm: bool) -> str:
    chips: list[str] = []
    labels = {
        "iso-9001": "ISO 9001 (piezas buenas / no conformes)",
        "iso-50001": "ISO 50001 (kWh/kg)",
        "iso-14067": "ISO 14067 (tramo de proceso)",
    }
    for card in nativas:
        chips.append(labels.get(str(card.get("id")), str(card.get("codigo"))))
    if chips:
        head = "Evidencia nativa: " + ", ".join(chips) + "."
    else:
        head = "Sin evidencia nativa en esta orden: faltan ciclo, scrap o kg."
    if tiene_tkm:
        tail = "14083: hay t·km en la orden; el well-to-wheel del 3PL sigue fuera."
    else:
        tail = "14083: fuera de este tramo — falta t·km del 3PL."
    return f"{head} {tail}"
