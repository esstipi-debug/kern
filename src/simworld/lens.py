"""La lente: el archivo que Kern realmente lee, con suciedad graduable.

Nunca toca el mundo. El marcador se calcula siempre contra la verdad limpia,
aunque Kern haya visto basura: si Kern pidio mal porque el exporte tenia la SKU
duplicada, el quiebre se cuenta igual contra el.

Nivel 0 es el CSV canonico que ``jobs/intake.py`` espera. Los niveles 1-3
acumulan: 3 incluye 2 incluye 1.
"""

from __future__ import annotations

from pathlib import Path

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


def apply_dirt(df: pd.DataFrame, *, level: int, seed: int = 0) -> pd.DataFrame:
    """Nivel 0 devuelve el frame tal cual. Los inyectores llegan en las Tasks 4-5."""
    if level not in LEVELS:
        raise ValueError(f"nivel de suciedad desconocido: {level}; conocidos: {LEVELS}")
    return df.copy()
