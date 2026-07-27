"""La lente: el archivo que Kern realmente lee, con suciedad graduable.

Nunca toca el mundo. El marcador se calcula siempre contra la verdad limpia,
aunque Kern haya visto basura: si Kern pidio mal porque el exporte tenia la SKU
duplicada, el quiebre se cuenta igual contra el.

Nivel 0 es el CSV canonico que ``jobs/intake.py`` espera. Los niveles 1-3
acumulan: 3 incluye 2 incluye 1.
"""

from __future__ import annotations

import re
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


def _alt_spelling(sku_id: str) -> str:
    """Segunda grafia reconocible pero distinta del id original, derivada del
    id mismo -- no de un prefijo fijo tipo ``"SKU-"`` (eso acopla el inyector
    en silencio a la convencion de nombres de ``world.py``: con cualquier
    otro catalogo, ``victim.replace("SKU-", "Sku ")`` no encuentra nada que
    reemplazar y devuelve el id intacto, reduciendo el nivel 2 a cuatro
    inyectores sin ninguna senal de que algo fallo).

    Cualquier separador de catalogo (``-``, ``_``, ``.``) se cambia por un
    espacio, imitando el mismo tipo de error humano que ``"SKU-1005"`` ->
    ``"Sku 1005"``. Si no hay separador, se usa el borde letras/digitos
    (``"P1000"`` -> ``"P 1000"``). Si tampoco hay eso, se inserta un espacio
    interno. En todos los casos el caracter insertado es interno (nunca al
    inicio o al final), a proposito: un espacio SOLO al borde desaparece bajo
    ``strip()``, y la propiedad que el resto del harness necesita es que la
    segunda grafia NO colapse con la original bajo ``strip().upper()``.
    """
    for sep in ("-", "_", "."):
        if sep in sku_id:
            prefix, _, rest = sku_id.partition(sep)
            return f"{prefix.capitalize()} {rest}"
    match = re.match(r"^([A-Za-z]+)(\d.*)$", sku_id)
    if match:
        letters, rest = match.groups()
        return f"{letters.capitalize()} {rest}"
    if len(sku_id) < 2:
        return f"{sku_id}'"
    mid = max(1, len(sku_id) // 2)
    return f"{sku_id[:mid]} {sku_id[mid:]}"


def inject_split_sku(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """La misma SKU cargada con dos grafias: la demanda se parte en dos series."""
    out = df.copy()
    ids = sorted(out["product_id"].astype(str).str.strip().unique())
    if not ids:
        return out
    victim = ids[int(rng.integers(0, len(ids)))]
    is_victim = out["product_id"].astype(str).str.strip() == victim
    flip = is_victim & (rng.random(len(out)) < 0.5)
    out.loc[flip, "product_id"] = _alt_spelling(victim)
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
    """Decision-critico: se pierde el lead time prometido para algunos VALORES
    de lead time declarados (no filas sueltas al azar -- el dato falta para
    todo el grupo de filas que comparte ese valor, como en la vida real).

    Nota: agrupa por valor de ``lead_time_days``, no por proveedor -- si dos
    proveedores distintos prometen el mismo lead time, sus filas se blanquean
    o se salvan siempre juntas (mismo valor = mismo grupo)."""
    out = df.copy()
    numeric_leads = pd.to_numeric(out["lead_time_days"], errors="coerce")
    leads = sorted(numeric_leads.dropna().unique())
    if not leads:
        return out
    blanked = {v for v in leads if rng.random() < 0.34}
    mask = numeric_leads.isin(blanked)
    if not mask.any():
        mask = numeric_leads == leads[0]
    out.loc[mask, "lead_time_days"] = np.nan
    return out


def inject_calendar_gaps(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Semanas sin filas: cero demanda o no se cargo, el archivo no lo dice."""
    out = df.copy()
    dates = sorted(out["date"].astype(str).unique())
    if len(dates) < 4:
        return out
    drop = {d for d in dates if rng.random() < 0.12}
    if not drop:
        drop = {dates[0]}
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
    # Un solo `rng` compartido entre todas las llamadas de este `apply_dirt`
    # (plan-mandado, no cambiar): a nivel 2 los inyectores semanticos avanzan
    # el generador antes de que corra la capa cosmetica, asi que el nivel 2
    # NO reproduce byte-a-byte el ruido cosmetico del nivel 1 con la misma
    # semilla -- lleva danio de la MISMA naturaleza (mismos inyectores, mismas
    # probabilidades) pero con OTRO draw aleatorio. Una tarea futura no puede
    # descomponer un delta nivel-2 como "nivel 1 mas un incremento" celda a
    # celda; solo puede comparar contra el nivel-0 limpio.
    rng = np.random.default_rng(seed)
    out = df.copy()
    for lvl in range(COSMETIC_LEVEL + 1, level + 1):
        for injector in DIRT_BY_LEVEL[lvl]:
            out = injector(out, rng)
    if level >= COSMETIC_LEVEL:
        for injector in DIRT_BY_LEVEL[COSMETIC_LEVEL]:
            out = injector(out, rng)
    return out
