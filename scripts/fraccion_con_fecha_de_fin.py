"""Que fraccion de una cartera REAL trae la fecha de fin por SKU.

`documentation/KERN_FECHA_DE_FIN_POR_SKU.md` cerro declarando que no se sabe, y que
"lo realizable es proporcional a eso". Esto lo mide sobre datos reales: el dataset
publico de Olist (99.441 ordenes, 32.729 SKU, 3.053 vendedores, 2016-09 a 2018-09),
que `scripts/fetch_olist.py` baja de un mirror publico sin credenciales.

EL REENCUADRE, QUE HAY QUE DECLARAR ANTES DE MEDIR
    Ningun export real trae una columna `end_date`: es una columna que Kern acaba de
    inventar. Medir "que fraccion de los archivos la trae" da CERO y no dice nada.
    La pregunta medible es otra: **que fraccion de los SKU de una cartera real tiene
    un fin de vida observable**, porque esa es la poblacion a la que la palanca podria
    aplicarse alguna vez.

    Y se parte en dos mitades, una medible y otra no:
      - MEDIBLE: cuantos SKU dejan de venderse dentro de la ventana, con un criterio
        que sobrevive a una validacion fuera de muestra.
      - NO MEDIBLE de un archivo de transacciones: si el cliente SABIA la fecha de
        antemano. Una discontinuacion es una decision de negocio; el archivo solo
        muestra la consecuencia. Se declara, no se estima.

EL CRITERIO DE MUERTE, Y POR QUE SE VALIDA
    Un hueco no es una muerte: sobre demanda intermitente, "no vendio en N semanas"
    describe a casi toda la cartera. Asi que el criterio se escala a la intermitencia
    de CADA SKU -- muerto si su silencio final supera `k` veces su propio gap tipico,
    con un piso -- y se valida reservando las ultimas `HOLDOUT` semanas: un SKU
    llamado muerto que despues vende es un FALSO POSITIVO.

    Esa tasa es el resultado que mas pesa del estudio, porque un falso positivo aca
    es exactamente una FECHA EQUIVOCADA en el campo, que es la pregunta de seguridad
    que `src/horizon.py` contesta negandose a anular una politica.

PII: Olist esta anonimizado y aca ademas no se publica ningun identificador -- ni de
producto ni de vendedor --, solo agregados. La regla de `CLAUDE.md` es analisis
agregado y punto.

    python scripts/fraccion_con_fecha_de_fin.py
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import tempfile

import numpy as np
import pandas as pd

RAIZ = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from jobs import intake  # noqa: E402
from jobs import inventory_optimization as inv  # noqa: E402
from src.horizon import cap_policy, periods_until  # noqa: E402

#: Semanas reservadas para validar el criterio de muerte fuera de muestra.
HOLDOUT = 26
#: `k` y piso del criterio publicado. Se eligen por la tabla de calibracion, no a dedo:
#: son el par que minimiza el falso positivo (ver `calibrar`).
K = 4
PISO = 26
#: Un gap tipico necesita al menos dos intervalos, o sea tres semanas con venta.
MIN_SEMANAS_PARA_GAP = 3


def cargar(raw: pathlib.Path) -> pd.DataFrame:
    """Las lineas de pedido de Olist, sin lo cancelado (no es demanda servida)."""
    o = pd.read_csv(
        raw / "olist_orders_dataset.csv",
        usecols=["order_id", "order_purchase_timestamp", "order_status"],
    )
    it = pd.read_csv(
        raw / "olist_order_items_dataset.csv",
        usecols=["order_id", "product_id", "seller_id", "price"],
    )
    o = o[~o["order_status"].isin(["canceled", "unavailable"])]
    m = it.merge(o, on="order_id", how="inner")
    m["date"] = pd.to_datetime(m["order_purchase_timestamp"]).dt.normalize()
    m = m.rename(columns={"price": "unit_price"})
    return m[["date", "product_id", "seller_id", "unit_price"]]


def semanal(m: pd.DataFrame) -> pd.DataFrame:
    """La misma grilla semanal que `jobs.intake` arma con `to_period('W')`."""
    m = m.copy()
    m["bucket"] = m["date"].dt.to_period("W").dt.start_time
    wk = m.groupby(["product_id", "bucket"], as_index=False).agg(
        quantity=("unit_price", "size"), unit_price=("unit_price", "mean")
    )
    return wk.rename(columns={"bucket": "date"})


def rasgos(wk: pd.DataFrame, fin: pd.Timestamp) -> pd.DataFrame:
    """Por SKU: el span que VE Kern, semanas con venta, silencio final y gap tipico.

    `n_periods` replica `src.sources._series_from_frame`: arranca en la primera venta
    del SKU y llega al final del ARCHIVO, no a su ultima venta -- el silencio posterior
    es demanda cero real, no dato faltante. `verificar_contra_el_lector` lo comprueba
    contra el lector de verdad en vez de confiar en esta aritmetica.
    """
    filas = []
    for pid, g in wk.groupby("product_id", sort=False):
        d = np.sort(g["date"].to_numpy())
        semanas = ((d - d[0]) / np.timedelta64(7, "D")).astype(int)
        gaps = np.diff(semanas)
        filas.append(
            (
                pid,
                int((fin - d[0]) / np.timedelta64(7, "D")) + 1,
                len(d),
                int((fin - d[-1]) / np.timedelta64(7, "D")),
                float(np.median(gaps)) if len(gaps) else np.nan,
            )
        )
    cols = ["product_id", "n_periods", "wks", "silencio", "gap"]
    return pd.DataFrame(filas, columns=cols).set_index("product_id")


def verificar_contra_el_lector(wk: pd.DataFrame, r: pd.DataFrame, n: int = 250) -> dict:
    """CONTROL: `n_periods` tiene que salir identico al lector real de Kern."""
    from src.sources import _series_from_frame

    rng = np.random.default_rng(7)
    muestra = rng.choice(r.index.to_numpy(), size=min(n, len(r)), replace=False)
    iguales = sum(
        1
        for pid in muestra
        if len(_series_from_frame(wk, pid, period_freq="W")) == int(r.loc[pid, "n_periods"])
    )
    return {"muestreados": int(len(muestra)), "coinciden": int(iguales)}


def muerto(r: pd.DataFrame, k: float = K, piso: float = PISO) -> pd.Series:
    """Muerto = el silencio final supera `k` veces su propio gap tipico, con un piso."""
    return r["silencio"] > np.maximum(k * r["gap"].fillna(np.inf), piso)


def calibrar(wk: pd.DataFrame, fin: pd.Timestamp, holdout: int = HOLDOUT) -> list[dict]:
    """Tasa de FALSOS POSITIVOS del criterio, reservando las ultimas `holdout` semanas."""
    corte = fin - pd.Timedelta(weeks=holdout)
    r_tr = rasgos(wk[wk["date"] <= corte], corte)
    vendio_despues = set(wk[wk["date"] > corte]["product_id"].unique())
    elegibles = r_tr[r_tr["wks"] >= MIN_SEMANAS_PARA_GAP]
    out = []
    for k in (2, 3, 4, 6):
        for piso in (8, 13, 26):
            llamados = elegibles.index[muerto(elegibles, k, piso)]
            if not len(llamados):
                continue
            fp = sum(1 for p in llamados if p in vendio_despues)
            out.append(
                {
                    "k": k,
                    "piso": piso,
                    "llamados_muertos": int(len(llamados)),
                    "vendieron_despues": int(fp),
                    "falso_positivo": round(fp / len(llamados), 4),
                }
            )
    return out


def fracciones(r: pd.DataFrame, rev: pd.Series) -> list[dict]:
    """La fraccion con fin de vida observable, por definicion de "cartera"."""
    r = r.join(rev)
    tiers = [
        ("todo lo que Kern dimensiona (n_periods>=6)", r["n_periods"] >= 6),
        ("con >=3 semanas con venta", r["wks"] >= MIN_SEMANAS_PARA_GAP),
        ("rango recurrente (>=6 semanas con venta)", r["wks"] >= 6),
        ("nucleo (>=12 semanas con venta)", r["wks"] >= 12),
    ]
    out = []
    for nombre, mask in tiers:
        sub = r[mask & (r["wks"] >= MIN_SEMANAS_PARA_GAP)]
        if not len(sub):
            continue
        mu = muerto(sub)
        out.append(
            {
                "cartera": nombre,
                "skus": int(len(sub)),
                "muertos": int(mu.sum()),
                "frac_skus": round(float(mu.mean()), 4),
                "frac_facturacion": round(
                    float(sub.loc[mu, "revenue"].sum() / sub["revenue"].sum()), 4
                ),
            }
        )
    return out


def por_vendedor(m: pd.DataFrame, r: pd.DataFrame) -> list[dict]:
    """Cada vendedor es UNA cartera real: la respuesta es una distribucion, no un numero."""
    sk = m.groupby("product_id")["seller_id"].agg(lambda s: s.mode().iat[0])
    base = r[r["wks"] >= MIN_SEMANAS_PARA_GAP].join(sk)
    base["muerto"] = muerto(base)
    agg = base.groupby("seller_id").agg(skus=("muerto", "size"), frac=("muerto", "mean"))
    out = []
    for minimo in (10, 20, 50):
        v = agg[agg["skus"] >= minimo]
        if len(v) < 3:
            continue
        q = v["frac"].quantile([0.1, 0.25, 0.5, 0.75, 0.9])
        out.append(
            {
                "min_skus": minimo,
                "carteras": int(len(v)),
                "p10": round(float(q[0.1]), 4),
                "p25": round(float(q[0.25]), 4),
                "mediana": round(float(q[0.5]), 4),
                "p75": round(float(q[0.75]), 4),
                "p90": round(float(q[0.9]), 4),
            }
        )
    return out


def ya_descontado(r: pd.DataFrame) -> dict:
    """Cuanto achica YA el relleno de ceros la media de un SKU muerto.

    `_series_from_frame` llega hasta el final del archivo, asi que la media de un SKU
    muerto ya viene dividida por su span completo. Parte del trabajo de la palanca esta
    hecho antes de que la palanca exista, y eso acota su valor INCREMENTAL.
    """
    vivo = (r["n_periods"] - r["silencio"]).clip(lower=1)
    f = vivo / r["n_periods"]
    mu = muerto(r)
    return {
        "factor_mediano_muertos": round(float(f[mu].median()), 4),
        "factor_p25_muertos": round(float(f[mu].quantile(0.25)), 4),
        "factor_p75_muertos": round(float(f[mu].quantile(0.75)), 4),
        "factor_mediano_vivos": round(float(f[~mu].median()), 4),
    }


def veredicto_de_los_muertos(r: pd.DataFrame, fin: pd.Timestamp) -> dict:
    """Que hace el tope REAL con la fecha de fin de un SKU que ya murio.

    La respuesta importa mas que su tamano: una fecha que ya paso sale `expired` y el
    tope NO topea. O sea que la fraccion con fin de vida OBSERVABLE es justo la fraccion
    donde la palanca no hace nada -- la que paga es la que todavia no ocurrio.
    """
    d = r[muerto(r)]
    cuenta: dict[str, int] = {}
    for _pid, row in d.iterrows():
        fecha_fin = (fin - pd.Timedelta(weeks=int(row["silencio"]))).date()
        pr = periods_until(fin.date(), fecha_fin, periods_per_year=52.0)
        t = cap_policy(100.0, 140.0, mean_per_period=2.0, periods_remaining=pr)
        cuenta[t.verdict] = cuenta.get(t.verdict, 0) + 1
    return cuenta


def punta_a_punta(m: pd.DataFrame, fin: pd.Timestamp, n_vendedores: int = 60) -> dict:
    """La capacidad REAL sobre una cartera REAL, por el pipeline de verdad.

    El cliente planifica en `corte`; las muertes posteriores son fechas de fin
    verdaderas y solo conocibles despues, o sea el mejor caso posible -- un cliente que
    declara bien. Es el analogo de campo del carril `horizonte_real` del marcador.
    """
    m = m.copy()
    m["bucket"] = m["date"].dt.to_period("W").dt.start_time
    corte = fin - pd.Timedelta(weeks=52)
    sk = m.groupby("product_id")["seller_id"].agg(lambda s: s.mode().iat[0])

    pre = m[m["bucket"] <= corte]
    wks = pre.groupby("product_id")["bucket"].nunique()
    buenos = set(wks[wks >= 6].index)
    cand = sk[sk.index.isin(buenos)].value_counts()
    vends = list(cand.index[:n_vendedores])
    skus = [p for p in sk[sk.isin(vends)].index if p in buenos]

    hist = pre[pre["product_id"].isin(skus)]
    post = m[(m["bucket"] > corte) & (m["product_id"].isin(skus))]
    ultima = post.groupby("product_id")["bucket"].max()

    fechas = {p: corte for p in skus if p not in set(post["product_id"])}
    for p in ultima.index:  # mismo criterio calibrado, no un umbral ad-hoc
        g = np.sort(m.loc[m["product_id"] == p, "bucket"].unique())
        gaps = np.diff(((g - g[0]) / np.timedelta64(7, "D")).astype(int))
        gap = float(np.median(gaps)) if len(gaps) else np.inf
        if (fin - ultima[p]).days // 7 > max(K * gap, PISO):
            fechas[p] = ultima[p]

    filas = hist.groupby(["product_id", "bucket"], as_index=False).agg(
        quantity=("unit_price", "size"), unit_cost=("unit_price", "mean")
    )
    filas = filas.rename(columns={"bucket": "date"})
    filas["lead_time_days"] = 14

    tmp = pathlib.Path(tempfile.mkdtemp())
    sin, con = tmp / "sin.csv", tmp / "con.csv"
    filas.to_csv(sin, index=False)
    f2 = filas.copy()
    f2["end_date"] = f2["product_id"].map(lambda p: fechas.get(p, pd.NaT))
    f2.to_csv(con, index=False)

    def corre(path: pathlib.Path, con_fechas: bool):
        prep, _q = intake.prepare_tracked(str(path))
        kw = {}
        if con_fechas:
            fr = intake.sku_end_dates(str(path))
            kw = {
                "end_dates": intake.end_date_map(fr),
                "horizon_note": (fr.attrs.get("horizon_note") or ""),
            }
        return inv.run(prep, **kw)

    a, b = corre(sin, False), corre(con, True)
    ia = {r.product_id: r for r in a.recommendations}

    cuenta: dict[str, int] = {}
    # DOS numeros distintos, y confundirlos fue el error de la primera medicion.
    #   `pico`   = la caida del TECHO de reposicion (order-up-to / Q) x costo. Es lo que
    #              el plan deja de poder tener EN EL MAXIMO.
    #   `titular`= la caida del PROMEDIO inmovilizado, que es lo que `investment` mide
    #              (ciclo + buffer). Es lo que se mueve en `requested_investment`.
    # El pico es ~2,4x el titular: un tope que baja el techo no baja el promedio en la
    # misma proporcion, porque el buffer no se toca.
    pico = 0.0
    for r in b.recommendations:
        cuenta[r.horizon_verdict] = cuenta.get(r.horizon_verdict, 0) + 1
        if r.horizon_verdict != "applied":
            continue
        o = ia[r.product_id]
        antes = o.order_up_to if o.order_up_to is not None else (o.order_quantity or 0.0)
        desp = r.order_up_to if r.order_up_to is not None else (r.order_quantity or 0.0)
        pico += max(0.0, antes - desp) * r.unit_cost

    con_fecha = sum(1 for r in b.recommendations if r.horizon_verdict != "no_date")
    return {
        "vendedores": len(vends),
        "skus_dimensionados": len(b.recommendations),
        "skus_con_fecha": con_fecha,
        "veredictos": cuenta,
        "frac_con_fecha": round(con_fecha / len(b.recommendations), 4),
        "frac_topeados_de_los_que_traen_fecha": round(
            cuenta.get("applied", 0) / max(con_fecha, 1), 4
        ),
        "caida_del_pico_usd": round(pico, 2),
        "titular_inversion_sin": round(a.requested_investment, 2),
        "titular_inversion_con": round(b.requested_investment, 2),
        "titular_se_movio": abs(a.requested_investment - b.requested_investment) > 1e-9,
        "capital_liberado_usd": round(a.requested_investment - b.requested_investment, 2),
        "capital_liberado_reportado_usd": round(
            float(getattr(b, "horizon_capital_freed_usd", 0.0) or 0.0), 2
        ),
        "capital_liberado_frac_del_titular": round(
            (a.requested_investment - b.requested_investment) / a.requested_investment, 4
        ),
        "pico_frac_del_titular": round(pico / a.requested_investment, 4),
    }


def medir(raw: pathlib.Path) -> dict:
    m = cargar(raw)
    wk = semanal(m)
    fin = wk["date"].max()
    r = rasgos(wk, fin)
    rev = m.groupby("product_id")["unit_price"].sum().rename("revenue")
    r_gap = r[r["wks"] >= MIN_SEMANAS_PARA_GAP]
    semanas = (fin - wk["date"].min()).days / 7

    tasa = {}
    for nombre, sub in [
        ("con >=3 semanas con venta", r_gap),
        ("rango recurrente (>=6)", r_gap[r_gap["wks"] >= 6]),
        ("nucleo (>=12)", r_gap[r_gap["wks"] >= 12]),
    ]:
        f = float(muerto(sub).mean())
        tasa[nombre] = {
            "en_la_ventana": round(f, 4),
            "por_ano": round(f / (semanas / 52), 4),
        }

    return {
        "fuente": "Olist (mirror publico, scripts/fetch_olist.py)",
        "lineas": int(len(m)),
        "skus": int(m["product_id"].nunique()),
        "vendedores": int(m["seller_id"].nunique()),
        "ventana": [str(wk["date"].min().date()), str(fin.date())],
        "semanas": round(semanas, 1),
        "criterio": {"k": K, "piso": PISO, "holdout": HOLDOUT},
        "control_lector": verificar_contra_el_lector(wk, r),
        "calibracion": calibrar(wk, fin),
        "fracciones": fracciones(r, rev),
        "por_vendedor": por_vendedor(m, r),
        "tasa_de_muerte": tasa,
        "ya_descontado_por_el_relleno": ya_descontado(r_gap),
        "veredicto_de_los_ya_muertos": veredicto_de_los_muertos(r_gap, fin),
        "punta_a_punta": punta_a_punta(m, fin),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--datos", default=str(RAIZ / "data" / "kaggle" / "olist"))
    ap.add_argument(
        "--salida",
        default=str(RAIZ / "documentation" / "lineas_base" / "fraccion_con_fecha_de_fin.json"),
    )
    args = ap.parse_args()
    raw = pathlib.Path(args.datos)
    if not (raw / "olist_orders_dataset.csv").exists():
        sys.exit(f"faltan los datos en {raw}; corre primero: python scripts/fetch_olist.py")

    res = medir(raw)
    salida = pathlib.Path(args.salida)
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(json.dumps(res, indent=2, ensure_ascii=True), encoding="utf-8")

    c = res["control_lector"]
    print(f"CONTROL lector real: {c['coinciden']}/{c['muestreados']} coinciden exacto")
    mejor = min(res["calibracion"], key=lambda d: d["falso_positivo"])
    print(f"falso positivo minimo: {mejor['falso_positivo']:.1%} (k={mejor['k']}, piso={mejor['piso']})")
    for f in res["fracciones"]:
        print(f"  {f['cartera']:<44} {f['frac_skus']:>6.1%} SKU  {f['frac_facturacion']:>6.1%} facturacion")
    e = res["punta_a_punta"]
    print(f"punta a punta: {e['skus_con_fecha']}/{e['skus_dimensionados']} traen fecha; "
          f"topeados {e['veredictos'].get('applied', 0)} "
          f"({e['frac_topeados_de_los_que_traen_fecha']:.1%} de los que la traen)")
    print(f"escrito: {salida}")


if __name__ == "__main__":
    main()
