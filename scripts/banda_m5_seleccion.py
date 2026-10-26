"""Seleccion de banda por SKU: elegir con origenes 80-91, evaluar con 92-103 (M5 real)."""
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
N, SEMILLA = int(sys.argv[1]), 20261026
df = pd.read_csv(M5)
dias = [c for c in df.columns if c.startswith("d_")]
S = {}
for _, f in df.sample(n=N, random_state=SEMILLA).iterrows():
    d = f[dias].to_numpy(dtype=float)
    d = d[len(d) % 7:]
    S[f["id"]] = [float(v) for v in d.reshape(-1, 7).sum(axis=1)[-104:]]


def puntajes(y, method, origenes):
    med, nor, cob_m, cob_n = 0.0, 0.0, [], []
    for t in origenes:
        fc = forecast_demand(y[:t], method=method)
        b, n = fj._band(y[:t], fc), fj._normal_band(fc)
        if b is None or n is None or b.method == "normal":
            return None
        pb, cb = band_score([y[t]], b)
        pn, cn = band_score([y[t]], n)
        med += pb
        nor += pn
        cob_m.append(cb)
        cob_n.append(cn)
    return med, nor, float(np.mean(cob_m)), float(np.mean(cob_n)), b.method


out = {}
for it in segment(S).items:
    y = S[it.name]
    sel = puntajes(y, it.recommended_method, range(80, 92))
    ev = puntajes(y, it.recommended_method, range(92, 104))
    if sel is None or ev is None:
        continue
    m = ev[4]
    d = out.setdefault(m, {"skus": 0, "elige_medida": 0, "eval_sel": 0.0, "eval_normal": 0.0,
                           "eval_medida": 0.0, "gana_sel": 0, "cob_sel": [], "cob_normal": []})
    usa_medida = sel[0] < sel[1]
    d["skus"] += 1
    d["elige_medida"] += int(usa_medida)
    pick = ev[0] if usa_medida else ev[1]
    d["eval_sel"] += pick
    d["eval_normal"] += ev[1]
    d["eval_medida"] += ev[0]
    d["gana_sel"] += int(pick < ev[1])
    d["cob_sel"].append(ev[2] if usa_medida else ev[3])
    d["cob_normal"].append(ev[3])

print(json.dumps({"n": N, "por_metodo": {m: {
    "skus": d["skus"], "elige_medida": d["elige_medida"],
    "seleccion_sobre_normal": round(d["eval_sel"] / d["eval_normal"], 4),
    "siempre_medida_sobre_normal": round(d["eval_medida"] / d["eval_normal"], 4),
    "skus_donde_la_seleccion_mejora": d["gana_sel"],
    "cobertura_seleccion": round(float(np.mean(d["cob_sel"])), 3),
    "cobertura_normal": round(float(np.mean(d["cob_normal"])), 3),
} for m, d in out.items()}}, indent=1))
