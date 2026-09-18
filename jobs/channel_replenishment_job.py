"""Channel -> ERP replenishment: demand from the storefront, reorder point in the ERP.

The first job in Kern that crosses two connectors, and the reason it exists is that each
single-connector tool is blind in one eye:

- ``shopify_replenishment`` reads real demand but has nowhere to write a plan: a
  storefront's only writable inventory number is physical on-hand (see
  ``jobs/shopify_job.py``), so it can only hand the quantities off.
- ``odoo_replenishment`` can write a reorder point, but reads demand from the ERP's own
  ``sale.order`` -- which in a DTC business is empty or partial, because the selling
  happens in the storefront.

Joining them gives the whole loop: forecast each SKU from the CHANNEL's orders, compare
against the ERP's stock, and stage the restock as a reversible reorder point in the ERP,
through the writeback plane the ERP connector already owns. No new write path is added
here; this job composes two existing ones.

**The join goes through the entity layer, not a dict on the SKU string.** ``src/entities.py``
declares ``unit_cost`` as owned by **whichever ERP is in play**, so a channel's idea of cost
-- a display artefact -- cannot overwrite the book value the restock is valued at and the
financial escalation threshold is measured against. A hand-rolled merge would re-create
exactly the bug that module was written to prevent. The store is opened in memory per run by
default, so the join costs no persistence unless an operator asks for it.

That ownership used to be the constant ``SOURCE_ODOO``, and making the ERP swappable is
what exposed why it could not stay one: ownership breaks ties only among systems that
reported, so naming an absent system as the owner does not fail loudly -- it hands the tie
to the channel. Measured with Unleashed as the ERP: Shopify's 18.00 beat the ERP's 12.00
until ``build_scm_registry`` learned to take a ``cost_owner``.

**Stock divergence is an output, not an aside.** The two systems both report on-hand. When
they disagree, one of them is wrong, and neither single-connector tool can see it. Planning
happens against the ERP's figure -- that is where the reorder point lives and what the ERP's
own replenishment compares against -- but the disagreement is reported rather than resolved
silently.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from nosilo_core.access import Principal
from nosilo_core.entities import SqliteEntityStore
from nosilo_core.escalation import escalation_banner, maybe_escalate_financial
from nosilo_core.guided import (
    ExecutionOption,
    GuidedOutcome,
    Residual,
    as_options,
    verify_guided,
)
from nosilo_core.writeback import Changeset
from src.connectors.meli import MeliConnector, demo_meli_commerce
from src.connectors.odoo import OdooConnector, OdooError, demo_odoo
from src.connectors.replenish import (
    daily_demand_by_sku,
    demand_by_sku,
    demand_points_by_sku,
)
from src.connectors.shopify import ShopifyConnector, demo_shopify
from src.connectors.unleashed import (
    UnleashedConnector,
    UnleashedCredentials,
    live_http,
)
from src.ddmrp import net_flow_position
from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.entities import (
    SOURCE_MELI,
    SOURCE_ODOO,
    SOURCE_SHOPIFY,
    SOURCE_UNLEASHED,
    build_scm_registry,
    observe_product,
)
from src.export import write_summary_csv
from src.forecasting import forecast_demand

from . import odoo_job

#: Reading the catalogue to plan against it is a service action: the job is not an agent
#: exercising a human's authority, and inventing an ``on_behalf_of`` to satisfy the agent
#: branch would put a fictional accountable owner in the access log.
_PRINCIPAL = Principal(id="kern.channel_replenishment", kind="service", roles=frozenset({"planner"}))
_PURPOSE = "planning"

#: Below this the two systems are treated as agreeing. Not zero: a unit of drift between a
#: storefront and an ERP is ordinary timing (an order in flight), not a data-quality finding,
#: and reporting it would bury the divergences that matter.
DIVERGENCE_TOLERANCE = 1.0


@dataclass(frozen=True)
class ChannelLine:
    sku: str
    channel_demand_per_period: float
    erp_on_hand: float
    channel_on_hand: float
    target: float
    restock_qty: float
    lead_time_days: float
    #: Units on open purchase orders in the ERP, netted out of ``restock_qty``, or
    #: ``None`` when this ERP could not be asked (or does not expose them). ``None`` and
    #: ``0.0`` mean different things: the first is "nobody told us", the second is
    #: "measured, and nothing is inbound".
    on_order: float | None = None
    #: The inventory position the quantity was sized against -- ``src.ddmrp
    #: .net_flow_position(erp_on_hand, on_order or 0, 0)``. Equals ``erp_on_hand`` when
    #: nothing was netted, and is emitted so the arithmetic is auditable from the file.
    position: float | None = None
    #: Unidades por dia CALENDARIO del canal (``daily_demand_by_sku``), que NO es
    #: ``channel_demand_per_period``: el periodo de esa serie es un dia-con-pedidos.
    #: ``None`` cuando ninguna fecha de pedido se pudo leer -- nunca ``0.0``.
    avg_daily_demand: float | None = None


@dataclass(frozen=True)
class StockDivergence:
    """The same SKU, two systems, two answers about how many are on the shelf."""

    sku: str
    channel_on_hand: float
    erp_on_hand: float

    @property
    def delta(self) -> float:
        """Channel minus ERP: positive means the storefront claims more than the ERP holds,
        which is the direction that oversells."""
        return self.channel_on_hand - self.erp_on_hand


@dataclass(frozen=True)
class ChannelReplenishmentReport:
    channel_source: str
    erp_source: str
    lines: tuple[ChannelLine, ...]
    restock: dict[str, float]
    restock_value: float
    divergences: tuple[StockDivergence, ...]
    channel_only: tuple[str, ...]
    erp_only: tuple[str, ...]
    n_matched: int
    total_restock: float
    cover_periods: float
    truncated: bool
    changeset: Changeset | None
    outcome: GuidedOutcome
    summary: str
    #: Units on open purchase orders in the ERP, per SKU, netted out of every quantity
    #: above. ``None`` -- never ``{}`` -- when the ERP could not be asked at all; a SKU
    #: simply absent from a real map has no open PO, which is a different statement and
    #: the one every surface below must keep apart from "we could not read".
    on_order_by_sku: dict[str, float] | None = None
    #: Lines that carried an on-order figure. Zero on a SUCCESSFUL read just means no SKU
    #: has an open purchase order, so it never decides whether the plan was netted --
    #: ``on_order_by_sku is None`` does.
    n_netted: int = 0
    #: Why the quantities are, or are not, net of what the ERP already has on order.
    netting_note: str = ""
    #: Que paso con la memoria del Tower en esta corrida: el ciclo con el que se
    #: escribio el snapshot ``stock``, o por que no se escribio
    #: (``jobs/state_bridge.py``). Viaja en el reporte y no solo en el resumen, para
    #: que un llamador pueda comprobarlo sin parsear prosa.
    state_note: str = ""
    #: La demanda REALIZADA que el plan pronostico, CON sus fechas, por SKU
    #: (``src.connectors.replenish.demand_points_by_sku``). Viaja en el reporte para que
    #: el puente del Tower la escriba como ``actual_qty`` sin volver a leer el backend, y
    #: con las fechas reales: quien despues pronostica sobre esos puntos no puede
    #: inventarles un dia.
    demand_points: dict[str, tuple[tuple[str, float], ...]] = field(default_factory=dict)


#: Sales channels this job can read demand from. A channel qualifies when it implements
#: ``InventorySource`` -- nothing more is required of it, because the ERP is what gets
#: written to. Adding the third one is a resolver function and an entry here.
CHANNELS = ("shopify", "meli")
DEFAULT_CHANNEL = "shopify"


def _shopify_channel(params: dict) -> tuple[object, str]:
    location = params.get("location_id") or os.environ.get("SHOPIFY_LOCATION_ID") or "gid://shopify/Location/1"
    injected = params.get("shopify_rpc")
    if injected is not None:
        return ShopifyConnector(injected, location_id=location), "injected Shopify RPC"
    shop = params.get("shopify_shop_url") or os.environ.get("SHOPIFY_SHOP_URL")
    token = params.get("shopify_access_token") or os.environ.get("SHOPIFY_ACCESS_TOKEN")
    if shop and token:
        import httpx

        from src.connectors.shopify import ShopifyCommerceClient
        from src.connectors.shopify_inventory import ShopifyInventoryClient

        http = httpx.Client(base_url=shop, headers={"X-Shopify-Access-Token": token}, timeout=30.0)
        return (
            ShopifyConnector(ShopifyCommerceClient(http), ShopifyInventoryClient(http), location_id=location),
            f"live Shopify ({shop})",
        )
    return ShopifyConnector(demo_shopify(), location_id=location), "in-memory Shopify stand-in (demo data)"


def _meli_channel(params: dict) -> tuple[object, str]:
    injected = params.get("meli_rpc")
    if injected is not None:
        return MeliConnector(injected), "injected MercadoLibre RPC"
    token = params.get("meli_access_token") or os.environ.get("MELI_ACCESS_TOKEN")
    user_id = params.get("meli_user_id") or os.environ.get("MELI_USER_ID")
    if token and user_id:
        import httpx

        from src.connectors.meli import MeliCommerceClient

        http = httpx.Client(
            base_url="https://api.mercadolibre.com",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30.0,
        )
        return MeliConnector(MeliCommerceClient(http, user_id=user_id)), f"live MercadoLibre (seller {user_id})"
    return MeliConnector(demo_meli_commerce()), "in-memory MercadoLibre stand-in (demo data)"


_CHANNEL_RESOLVERS = {
    "shopify": (_shopify_channel, SOURCE_SHOPIFY),
    "meli": (_meli_channel, SOURCE_MELI),
}


def _channel_connector(params: dict) -> tuple[object, str, str]:
    """Resolve the demand side. Returns (connector, human label, entity-system constant).

    The system constant is not cosmetic: it is what tells the entity layer whose claim a
    field came from, and therefore whether it may overwrite the ERP's owned ``unit_cost``.
    """
    channel = str(params.get("channel") or DEFAULT_CHANNEL).lower()
    if channel not in _CHANNEL_RESOLVERS:
        raise ValueError(f"unknown channel {channel!r}; expected one of {', '.join(CHANNELS)}")
    resolver, system = _CHANNEL_RESOLVERS[channel]
    connector, label = resolver(params)
    return connector, label, system


def _odoo_erp(params: dict) -> tuple[object, str]:
    injected = params.get("odoo_rpc")
    if injected is not None:
        return OdooConnector(injected), "injected Odoo RPC"
    url = params.get("odoo_url") or os.environ.get("ODOO_URL")
    db = params.get("odoo_db") or os.environ.get("ODOO_DB")
    user = params.get("odoo_username") or os.environ.get("ODOO_USERNAME")
    key = params.get("odoo_api_key") or os.environ.get("ODOO_API_KEY")
    if url and db and user and key:
        from src.connectors.odoo import OdooClient

        return OdooConnector(OdooClient(url, db, user, key)), f"live Odoo (db={db})"
    return OdooConnector(demo_odoo()), "in-memory Odoo stand-in (demo data)"


def _unleashed_erp(params: dict) -> tuple[object, str]:
    """Unleashed es ``[CRED]``: sin credenciales se NIEGA, no inventa datos.

    La rama original tenia un ``demo_unleashed()`` en memoria y llamaba
    ``UnleashedConnector(algo)`` con un solo argumento. `main` colapso esa capa:
    hoy el conector toma un cliente httpx MAS ``UnleashedCredentials``, porque la
    ``api_key`` FIRMA cada request y no viaja en ninguna cabecera -- un doble en
    memoria no puede ejercitar eso, que es justamente donde estaba el bug de firma
    que los tests de `main` cazan.

    Odoo si conserva su camino de demo (`demo_odoo()` vive en `main`). Para
    Unleashed, pedir credenciales es la respuesta honesta: un stand-in que no firma
    daria verde sobre el unico paso que en produccion falla.
    """
    creds_id = params.get("unleashed_api_id") or os.environ.get("UNLEASHED_API_ID")
    creds_key = params.get("unleashed_api_key") or os.environ.get("UNLEASHED_API_KEY")
    injected = params.get("unleashed_http")
    if injected is not None:
        creds = UnleashedCredentials(api_id=creds_id or "test", api_key=creds_key or "test")
        return UnleashedConnector(injected, creds), "injected Unleashed HTTP"
    if not (creds_id and creds_key):
        raise ValueError(
            "unleashed necesita UNLEASHED_API_ID y UNLEASHED_API_KEY (o los params "
            "unleashed_api_id / unleashed_api_key). No hay stand-in offline: la api_key "
            "firma cada request, y un doble que no firma aprueba el unico paso que falla "
            "en produccion. Para correr sin credenciales, usa erp='odoo'."
        )
    creds = UnleashedCredentials(api_id=creds_id, api_key=creds_key)
    return UnleashedConnector(live_http(creds), creds), "live Unleashed"


#: Systems that can RECEIVE a plan: they hold a reorder point, a planning parameter their
#: own replenishment acts on. This is the same line that keeps Shopify and MercadoLibre out
#: of the ERP slot -- and it is why Xero is absent despite >60% of the Australian market:
#: its Accounting API has no reorder point, and Inventory Plus's levels are informational.
#: The second element is the entity-system constant, which decides who owns ``unit_cost``.
_ERP_RESOLVERS = {
    "odoo": (_odoo_erp, SOURCE_ODOO, "product.product"),
    "unleashed": (_unleashed_erp, SOURCE_UNLEASHED, "product"),
}
ERPS = tuple(_ERP_RESOLVERS)
DEFAULT_ERP = "odoo"


def _erp_connector(params: dict) -> tuple[object, str, str, str]:
    """Resolve the destination. Returns (connector, label, entity-system, source entity)."""
    erp = str(params.get("erp") or DEFAULT_ERP).lower()
    if erp not in _ERP_RESOLVERS:
        raise ValueError(f"unknown erp {erp!r}; expected one of {', '.join(ERPS)}")
    resolver, system, source_entity = _ERP_RESOLVERS[erp]
    connector, label = resolver(params)
    return connector, label, system, source_entity


def prepare(data_path: str | None = None, params: dict | None = None) -> dict:
    """Open both connections, read both catalogues, resolve them to one identity each.

    ``data_path`` is unused (two live sources). ``params['entity_store_path']`` persists
    the cross-system xref; the default keeps the store in memory so a planning run leaves
    nothing behind and two runs cannot interfere.
    """
    params = params or {}
    channel, channel_source, channel_system = _channel_connector(params)
    erp, erp_source, erp_system, erp_source_entity = _erp_connector(params)

    # The registry is built around WHICH ERP is in play, not around Odoo. Ownership only
    # decides ties among systems that reported, so a registry naming an absent system as
    # the cost owner is not a no-op -- it hands the tie to the channel, whose cost is a
    # display figure. See src/entities.sku_entity_type.
    store = SqliteEntityStore(
        build_scm_registry(cost_owner=erp_system), params.get("entity_store_path") or ":memory:"
    )

    channel_products = channel.list_products()
    erp_products = erp.list_products()

    # ERP first, so its owned fields are established before the channel speaks. Ownership
    # makes the order irrelevant to the result -- asserting that is the point of
    # test_the_erp_cost_wins_even_though_the_storefront_reports_its_own.
    ids_by_sku: dict[str, str] = {}
    for product in erp_products:
        ids_by_sku[product.sku] = observe_product(
            store, product, system=erp_system, source_entity=erp_source_entity
        ).nosilo_id
    source_entity = "variant" if channel_system == SOURCE_SHOPIFY else "item"
    for product in channel_products:
        result = observe_product(store, product, system=channel_system, source_entity=source_entity)
        ids_by_sku.setdefault(product.sku, result.nosilo_id)

    channel_skus = {p.sku for p in channel_products}
    erp_skus = {p.sku for p in erp_products}
    return {
        "channel": channel,
        "erp": erp,
        "erp_system": erp_system,
        "store": store,
        "channel_system": channel_system,
        "channel_source": channel_source,
        "erp_source": erp_source,
        "ids_by_sku": ids_by_sku,
        "matched": sorted(channel_skus & erp_skus),
        "channel_only": tuple(sorted(channel_skus - erp_skus)),
        "erp_only": tuple(sorted(erp_skus - channel_skus)),
        "n_products": len(channel_skus | erp_skus),
    }


def read_erp_on_order(erp: object) -> tuple[dict[str, float] | None, str]:
    """Units already on open purchase orders in the ERP, per SKU, or why they are unknown.

    The plan sizes against the inventory POSITION (stock plus what is already bought), not
    the shelf, so a delivery that transport delayed is not ordered a second time. The Odoo
    path reuses ``jobs.odoo_job.read_on_order`` literally rather than re-implementing the
    read: the state filter, the pending rule and the unit-of-measure guard all live there.

    ``None`` with the reason attached whenever the figure could not be read, never ``{}``:
    an empty map reads downstream as "measured, and nothing is on order", which is the
    claim this function exists to avoid making. An ERP that simply does not expose open
    purchase orders (Unleashed today) says so in the note instead of going quiet.
    """
    if isinstance(erp, OdooConnector):
        try:
            return odoo_job.read_on_order(erp)
        except OdooError as exc:                     # defensive: read_on_order catches its own
            return None, f"in-transit stock was NOT netted: {exc}"
    return None, (
        f"in-transit stock was NOT netted: {type(erp).__name__} does not expose open purchase "
        "orders, so what is already bought could not be read"
    )


def _resolved_cost(store: SqliteEntityStore, nosilo_id: str) -> float:
    """The book cost the entity layer resolved, which is the ERP's by declared ownership.

    Falls back to 0.0 when no source reported one: ``_reported_money`` drops a
    non-positive cost as "not reported", so an uncosted SKU has no ``unit_cost`` at all
    rather than a zero that would quietly deflate the restock's value.
    """
    values = store.read(nosilo_id, principal=_PRINCIPAL, purpose=_PURPOSE).values
    cost = values.get("unit_cost")
    return float(cost) if cost is not None else 0.0


def _build_outcome(
    n_restock: int,
    total_restock: float,
    *,
    restock_value: float,
    financial_threshold: float,
    n_divergent: int,
    netted: bool = True,
    netting_note: str = "",
) -> GuidedOutcome:
    """Ranked, executable options. Unlike the channel-only tool, the recommended one WRITES:
    the ERP has a reorder point to put the plan in, and the write is reversible."""
    if n_restock > 0:
        options = [
            ExecutionOption(
                label="Apply reorder points in the ERP",
                summary=f"Raise the reorder point on {n_restock} SKU(s) to the target cover derived "
                        "from real storefront demand, so the ERP's own replenishment raises the orders.",
                score=3.0, recommended=True,
                action="stage + apply stock.warehouse.orderpoint.product_min_qty (reversible)",
                tradeoffs="lowest touch; the ERP drives the POs; fully reversible",
            ),
            ExecutionOption(
                label="Raise draft purchase orders in the ERP",
                summary=f"Create draft POs for the {total_restock:,.0f} units short across {n_restock} SKU(s).",
                score=2.0,
                action="create draft purchase.order lines for the restock quantities",
                tradeoffs="direct buy; needs a supplier + buyer confirmation in the ERP",
            ),
            ExecutionOption(
                label="Export the plan for review",
                summary="Hand off the channel-derived replenishment plan without writing anything.",
                score=1.0,
                action="export the replenishment plan (no write-back)",
                tradeoffs="zero risk; manual follow-up",
            ),
        ]
        summary = (
            f"{n_restock} SKU(s) below target cover on real channel demand "
            f"({total_restock:,.0f} units short): choose how to replenish."
        )
    else:
        options = [
            ExecutionOption(
                label="Hold - stock covers channel demand",
                summary="Every matched SKU is above its target cover against real storefront demand.",
                score=3.0, recommended=True,
                action="monitor; no write-back needed", tradeoffs="no cost",
            ),
            ExecutionOption(
                label="Tighten the cover target",
                summary="Lower the target cover to release working capital tied up in stock.",
                score=1.0,
                action="re-run with a shorter cover_periods", tradeoffs="frees cash; less buffer",
            ),
        ]
        summary = "All matched SKUs above target cover: choose how to proceed."

    if n_divergent:
        # Ranked below the plan but above re-planning: a plan built on a stock figure the
        # two systems disagree about is worth acting on, and worth knowing about first.
        options.insert(
            1,
            ExecutionOption(
                label=f"Investigate {n_divergent} stock divergence(s) first",
                summary="The storefront and the ERP disagree about how many units are on hand. "
                        "The plan uses the ERP's figure; if the storefront's is right, the plan is "
                        "calibrated against a stock level that does not exist.",
                score=2.5,
                action="reconcile the divergent SKUs before applying (count, then correct the loser)",
                tradeoffs="delays the replenishment; prevents planning on a phantom stock level",
            ),
        )

    residuals = []
    if not netted and n_restock > 0:
        # Sin esto, cada cantidad de arriba puede estar comprando otra vez una OC que el
        # ERP ya emitio y que transporte simplemente atraso.
        residuals.append(Residual(
            description=netting_note or "the quantities do not net open purchase orders in the ERP",
            risk_if_skipped="a purchase order the ERP already raised is bought a second time; "
                            "check open POs by hand before applying any reorder point",
        ))

    return maybe_escalate_financial(
        as_options(summary, options, residuals=residuals), restock_value, financial_threshold
    )


def run(
    payload: dict,
    *,
    cover_periods: float = 8.0,
    financial_threshold: float = 50_000.0,
    idempotency_key: str = "channel-replenish-1",
) -> ChannelReplenishmentReport:
    """Forecast from channel orders, compare against ERP stock, stage the reorder points."""
    # Any InventorySource with a ``truncated`` flag; see CHANNELS. Typed as the union
    # rather than a Protocol because ``truncated`` is a connector courtesy, not part of
    # InventorySource, and inventing a Protocol for one attribute would be ceremony.
    channel: ShopifyConnector | MeliConnector = payload["channel"]
    # Any ERP in ERPS: reads through InventorySource, writes through stage_restock.
    erp: OdooConnector | UnleashedConnector = payload["erp"]
    store: SqliteEntityStore = payload["store"]
    matched: list[str] = list(payload["matched"])

    channel_orders = channel.orders()
    series = demand_by_sku(channel_orders)
    points = demand_points_by_sku(channel_orders)
    # La misma lectura, en dias calendario: el plan razona por dia-con-pedidos y quien
    # cuenta dias de cobertura necesita la otra unidad (ver `daily_demand_by_sku`).
    daily_demand = daily_demand_by_sku(channel_orders)
    channel_on_hand = {lvl.sku: lvl.available for lvl in channel.inventory_levels()}
    erp_on_hand = {lvl.sku: lvl.available for lvl in erp.inventory_levels()}
    lead_times = erp.lead_times()
    on_order_by_sku, netting_note = read_erp_on_order(erp)
    n_netted = 0

    lines: list[ChannelLine] = []
    restock: dict[str, float] = {}
    divergences: list[StockDivergence] = []
    restock_value = 0.0

    for sku in matched:
        history = series.get(sku, [])
        forecast = float(forecast_demand(history, method="auto").forecast) if history else 0.0
        target = forecast * cover_periods
        on_hand_erp = erp_on_hand.get(sku, 0.0)
        on_hand_channel = channel_on_hand.get(sku, 0.0)
        # Contra la POSICION, no contra el estante: lo que el ERP ya compro y todavia no
        # recibio cubre parte del objetivo. `qualified_demand=0.0` es una ausencia
        # declarada -- un canal no expone demanda comprometida -- no un atajo.
        on_order = None if on_order_by_sku is None else on_order_by_sku.get(sku)
        position = net_flow_position(on_hand_erp, on_order or 0.0, 0.0)
        if on_order is not None:
            n_netted += 1
        qty = max(0.0, round(target - position, 1))

        lines.append(
            ChannelLine(
                sku=sku,
                channel_demand_per_period=forecast,
                erp_on_hand=on_hand_erp,
                channel_on_hand=on_hand_channel,
                target=target,
                restock_qty=qty,
                lead_time_days=lead_times.get(sku, 0.0),
                on_order=on_order,
                position=position,
                avg_daily_demand=daily_demand.get(sku),
            )
        )
        if qty > 0:
            restock[sku] = qty
            restock_value += qty * _resolved_cost(store, payload["ids_by_sku"][sku])
        if abs(on_hand_channel - on_hand_erp) > DIVERGENCE_TOLERANCE:
            divergences.append(StockDivergence(sku, on_hand_channel, on_hand_erp))

    # Staging is a dry-run: it reads and computes, and mutates nothing until apply.
    changeset = (
        erp.stage_restock(restock, idempotency_key=idempotency_key,
                          reason="replenish to cover channel demand")
        if restock
        else None
    )

    total = sum(restock.values())
    summary = (
        f"Matched {len(matched)} SKU(s) between {payload['channel_source']} and "
        f"{payload['erp_source']}; {len(restock)} below a {cover_periods:.0f}-period cover "
        f"({total:,.0f} units short). {netting_note}."
    )
    return ChannelReplenishmentReport(
        channel_source=str(payload["channel_source"]),
        erp_source=str(payload["erp_source"]),
        lines=tuple(lines),
        restock=restock,
        restock_value=restock_value,
        divergences=tuple(divergences),
        channel_only=tuple(payload["channel_only"]),
        erp_only=tuple(payload["erp_only"]),
        n_matched=len(matched),
        total_restock=total,
        cover_periods=cover_periods,
        truncated=channel.truncated,
        changeset=changeset,
        outcome=_build_outcome(
            len(restock), total, restock_value=restock_value,
            financial_threshold=financial_threshold, n_divergent=len(divergences),
            netted=on_order_by_sku is not None, netting_note=netting_note,
        ),
        summary=summary,
        on_order_by_sku=on_order_by_sku,
        n_netted=n_netted,
        netting_note=netting_note,
        demand_points={sku: tuple(pts) for sku, pts in points.items() if sku in set(matched)},
    )


def verify(report: ChannelReplenishmentReport) -> list[str]:
    """QA gate. The two failures worth blocking on are both silent-success shapes."""
    issues = list(verify_guided(report.outcome))
    if report.n_matched == 0:
        # Not "nothing to do": the catalogues did not intersect at all, which almost always
        # means the two systems do not spell their SKUs the same way. Reporting an empty
        # plan would present a join failure as a healthy inventory position.
        issues.append(
            "no SKU matched between the channel and the ERP: the catalogues do not intersect, "
            "so the SKU codes probably do not agree between the two systems"
        )
    elif not any(ln.channel_demand_per_period > 0 for ln in report.lines):
        issues.append("no channel order history read: every forecast is zero, so no SKU can be short")
    return issues


def write_operational(
    report: ChannelReplenishmentReport, out_dir: str | Path, client: str = "Client"
) -> dict[str, Path]:
    """Two files, because there are two findings: the plan, and where the systems disagree."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    plan_rows = [
        {
            "sku": ln.sku,
            "channel_demand_per_period": round(ln.channel_demand_per_period, 2),
            "erp_on_hand": round(ln.erp_on_hand, 2),
            "channel_on_hand": round(ln.channel_on_hand, 2),
            "target": round(ln.target, 2),
            "restock_qty": round(ln.restock_qty, 2),
            "lead_time_days": round(ln.lead_time_days, 1),
            # En blanco (no 0) cuando no se pudo leer lo que ya viene en camino: la celda
            # vacia dice "no medido", un cero diria "medido, nada en camino".
            "on_order": "" if ln.on_order is None else round(ln.on_order, 2),
            "position": "" if ln.position is None else round(ln.position, 2),
        }
        for ln in report.lines
    ]
    out = {"csv": write_summary_csv(plan_rows, d / "channel_replenishment.csv")}
    if report.divergences:
        out["divergence_csv"] = write_summary_csv(
            [
                {
                    "sku": dv.sku,
                    "channel_on_hand": round(dv.channel_on_hand, 2),
                    "erp_on_hand": round(dv.erp_on_hand, 2),
                    "delta_channel_minus_erp": round(dv.delta, 2),
                }
                for dv in report.divergences
            ],
            d / "channel_erp_stock_divergence.csv",
        )
    return out


