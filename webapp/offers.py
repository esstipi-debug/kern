"""Structured sales-package data backing GET /paquetes and GET /paquetes/{slug}.

Single source of truth for price/cadence/scope is documentation/MONETIZATION_BRIEF.md's
"Estructura de empaquetado comercial" table (verified against
documentation/paquetes/README.md) - extracted here ONCE into a structured form so the
sales routes never re-derive or duplicate that prose. The full one-pager copy stays
exclusively in documentation/paquetes/*.md; GET /paquetes/{slug} fetches it, it is
never copied into this module.

``lane`` is the cold-catalog filter:
- ``self_serve`` -- app-store / CSV prices (Gratis, Alerts, Starter Mass, Chequeo
  Stocky, Starter LatAm). Shown first on /paquetes. This is what Shopify and
  Odoo listings must match.
- ``operator`` -- human-in-the-loop retainers (Diagnostico, Starter 900, Growth,
  Liquidacion, Posicion de precios). Shown on /paquetes under a separate heading.
- ``upgrade`` -- Scale, Retainer, proyectos de red/sourcing. Detail URLs still
  work; they are never listed on the cold /paquetes grid (0 paying clients,
  those tickets are not an entry offer).
"""

from __future__ import annotations

import os
import urllib.parse
from dataclasses import dataclass

LANE_SELF_SERVE = "self_serve"
LANE_OPERATOR = "operator"
LANE_UPGRADE = "upgrade"
COLD_LANES = (LANE_SELF_SERVE, LANE_OPERATOR)


@dataclass(frozen=True)
class Offer:
    slug: str
    name: str
    price: str
    cadence: str
    recibe: str
    para_quien: str
    md_file: str
    lane: str

    @property
    def stripe_env_var(self) -> str:
        """Env var name for this package's Stripe Payment Link, e.g. STRIPE_LINK_STARTER_FUNDAMENTOS."""
        return "STRIPE_LINK_" + self.slug.upper().replace("-", "_")


