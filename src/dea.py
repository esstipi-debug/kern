"""Data Envelopment Analysis (Jacobs & Chase 15e, ch. 25).

Rates the relative efficiency of comparable units (suppliers, warehouses, DCs, stores) as
a frontier: each unit's input/output weights are chosen by LP to show it in the best light
against all peers. Efficient units score 1.0; the rest score < 1.0. Unlike supplier
scorecards / TOPSIS (fixed weights), DEA derives the frontier from the data with no preset
weights. Input-oriented CCR model, solved via ``scipy.optimize.linprog`` (envelopment form).
"""

from __future__ import annotations

import numpy as np
from scipy.optimize import linprog


def dea_efficiency(inputs: list[list[float]], outputs: list[list[float]]) -> list[float]:
    """Input-oriented CCR efficiency theta in (0, 1] for each decision-making unit (DMU).

    ``inputs`` / ``outputs`` are row-per-DMU matrices. For each DMU o:
        minimise theta  s.t.  sum_j lambda_j x_ij <= theta x_io  (each input i)
                              sum_j lambda_j y_rj >= y_ro         (each output r)
                              lambda_j, theta >= 0
    """
    x = np.asarray(inputs, dtype=float)
    y = np.asarray(outputs, dtype=float)
    if x.ndim != 2 or y.ndim != 2 or x.shape[0] != y.shape[0]:
        raise ValueError("inputs and outputs must be row-per-DMU matrices with equal rows")
    n_dmu, n_in = x.shape
    n_out = y.shape[1]

    scores: list[float] = []
    for o in range(n_dmu):
        # variables: [theta, lambda_0 .. lambda_{n-1}]
        c = np.zeros(n_dmu + 1)
        c[0] = 1.0
        a_ub, b_ub = [], []
        for i in range(n_in):                       # sum_j lambda_j x_ij - theta x_io <= 0
            row = np.zeros(n_dmu + 1)
            row[0] = -x[o, i]
            row[1:] = x[:, i]
            a_ub.append(row)
            b_ub.append(0.0)
        for r in range(n_out):                      # -sum_j lambda_j y_rj <= -y_ro
            row = np.zeros(n_dmu + 1)
            row[1:] = -y[:, r]
            a_ub.append(row)
            b_ub.append(-y[o, r])
        res = linprog(
            c, A_ub=np.array(a_ub), b_ub=np.array(b_ub),
            bounds=[(0, None)] * (n_dmu + 1), method="highs",
        )
        scores.append(float(res.x[0]) if res.success else float("nan"))
    return scores
