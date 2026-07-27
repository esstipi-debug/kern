"""La lente: el archivo que Kern realmente lee, con suciedad graduable.

Nunca toca el mundo. El marcador se calcula siempre contra la verdad limpia,
aunque Kern haya visto basura: si Kern pidio mal porque el exporte tenia la SKU
duplicada, el quiebre se cuenta igual contra el.

Nivel 0 es el CSV canonico que ``jobs/intake.py`` espera. Los niveles 1-3
acumulan: 3 incluye 2 incluye 1.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from src.simworld.world import WeekOutcome, World

LEVELS = (0, 1, 2, 3)

# Lunes de una semana cualquiera; el mundo es relativo, la fecha absoluta no importa
# mientras sea consistente y parseable.
EPOCH = pd.Timestamp("2025-01-06")


def history_frame(world: World, outcomes: list[WeekOutcome]) -> pd.DataFrame:
    """El historico limpio: una fila por SKU y semana, con lo efectivamente vendido."""
    rows = []
    for out in outcomes:
        date = EPOCH + pd.Timedelta(weeks=out.week)
        for i, sku in enumerate(world.skus):
            sup = world.suppliers[sku.supplier_idx]
            rows.append(
                {
                    "date": date.strftime("%Y-%m-%d"),
                    "product_id": sku.sku,
                    "quantity": float(out.sold[i]),
                    "unit_cost": sku.unit_cost,
                    "lead_time_days": float(sup.promised_lead_weeks * 7),
                    # 0.0 = ninguna dispersion declarada: la promesa del proveedor
                    # es un numero fijo, no una distribucion. jobs/intake.py trata
                    # 0.0 exactamente asi ("no dispersion known"), asi que escribirlo
                    # explicito evita que synthesize esta columna entera.
                    "lead_time_std": 0.0,
                }
            )
    return pd.DataFrame(rows)


def export_history(
    world: World,
    outcomes: list[WeekOutcome],
    path: str | Path,
    *,
    level: int = 0,
    seed: int = 0,
) -> Path:
    """Escribe el historico con el nivel de suciedad pedido y devuelve la ruta."""
    if level not in LEVELS:
        raise ValueError(f"nivel de suciedad desconocido: {level}; conocidos: {LEVELS}")
    df = history_frame(world, outcomes)
    df = apply_dirt(df, level=level, seed=seed)
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    return out


CASE_SIZE = 12.0


def inject_whitespace_case(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Cosmetico puro: la SKU sigue siendo la misma, solo mal escrita."""
    out = df.copy()
    mask = rng.random(len(out)) < 0.35
    ids = out["product_id"].astype(str)
    out.loc[mask, "product_id"] = " " + ids[mask].str.lower()
    return out


def inject_mixed_date_formats(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Cosmetico puro: mismo dia calendario, otro formato de texto."""
    out = df.copy()
    mask = rng.random(len(out)) < 0.4
    parsed = pd.to_datetime(out.loc[mask, "date"])
    out.loc[mask, "date"] = parsed.dt.strftime("%d/%m/%Y")
    return out


def inject_thousand_separators(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Cosmetico puro: mismo numero, formato europeo de miles/decimales."""
    out = df.copy()
    mask = rng.random(len(out)) < 0.3
    vals = out.loc[mask, "quantity"].astype(float)
    out["quantity"] = out["quantity"].astype(object)
    out.loc[mask, "quantity"] = [f"{v:,.2f}".replace(".", "#").replace(",", ".").replace("#", ",")
                                 for v in vals]
    return out


def inject_split_sku(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """La misma SKU cargada con dos grafias: la demanda se parte en dos series."""
    out = df.copy()
    ids = sorted(out["product_id"].astype(str).str.strip().unique())
    if not ids:
        return out
    victim = ids[int(rng.integers(0, len(ids)))]
    is_victim = out["product_id"].astype(str).str.strip() == victim
    flip = is_victim & (rng.random(len(out)) < 0.5)
    out.loc[flip, "product_id"] = victim.replace("SKU-", "Sku ")
    return out


def inject_mixed_units(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Algunas filas vienen en cajas y otras en unidades, sin columna que lo diga."""
    out = df.copy()
    mask = rng.random(len(out)) < 0.25
    qty = pd.to_numeric(out["quantity"], errors="coerce")
    out.loc[mask, "quantity"] = (qty[mask] / CASE_SIZE).round(2)
    return out


def inject_returns_as_negatives(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Devoluciones cargadas en la misma columna de ventas, como cantidad negativa."""
    out = df.copy()
    mask = rng.random(len(out)) < 0.06
    qty = pd.to_numeric(out["quantity"], errors="coerce")
    out.loc[mask, "quantity"] = -(qty[mask] * 0.2).round(2)
    return out


def inject_missing_lead_time(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Decision-critico: se pierde el lead time prometido de algunos proveedores
    (no filas sueltas al azar -- el dato falta por proveedor, como en la vida real)."""
    out = df.copy()
    leads = sorted(pd.to_numeric(out["lead_time_days"], errors="coerce").dropna().unique())
    if not leads:
        return out
    blanked = {v for v in leads if rng.random() < 0.34}
    mask = pd.to_numeric(out["lead_time_days"], errors="coerce").isin(blanked)
    if not mask.any() and leads:
        mask = pd.to_numeric(out["lead_time_days"], errors="coerce") == leads[0]
    out.loc[mask, "lead_time_days"] = np.nan
    return out


def inject_calendar_gaps(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Semanas sin filas: cero demanda o no se cargo, el archivo no lo dice."""
    out = df.copy()
    dates = sorted(out["date"].astype(str).unique())
    if len(dates) < 4:
        return out
    drop = {d for d in dates if rng.random() < 0.12}
    return out[~out["date"].astype(str).isin(drop)].reset_index(drop=True)


DIRT_BY_LEVEL: dict[int, tuple] = {
    0: (),
    1: (inject_whitespace_case, inject_mixed_date_formats, inject_thousand_separators),
    2: (inject_split_sku, inject_mixed_units, inject_returns_as_negatives,
        inject_missing_lead_time, inject_calendar_gaps),
    3: (),  # Task 5
}

COSMETIC_LEVEL = 1


def apply_dirt(df: pd.DataFrame, *, level: int, seed: int = 0) -> pd.DataFrame:
    """Aplica la suciedad hasta ``level`` inclusive. Los niveles acumulan.

    Orden deliberado: **primero los defectos que cambian el valor** (niveles 2 y
    3), **despues los cosmeticos** del nivel 1. Es el orden real — el dato es lo
    que es, y el formato del exporte se aplica al final — y ademas evita que un
    inyector semantico tenga que parsear ``"1.234,50"`` y lo coercione a NaN en
    silencio, corrompiendo el fixture en vez del archivo.

    Ningun inyector toca ``lead_time_std``: esa columna es la senal que Task 8
    lee para saber si Kern sintetizo la dispersion del lead time
    (``IntakeQuality.synthesized``), y debe quedar identica en todos los
    niveles para que las deltas de degradacion nivel-N contra nivel-0 sean
    comparables.
    """
    if level not in LEVELS:
        raise ValueError(f"nivel de suciedad desconocido: {level}; conocidos: {LEVELS}")
    rng = np.random.default_rng(seed)
    out = df.copy()
    for lvl in range(COSMETIC_LEVEL + 1, level + 1):
        for injector in DIRT_BY_LEVEL[lvl]:
            out = injector(out, rng)
    if level >= COSMETIC_LEVEL:
        for injector in DIRT_BY_LEVEL[COSMETIC_LEVEL]:
            out = injector(out, rng)
    return out