OFFERS: tuple[Offer, ...] = (
    Offer(
        slug="gratis",
        name="Gratis — Prueba sin permiso",
        price="USD 0 para siempre (100 SKUs, 1 archivo, watermark)",
        cadence="Gratis para siempre",
        recibe="3 tools: data_quality (intake_scan), abc_xyz, forecast — 1 Excel de muestra precargado, sin tarjeta",
        para_quien="Quiere probar Kern sin hablar con nadie ni pasar tarjeta",
        md_file="gratis.md",
        lane=LANE_SELF_SERVE,
    ),
    Offer(
        slug="kern-alerts",
        name="Kern Alerts",
        price="USD 49/mes (hasta 1.000 SKUs, 1 ubic.) o USD 99/mes (hasta 5.000 SKUs, multi)",
        cadence="Mensual, 100% software (solo notifica)",
        recibe=(
            "3 monitores: rop_breach, stockout_projected, excess_growing — cantidad sugerida "
            "con disclaimer; sin writeback, sin revision humana"
        ),
        para_quien="Merchant Shopify/Excel que quiere avisos de reorden, no un operador",
        md_file="kern-alerts.md",
        lane=LANE_SELF_SERVE,
    ),
    Offer(
        slug="starter-mass",
        name="Starter Mass",
        price="USD 79/mes (hasta 500 SKUs, +$25 cada 250 SKUs, techo $290)",
        cadence="Mensual self-serve",
        recibe="5 tools: data_quality, abc_xyz, forecast, inventory_optimization, excel_replenishment",
        para_quien="Jefe de ops que hoy compra a ojo en Excel, quiere probar sin hablar con nadie",
        md_file="starter-mass.md",
        lane=LANE_SELF_SERVE,
    ),
    Offer(
        slug="chequeo-stocky",
        name="Chequeo de migracion Stocky",
        price="USD 350-400 unico",
        cadence="Unico, operator-run sobre CSV de Stocky",
        recibe=(
            "Veredicto Stocky vs Shopify-nativo (OCs, proveedores, reorden, forecast) + "
            "auditoria del maestro de SKUs; read-only, sin API Shopify"
        ),
        para_quien="Tienda Shopify que usaba Stocky (cierre 31-ago-2026) y necesita reemplazo honesto",
        md_file="chequeo-stocky.md",
        lane=LANE_SELF_SERVE,
    ),
    Offer(
        slug="starter-fundamentos-latam",
        name="Starter LatAm -- Fundamentos de Inventario (alcance reducido)",
        price="USD 250-300/mes (alcance reducido) — o Starter Mass USD 79/mes self-serve sin alcance reducido",
        cadence="Mensual, alcance fijo (8 tools)",
        recibe=(
            "8 tools: data_quality, abc_xyz, forecast, whatif, inventory_optimization, "
            "excel_replenishment, cycle_count, newsvendor"
        ),
        para_quien='E-commerce/distribuidor mono-almacen en LatAm, USD 1-10M, compra "a ojo" '
                   "en Excel -- alcance de entrega mas liviano que el Starter Anglosfera, no el "
                   "mismo producto mas barato",
        md_file="starter-fundamentos-latam.md",
        lane=LANE_SELF_SERVE,
    ),
    Offer(
        slug="diagnostico-arranque",
        name="Diagnostico de Arranque",
        price="USD 1.500-2.500 unico",
        cadence="Unico, sprint de 2 semanas",
        recibe="4 tools: data_quality, abc_xyz, excess_obsolete, financial_kpis",
        para_quien="Primer contacto, cero confianza construida",
        md_file="diagnostico-arranque.md",
        lane=LANE_OPERATOR,
    ),
    Offer(
        slug="starter-fundamentos",
        name="Starter -- Fundamentos de Inventario",
        price="USD 900/mes (piso ~500 SKUs, +$40/mes cada bloque de 250 SKUs, techo $1.500)",
        cadence="Mensual, alcance variable por catalogo",
        recibe=(
            "15 tools: forecast, abc_xyz, whatif, inventory_optimization, newsvendor, "
            "excel_replenishment, cycle_count, data_quality, pricing, excess_obsolete, "
            "financial_kpis, reconciliation, landed_cost, returns, risk"
        ),
        para_quien='E-commerce/distribuidor mono-almacen, USD 1-10M, compra "a ojo" en Excel -- con operador',
        md_file="starter-fundamentos.md",
        lane=LANE_OPERATOR,
    ),
    Offer(
        slug="growth-operacion",
        name="Growth -- Operacion Completa de SC",
        price="USD 1.500/mes (piso ~2.000 SKUs, +$60/mes cada bloque de 500 SKUs, techo $3.200)",
        cadence="Mensual + QBR trimestral",
        recibe=(
            "26 tools: todo Starter + multi_echelon, ddmrp, simulation, drp, odoo_replenishment, "
            "fefo, sourcing, acceptance_sampling, cost_to_serve, learning_curve, dea"
        ),
        para_quien="Empresa en crecimiento, multi-almacen/canal, con o migrando a ERP (Odoo)",
        md_file="growth-operacion.md",
        lane=LANE_OPERATOR,
    ),
    Offer(
        slug="sprint-liquidacion",
        name="Sprint de Liquidacion",
        price="10-20% del cash recuperado (piso USD 1.500)",
        cadence="Unico, sprint de 2-3 semanas",
        recibe=(
            "3-4 tools: data_quality, excess_obsolete, markdown_liquidation "
            "(+ pricing opcional, si hay historial de precios)"
        ),
        para_quien="Stock muerto/excedente ya diagnosticado, decidido a liquidar -- "
                   "no quiere pagar un fee fijo por algo que todavia no se recupero",
        md_file="sprint-liquidacion.md",
        lane=LANE_OPERATOR,
    ),
    Offer(
        slug="diagnostico-posicion-precios",
        name="Diagnostico de Posicion de Precios",
        price="USD 2.000-3.500 unico",
        cadence="Unico, sprint de 2 semanas (one-shot)",
        recibe="1 tool: price_intelligence",
        para_quien="Vende productos comparables online, quiere saber donde esta caro/barato "
                   "frente a la competencia con evidencia trazable, sin monitoreo continuo todavia",
        md_file="diagnostico-posicion-precios.md",
        lane=LANE_OPERATOR,
    ),
    Offer(
        slug="scale-red-sop",
        name="Scale -- Red, S&OP y Mando Ejecutivo",
        price="USD 3.200/mes (flat)",
        cadence="Quincenal + S&OP mensual",
        recibe=(
            "Las 35 tools del catalogo completo (+ facility_location, transportation, "
            "warehouse_layout, slotting, queuing, scheduling, sop, earned_value, leadership_chain)"
        ),
        para_quien="Mid-market con red real (2+ plantas/CDs) -- no se vende en frio",
        md_file="scale-red-sop.md",
        lane=LANE_UPGRADE,
    ),
    Offer(
        slug="retainer-ejecutivo",
        name="Retainer Ejecutivo Fraccional",
        price="USD 4.500/mes (flat = Scale x 1.4)",
        cadence="Mensual + cadencia semanal + escalamiento con SLA",
        recibe="Mismas 35 tools de Scale -- la diferencia es gobierno, no capacidad",
        para_quien="Upgrade para un cliente Scale existente (6-18 meses en Scale), mandato de "
                   "VP/COO fraccional -- no se vende en frio como opcion de entrada",
        md_file="retainer-ejecutivo.md",
        lane=LANE_UPGRADE,
    ),
    Offer(
        slug="proyecto-red-almacen",
        name="Proyecto de Red, Almacen y Operacion",
        price="USD 8.000-18.000 unico",
        cadence="Unico, 4-8 semanas",
        recibe="6 tools: facility_location, transportation, warehouse_layout, slotting, queuing, scheduling",
        para_quien="Inflexion estructural: nueva bodega, rediseno de red/almacen -- cotizacion, no menu frio",
        md_file="proyecto-red-almacen.md",
        lane=LANE_UPGRADE,
    ),
    Offer(
        slug="proyecto-sourcing",
        name="Proyecto de Sourcing y Costo de Importacion",
        price="USD 5.000-10.000 unico",
        cadence="Unico, recurrible trimestral/anual",
        recibe="3 tools: sourcing, landed_cost, acceptance_sampling",
        para_quien="Importadores / manufactura offshore -- cotizacion, no menu frio",
        md_file="proyecto-sourcing.md",
        lane=LANE_UPGRADE,
    ),
)

