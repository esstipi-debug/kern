"""Decision-support API — thin FastAPI layer over ``src.decision_support``.

Each route takes a small JSON body (form-first, few fields), calls the matching pure
function, and returns a serialized ``DecisionCard`` (verdict + guardrails + ranked
options). Domain/range errors from the engine surface as 422 with a Spanish message;
"not feasible" cases are not errors — the engine returns a red verdict.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src import decision_support as ds

router = APIRouter(prefix="/api/decide", tags=["decisiones"])


def _card_or_422(fn, **kwargs) -> dict:
    """Run a decision function; turn engine ValueErrors into a 422 with its Spanish message."""
    try:
        return fn(**kwargs).to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


class CapacidadIn(BaseModel):
    entrada_unidades_dia: float = Field(..., ge=0)
    salida_unidades_dia: float = Field(..., ge=0)
    num_operarios: int = Field(..., ge=1)
    unidades_hora_operario: float = Field(..., gt=0)
    horas_turno: float = Field(..., gt=0)


@router.post("/capacidad")
def decide_capacidad(body: CapacidadIn) -> dict:
    return _card_or_422(ds.viabilidad_operativa, **body.model_dump())


class NegociacionIn(BaseModel):
    costo_unitario: float = Field(..., gt=0)
    precio_venta: float = Field(..., gt=0)
    margen_minimo_pct: float = Field(..., gt=0, lt=1)
    lead_time_actual_dias: float = Field(..., gt=0)
    demanda_std_dia: float = Field(0.0, ge=0)
    nivel_servicio: float = Field(0.95, gt=0, lt=1)


@router.post("/negociacion")
def decide_negociacion(body: NegociacionIn) -> dict:
    return _card_or_422(ds.negociacion_proveedor, **body.model_dump())


class ProveedorIn(BaseModel):
    actual_precio: float = Field(..., gt=0)
    actual_otif: float = Field(..., ge=0, le=1)
    actual_lead: float = Field(..., gt=0)
    actual_defectos: float = Field(..., ge=0, le=1)
    alt_precio: float = Field(..., gt=0)
    alt_otif: float = Field(..., ge=0, le=1)
    alt_lead: float = Field(..., gt=0)
    alt_defectos: float = Field(..., ge=0, le=1)


@router.post("/proveedor")
def decide_proveedor(body: ProveedorIn) -> dict:
    return _card_or_422(ds.cambiar_proveedor, **body.model_dump())


class InventarioIn(BaseModel):
    demanda_media_periodo: float = Field(..., gt=0)
    demanda_std_periodo: float = Field(..., ge=0)
    lead_time_periodos: float = Field(..., gt=0)
    nivel_servicio: float = Field(..., gt=0, lt=1)
    costo_unitario: float = Field(..., gt=0)
    costo_ordenar: float = Field(..., gt=0)
    holding_rate_anual: float = Field(..., gt=0, le=5)
    periodos_por_anio: float = Field(52.0, gt=0)
    stock_actual: float | None = Field(None, ge=0)


@router.post("/inventario")
def decide_inventario(body: InventarioIn) -> dict:
    return _card_or_422(ds.niveles_inventario, **body.model_dump())


class CompraIn(BaseModel):
    monto_compra: float = Field(..., ge=0)
    inventario_actual_valor: float = Field(..., ge=0)
    cogs_anual: float = Field(..., gt=0)
    ventas_anual: float = Field(..., gt=0)
    dio_objetivo_max: float = Field(90.0, gt=0)
    dso_dias: float = Field(45.0, ge=0)
    dpo_dias: float = Field(30.0, ge=0)


@router.post("/compra")
def decide_compra(body: CompraIn) -> dict:
    return _card_or_422(ds.aprobar_compra, **body.model_dump())


class LiquidarIn(BaseModel):
    unidades_excedente: float = Field(..., gt=0)
    costo_unitario: float = Field(..., gt=0)
    precio_actual: float = Field(..., gt=0)
    valor_recuperacion_pct: float = Field(0.4, gt=0, le=1)


@router.post("/liquidar")
def decide_liquidar(body: LiquidarIn) -> dict:
    return _card_or_422(ds.liquidar_stock, **body.model_dump())
