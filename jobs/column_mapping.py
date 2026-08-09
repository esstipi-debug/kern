from __future__ import annotations

import re
import unicodedata

import pandas as pd

CANONICAL_COLUMNS = ("date", "product_id", "quantity", "unit_cost", "lead_time_days")
CANONICAL_REQUIRED = ("date", "product_id", "quantity")

VARIANTS: dict[str, list[str]] = {
    "date": ["date", "fecha", "fecha_venta", "fecha_documento", "fecha_factura", "dia", "día", "day", "timestamp", "order_date", "period", "week", "datetime", "data"],
    "product_id": ["product_id", "producto", "product", "sku", "item", "codigo", "código", "codigo_producto", "cod_producto", "articulo", "artículo", "referencia", "clave_producto", "produto", "material", "stockcode"],
    "quantity": ["quantity", "cantidad", "qty", "demanda", "demand", "units", "sales", "volume", "cantidad_vendida", "unidades", "quantidade", "units_sold", "order_qty"],
    "unit_cost": ["unit_cost", "costo", "cost", "precio", "price", "unit_price", "costo_unitario", "precio_unitario", "custo", "preco", "sell_price", "unitcost"],
    "lead_time_days": ["lead_time_days", "lead_time", "leadtime", "lead", "plazo", "plazo_dias", "plazo_entrega", "plazo_de_entrega", "entrega", "dias_entrega", "lt_days", "prazo", "prazo_entrega", "lead_time_std"],
}


def _norm(value: object) -> str:
    folded = unicodedata.normalize("NFKD", str(value).lower()).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]", "", folded.strip())


def map_columns(df: pd.DataFrame, *, threshold: int = 80) -> tuple[dict[str, str], list[str]]:
    by_norm = {_norm(c): c for c in df.columns}
    consumed: set[str] = set()
    mapping: dict[str, str] = {}
    try:
        from rapidfuzz import fuzz, process  # noqa: WPS433

        has_rapidfuzz = True
    except ImportError:
        has_rapidfuzz = False
        fuzz = None  # type: ignore
        process = None  # type: ignore

    for canon in CANONICAL_COLUMNS:
        if canon in mapping:
            continue
        for alias in VARIANTS.get(canon, [canon]):
            hit = by_norm.get(_norm(alias))
            if hit is not None and hit not in consumed:
                mapping[canon] = hit
                consumed.add(hit)
                break
        if canon in mapping:
            continue
        if has_rapidfuzz:
            choices = [c for c in df.columns if c not in consumed]
            if not choices:
                continue
            norm_choices = [_norm(c) for c in choices]
            norm_aliases = [_norm(a) for a in VARIANTS.get(canon, [canon])]
            best_score = -1
            best_col: str | None = None
            for alias_norm in norm_aliases:
                result = process.extractOne(alias_norm, norm_choices, scorer=fuzz.token_sort_ratio)  # type: ignore
                if result is None:
                    continue
                matched_norm, score, idx = result
                if score > best_score:
                    best_score = score
                    best_col = choices[idx]
            if best_col is not None and best_score >= threshold:
                mapping[canon] = best_col
                consumed.add(best_col)

    unmapped = [c for c in CANONICAL_COLUMNS if c not in mapping]
    return mapping, unmapped


def normalize_dataframe(df: pd.DataFrame, mapping: dict[str, str]) -> pd.DataFrame:
    rename = {actual: canon for canon, actual in mapping.items()}
    out = df.rename(columns=rename)
    missing = [c for c in CANONICAL_REQUIRED if c not in out.columns]
    if missing:
        raise ValueError(f"missing required columns: {missing}")
    return out
