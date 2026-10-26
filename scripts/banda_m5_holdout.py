"""Banda medida vs banda normal sobre M5 real (holdout pinball + cobertura)."""
import json
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
from jobs import forecast_job as fj  # noqa: E402

M5 = r"C:\Users\Gamer\Music\scm\kern\data\kaggle\m5\m5\datasets\sales_train_evaluation.csv"
N, SEMANAS, SEMILLA = int(sys.argv[1]), 104, 20261026

df = pd.read_csv(M5)
dias = [c for c in df.columns if c.startswith("d_")]
muestra = df.sample(n=N, random_state=SEMILLA)
series = {}
for _, fila in muestra.iterrows():
    diario = fila[dias].to_numpy(dtype=float)
    diario = diario[len(diario) % 7:]
    semanal = diario.reshape(-1, 7).sum(axis=1)[-SEMANAS:]
    series[fila["id"]] = [float(v) for v in semanal]

r = fj.run(series)
por = {}
for s in r.skus:
    d = por.setdefault(s.band_method, {"n": 0, "comparados": 0, "gana": 0, "cob": [], "cob_normal": [],
                                         "ratio": []})
    d["n"] += 1
    if s.holdout_pinball is not None and s.holdout_pinball_normal is not None:
        d["comparados"] += 1
        d["gana"] += int(s.holdout_pinball < s.holdout_pinball_normal)
        d["cob"].append(s.holdout_coverage)
        if s.holdout_pinball_normal > 0:
            d["ratio"].append(s.holdout_pinball / s.holdout_pinball_normal)
out = {}
for m, d in por.items():
    out[m] = {
        "skus": d["n"], "comparados": d["comparados"], "gana_a_la_normal": d["gana"],
        "cobertura_media_P10_P90": round(float(np.mean(d["cob"])), 3) if d["cob"] else None,
        "pinball_relativo_mediano": round(float(np.median(d["ratio"])), 3) if d["ratio"] else None,
    }
print(json.dumps({"n": N, "semanas": SEMANAS, "semilla": SEMILLA, "mix": r.mix,
                  "n_band_compared": r.n_band_compared,
                  "n_band_beats_normal": r.n_band_beats_normal, "por_metodo": out}, indent=1))