def build_deck(
    report: ChannelReplenishmentReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float,
) -> Deliverable:
    """Compose the study. ``confidence`` is required for the same reason as in
    ``jobs/shopify_job.py``: the ratchet in ``tests/test_qa_confidence_from_coverage.py``
    only allows the literal count to go down, and the caller always has the real figure."""
    findings: list[Finding] = []
    banner = escalation_banner(report.outcome)
    if banner:
        findings.append(Finding("Requires finance sign-off before acting", banner,
                                impact="do not apply until the named approver signs off"))
    findings.append(
        Finding(
            f"{len(report.restock)} SKU(s) below target cover on real channel demand",
            f"{report.total_restock:,.0f} units short of a {report.cover_periods:.0f}-period cover "
            f"across {report.n_matched} SKU(s) matched between {report.channel_source} and "
            f"{report.erp_source}.",
            impact="replenish before the thin SKUs stock out",
        )
    )
    if report.on_order_by_sku is None:
        # Antes de las cantidades que califica: sin esto, cada cifra de abajo puede estar
        # recomprando una OC que el ERP ya emitio.
        findings.append(
            Finding(
                "Quantities do NOT net stock already on order",
                report.netting_note or "open purchase orders could not be read from the ERP",
                impact="subtract the ERP's open POs by hand before applying any reorder point",
            )
        )
    elif report.netting_note:
        findings.append(
            Finding(
                "Quantities are net of stock already on order",
                report.netting_note,
                impact="the ERP's open POs are already discounted; do not subtract them twice",
            )
        )
    if report.divergences:
        worst = max(report.divergences, key=lambda d: abs(d.delta))
        findings.append(
            Finding(
                f"{len(report.divergences)} SKU(s) where the channel and the ERP disagree on stock",
                f"Worst: {worst.sku} - storefront says {worst.channel_on_hand:.0f}, ERP says "
                f"{worst.erp_on_hand:.0f} ({worst.delta:+.0f}). The plan uses the ERP's figure.",
                impact="one of the two systems is wrong; a positive delta is the direction that oversells",
            )
        )
    if report.channel_only:
        findings.append(
            Finding(
                f"{len(report.channel_only)} SKU(s) sold on the channel that the ERP does not carry",
                "These cannot be replenished through the ERP because there is no product record to "
                f"hold a reorder point: {', '.join(report.channel_only[:8])}"
                + ("..." if len(report.channel_only) > 8 else ""),
                impact="either create them in the ERP or accept that nothing replenishes them",
            )
        )
    if report.truncated:
        findings.append(
            Finding(
                "Channel catalogue read hit the page limit",
                "The storefront returned a full page of variants, so it may carry more than were "
                "read. The match, the plan and the divergence list cover this page only.",
                impact="re-read with pagination before treating this as a full-catalogue plan",
            )
        )

    kpis = (
        Kpi("SKUs matched", str(report.n_matched),
            rationale="Present in both the channel and the ERP"),
        Kpi("SKUs to replenish", str(len(report.restock)), target="0", rationale="Below target cover"),
        Kpi("Units short", f"{report.total_restock:,.0f}", target="0", rationale="Total restock to target"),
        Kpi("Stock divergences", str(len(report.divergences)), target="0",
            rationale="Channel and ERP disagree by more than one unit"),
    )
    data_sources = (
        DataSource("Demand", f"{report.channel_source} (orders)", "live"),
        DataSource("Stock / lead times", f"{report.erp_source} (stock.quant, supplierinfo)", "live"),
        DataSource(
            "Stock already on order",
            report.netting_note or "ERP open purchase orders",
            "live" if report.on_order_by_sku is not None else "absent",
        ),
        DataSource("Book cost", "ERP, by declared ownership in src/entities.py", "live"),
        DataSource("Reorder-point write-back", "ERP stock.warehouse.orderpoint", "on apply"),
    )
    recommendations = [
        "Apply the reorder points so the ERP replenishes automatically against real channel demand.",
        "Resolve the stock divergences before trusting the cover figures on those SKUs.",
    ]
    return Deliverable(
        title="Channel to ERP Replenishment",
        client=client,
        summary=report.summary,
        findings=tuple(findings),
        kpis=kpis,
        data_sources=data_sources,
        recommendations=tuple(recommendations),
        citations=tuple(citations),
        confidence=confidence,
        residual=(f"{banner} " if banner else "")
                 + "Applying the reorder points writes to the ERP through the safe-staging plane "
                   "(dry-run, reversible, idempotent); a human approves before anything is "
                   "committed. Nothing is written to the storefront.",
        prepared=prepared,
    )