_OFFERS_BY_SLUG: dict[str, Offer] = {offer.slug: offer for offer in OFFERS}


def get_offer(slug: str) -> Offer | None:
    return _OFFERS_BY_SLUG.get(slug)


def catalog_offers() -> tuple[Offer, ...]:
    """Offers listed on the cold /paquetes grid (self-serve + operator)."""
    return tuple(offer for offer in OFFERS if offer.lane in COLD_LANES)


def offers_in_lane(lane: str) -> tuple[Offer, ...]:
    return tuple(offer for offer in OFFERS if offer.lane == lane)


@dataclass(frozen=True)
class Cta:
    label: str
    href: str
    kind: str  # "calendly" | "stripe" | "mailto"


_SAFE_URL_SCHEMES = ("http://", "https://")


def is_safe_external_url(url: str) -> bool:
    """Reject anything but http(s) -- blocks a javascript: URI from an
    operator-configured env var (CALENDLY_URL, STRIPE_LINK_*, OPERATOR_LINKEDIN)
    from ever landing in a rendered href/src."""
    return url.startswith(_SAFE_URL_SCHEMES)


def is_safe_same_origin_or_external_url(url: str) -> bool:
    """Like is_safe_external_url, but also allows a same-origin relative path
    (a single leading "/", not "//" which is a protocol-relative URL) -- used
    for OPERATOR_PHOTO_URL, which documentation/operator/07_setup_venta.md
    instructs operators to set to a path under /static/operator/."""
    if url.startswith("/") and not url.startswith("//"):
        return True
    return is_safe_external_url(url)


def _mailto(offer: Offer, intent: str) -> str:
    """Degraded CTA: mailto with a prefilled subject. Recipient may be empty (no
    OPERATOR_EMAIL configured yet) -- a bare `mailto:?subject=...` still opens the
    visitor's mail client with the subject/body ready, which is a clean degrade,
    not a broken link."""
    operator_email = os.environ.get("OPERATOR_EMAIL", "").strip()
    subject = f"{intent}: {offer.name} - Kern"
    return "mailto:" + operator_email + "?subject=" + urllib.parse.quote(subject)


def resolve_agendar_cta(offer: Offer) -> Cta:
    calendly_url = os.environ.get("CALENDLY_URL", "").strip()
    if calendly_url and is_safe_external_url(calendly_url):
        return Cta(label="Agendar una llamada", href=calendly_url, kind="calendly")
    return Cta(label="Agendar una llamada", href=_mailto(offer, "Agendar"), kind="mailto")


def resolve_pagar_cta(offer: Offer) -> Cta:
    stripe_link = os.environ.get(offer.stripe_env_var, "").strip()
    if stripe_link and is_safe_external_url(stripe_link):
        return Cta(label="Pagar / Empezar", href=stripe_link, kind="stripe")
    return Cta(label="Pagar / Empezar", href=_mailto(offer, "Pagar"), kind="mailto")
