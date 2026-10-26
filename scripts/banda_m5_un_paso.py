"""Banda medida vs normal a UN paso, origen rodante (12 origenes), sobre M5 real."""
import json
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
from jobs import forecast_job as fj  # noqa: E402
from src.forecastability import segment  # noqa: E402
from src.forecasting import forecast_demand  # noqa: E402
from src.quantile_forecasting import band_score  # noqa: E402

M5 = r"C:\Users\Gamer\Music\scm\kern\data\kaggle\m5\m5\datasets\sales_train_evaluation.csv"
N, SEMANAS, SEMILLA, ORIGENES = int(sys.argv[1]), 104, 20261026, 12

df = pd.read_csv(M5)
dias = [c for c in df.columns if c.startswith("d_")]
muestra = df.sample(n=N, random_state=SEMILLA)
series = {}
for _, fila in muestra.iterrows():
    diario = fila[dias].to_numpy(dtype=float)
    diario = diario[len(diario) % 7:]
    series[fila["id"]] = [float(v) for v in diario.reshape(-1, 7).sum(axis=1)[-SEMANAS:]]

seg = segment(series)
res = {}
for f in seg.items:
    y = series[f.name]
    for t in range(len(y) - ORIGENES, len(y)):
        fc = forecast_demand(y[:t], method=f.recommended_method)
        banda = fj._band(y[:t], fc)
        normal = fj._normal_band(fc)
        if banda is None or normal is None or banda.method == "normal":
            continue
        d = res.setdefault(banda.method, {"pin": [], "pin_n": [], "cob": [], "cob_n": [], "sku": {}})
        p, c = band_score([y[t]], banda)
        pn, cn = band_score([y[t]], normal)
        d["pin"].append(p); d["pin_n"].append(pn); d["cob"].append(c); d["cob_n"].append(cn)
        acc = d["sku"].setdefault(f.name, [0.0, 0.0])
        acc[0] += p; acc[1] += pn

out = {}
for m, d in res.items():
    gana = sum(1 for a, b in d["sku"].values() if a < b)
    out[m] = {
        "skus": len(d["sku"]), "gana_a_la_normal": gana,
        "cobertura_banda": round(float(np.mean(d["cob"])), 3),
        "cobertura_normal": round(float(np.mean(d["cob_n"])), 3),
        "pinball_total_banda_sobre_normal": round(float(np.sum(d["pin"]) / np.sum(d["pin_n"])), 4),
    }
print(json.dumps({"n": N, "origenes": ORIGENES, "semilla": SEMILLA, "por_metodo": out}, indent=1))
