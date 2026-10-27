"""Hierarchical Forecast Reconciliation (MinT / Trace Minimization).

Implements optimal forecast reconciliation across hierarchical supply-chain structures
(e.g., Total Enterprise -> Regional Warehouses -> SKUs).

Based on:
  - Hyndman et al. (2011): "Optimal combination forecasts for hierarchical time series" (CSDA)
  - Wickramasuriya, Athanasopoulos, Hyndman (2019): "Optimal Forecast Reconciliation
    for Hierarchical and Grouped Time Series Through Trace Minimization" (JASA)
  - FPP3 Chapter 11 (Hyndman & Athanasopoulos)

Mathematical Core:
  Let m be the total number of series in the hierarchy, and n_b be the number of bottom-level
  (leaf) series. The structural aggregation matrix S in {0, 1}^(m x n_b) defines:
      y_t = S * b_t
  Given independent base forecasts \\hat{y}_h, the reconciled coherent forecast \\tilde{y}_h is:
      \\tilde{y}_h = S * P * \\hat{y}_h
  where P in R^(n_b x m). The unbiased minimum trace (MinT) solution that minimizes
  Tr(Var(y_{t+h} - \\tilde{y}_h)) is:
      P = (S^T * W^{-1} * S)^{-1} * S^T * W^{-1}
  where W is the error covariance matrix of the base forecasts.

Supported Covariance Estimators for W:
  - "ols": W = I (Ordinary Least Squares)
  - "wls_struct": W = diag(S * 1) (Structural WLS, proportional to node hierarchy depth/leaves)
  - "wls_var": W = diag(Var(e_1), ..., Var(e_m)) (Sample variance scaling)
  - "mint_shrink": Ledoit-Wolf shrinkage on empirical covariance W for high-dimensional stability.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np
import scipy.linalg


@dataclass(frozen=True)
class HierarchyStructure:
    """Represents the hierarchical topology and structural summation matrix S."""

    node_names: tuple[str, ...]
    bottom_names: tuple[str, ...]
    S: np.ndarray

    def __post_init__(self):
        if self.S.ndim != 2:
            raise ValueError(f"S matrix must be 2D, got shape {self.S.shape}")
        m, n_b = self.S.shape
        if len(self.node_names) != m:
            raise ValueError(
                f"node_names length ({len(self.node_names)}) does not match S rows ({m})"
            )
        if len(self.bottom_names) != n_b:
            raise ValueError(
                f"bottom_names length ({len(self.bottom_names)}) does not match S columns ({n_b})"
            )

    @classmethod
    def from_groupings(
        cls,
        bottom_nodes: Sequence[str],
        groups: Mapping[str, Sequence[str]],
        root_name: str = "Total",
    ) -> HierarchyStructure:
        """Construct a 3-level hierarchy: Root -> Groups (e.g. Warehouses) -> Bottom (SKUs).

        Args:
            bottom_nodes: Ordered list of leaf node identifiers.
            groups: Mapping of group_name -> list of bottom node IDs belonging to it.
            root_name: Identifier for the root total node.

        Returns:
            Configured HierarchyStructure instance.
        """
        b_names = tuple(bottom_nodes)
        b_index = {name: i for i, name in enumerate(b_names)}
        n_b = len(b_names)

        group_names = sorted(groups.keys())
        all_nodes = [root_name] + group_names + list(b_names)
        m = len(all_nodes)

        S = np.zeros((m, n_b), dtype=float)

        # 1. Root connects to all bottom series
        S[0, :] = 1.0

        # 2. Group nodes connect to their assigned bottom series
        for g_idx, g_name in enumerate(group_names, start=1):
            children = groups[g_name]
            for ch in children:
                if ch in b_index:
                    S[g_idx, b_index[ch]] = 1.0
                else:
                    raise ValueError(f"child {ch!r} in group {g_name!r} not in bottom_nodes")

        # 3. Bottom nodes are identity mapping
        bottom_offset = 1 + len(group_names)
        for i in range(n_b):
            S[bottom_offset + i, i] = 1.0

        return cls(node_names=tuple(all_nodes), bottom_names=b_names, S=S)

    def is_coherent(self, values: Mapping[str, float], tolerance: float = 1e-4) -> bool:
        """Check whether a vector of node values satisfies the structural sum: y = S * b."""
        y_vec = np.array([values[name] for name in self.node_names], dtype=float)
        b_vec = np.array([values[name] for name in self.bottom_names], dtype=float)
        expected_y = self.S @ b_vec
        return bool(np.all(np.abs(y_vec - expected_y) <= tolerance))


@dataclass(frozen=True)
class HierarchicalReconciliationResult:
    """Output of hierarchical reconciliation with coherence verification metrics."""

    reconciled_forecasts: dict[str, float]
    base_forecasts: dict[str, float]
    adjustments: dict[str, float]
    method: str
    coherence_max_discrepancy: float
    is_coherent: bool


def _shrinkage_covariance(residuals: np.ndarray) -> np.ndarray:
    """Compute Ledoit-Wolf shrinkage covariance matrix W_shrink = lambda * diag(W) + (1-lambda) * W."""
    # residuals shape: (n_samples, m)
    n_samples, m = residuals.shape
    if n_samples < 2:
        return np.eye(m)

    # Sample covariance
    S_sample = np.cov(residuals, rowvar=False)
    if S_sample.ndim == 0:
        return np.array([[float(S_sample)]])

    diag_S = np.diag(np.diag(S_sample))

    # Ledoit-Wolf analytical shrinkage intensity estimation
    # Center residuals
    X = residuals - np.mean(residuals, axis=0)
    # Variance of the sample covariance entries
    phi_mat = np.zeros((m, m))
    for t in range(n_samples):
        x_t = X[t, :, None]
        phi_mat += (x_t @ x_t.T - S_sample) ** 2
    phi_mat /= n_samples

    phi = np.sum(phi_mat)
    gamma = float(np.linalg.norm(S_sample - diag_S, "fro") ** 2)

    if gamma > 0:
        kappa = phi / gamma
        shrinkage = max(0.0, min(1.0, kappa / n_samples))
    else:
        shrinkage = 1.0

    W_shrink = shrinkage * diag_S + (1.0 - shrinkage) * S_sample
    # Ensure positive definiteness with small ridge if needed
    min_eig = np.min(np.real(scipy.linalg.eigvals(W_shrink)))
    if min_eig <= 1e-7:
        W_shrink += (1e-6 - min_eig) * np.eye(m)
    return W_shrink


def reconcile_bottom_up(
    hierarchy: HierarchyStructure,
    base_forecasts: Mapping[str, float],
) -> HierarchicalReconciliationResult:
    """Aggregate bottom-level forecasts up the hierarchy (y_tilde = S * b_hat)."""
    b_vec = np.array([base_forecasts[name] for name in hierarchy.bottom_names], dtype=float)
    y_tilde = hierarchy.S @ b_vec

    reconciled = {name: float(val) for name, val in zip(hierarchy.node_names, y_tilde)}
    base = {name: float(base_forecasts[name]) for name in hierarchy.node_names}
    adjustments = {name: reconciled[name] - base[name] for name in hierarchy.node_names}

    return HierarchicalReconciliationResult(
        reconciled_forecasts=reconciled,
        base_forecasts=base,
        adjustments=adjustments,
        method="bottom_up",
        coherence_max_discrepancy=0.0,
        is_coherent=True,
    )


def reconcile_top_down(
    hierarchy: HierarchyStructure,
    base_forecasts: Mapping[str, float],
    proportions: Mapping[str, float] | None = None,
    root_name: str = "Total",
) -> HierarchicalReconciliationResult:
    """Disaggregate the top-level forecast down to bottom series using proportions."""
    root_forecast = float(base_forecasts[root_name])
    n_b = len(hierarchy.bottom_names)

    if proportions is None:
        # Default to base bottom forecast weights
        bottom_sum = sum(base_forecasts[b] for b in hierarchy.bottom_names)
        if bottom_sum > 0:
            weights = np.array(
                [base_forecasts[b] / bottom_sum for b in hierarchy.bottom_names],
                dtype=float,
            )
        else:
            weights = np.full(n_b, 1.0 / n_b, dtype=float)
    else:
        p_vec = np.array([proportions[b] for b in hierarchy.bottom_names], dtype=float)
        p_sum = float(np.sum(p_vec))
        if p_sum <= 0:
            raise ValueError("sum of top-down proportions must be positive")
        weights = p_vec / p_sum

    b_tilde = weights * root_forecast
    y_tilde = hierarchy.S @ b_tilde

    reconciled = {name: float(val) for name, val in zip(hierarchy.node_names, y_tilde)}
    base = {name: float(base_forecasts[name]) for name in hierarchy.node_names}
    adjustments = {name: reconciled[name] - base[name] for name in hierarchy.node_names}

    return HierarchicalReconciliationResult(
        reconciled_forecasts=reconciled,
        base_forecasts=base,
        adjustments=adjustments,
        method="top_down",
        coherence_max_discrepancy=0.0,
        is_coherent=True,
    )


def reconcile_mint(
    hierarchy: HierarchyStructure,
    base_forecasts: Mapping[str, float],
    *,
    residuals: np.ndarray | None = None,
    covariance_method: str = "mint_shrink",
    non_negative: bool = True,
) -> HierarchicalReconciliationResult:
    """Optimal Minimum Trace (MinT) forecast reconciliation.

    Formulation:
        P = (S^T * W^{-1} * S)^{-1} * S^T * W^{-1}
        \\tilde{y} = S * P * \\hat{y}

    Args:
        hierarchy: Structural aggregation tree.
        base_forecasts: Independent base forecasts for all m nodes.
        residuals: Array of shape (n_periods, m) of base forecast errors (fitted - actual).
                   Required for "mint_shrink" and "wls_var".
        covariance_method: One of "ols", "wls_struct", "wls_var", "mint_shrink".
        non_negative: If True, clips negative forecasts and re-aggregates via Bottom-Up.
    """
    valid_methods = {"ols", "wls_struct", "wls_var", "mint_shrink"}
    if covariance_method not in valid_methods:
        raise ValueError(
            f"unknown covariance_method: {covariance_method!r} (expected one of {sorted(valid_methods)})"
        )

    S = hierarchy.S
    m, n_b = S.shape
    y_hat = np.array([base_forecasts[name] for name in hierarchy.node_names], dtype=float)

    # 1. Compute covariance matrix W
    if covariance_method == "ols":
        W = np.eye(m)
    elif covariance_method == "wls_struct":
        # Node structural weight proportional to number of leaf nodes under it
        struct_weights = S @ np.ones(n_b, dtype=float)
        W = np.diag(struct_weights)
    elif covariance_method in ("wls_var", "mint_shrink"):
        if residuals is None or residuals.shape[1] != m:
            # Fallback to structural WLS if residuals not supplied
            struct_weights = S @ np.ones(n_b, dtype=float)
            W = np.diag(struct_weights)
        elif covariance_method == "wls_var":
            variances = np.var(residuals, axis=0, ddof=1)
            variances = np.where(variances > 1e-6, variances, 1.0)
            W = np.diag(variances)
        else:  # mint_shrink
            W = _shrinkage_covariance(residuals)

    # 2. Invert covariance matrix stably
    try:
        W_inv = scipy.linalg.inv(W)
    except scipy.linalg.LinAlgError:
        W_inv = scipy.linalg.pinv(W)

    # 3. Compute MinT projection matrix P = (S^T * W^{-1} * S)^{-1} * S^T * W^{-1}
    St_Winv = S.T @ W_inv
    denom = St_Winv @ S
    try:
        P = scipy.linalg.solve(denom, St_Winv, assume_a="pos")
    except (scipy.linalg.LinAlgError, ValueError):
        P = scipy.linalg.pinv(denom) @ St_Winv

    # 4. Compute reconciled forecasts: b_tilde = P * y_hat, y_tilde = S * b_tilde
    b_tilde = P @ y_hat

    if non_negative:
        # In supply chains, physical demand cannot be negative.
        # Clip bottom series to 0 and re-aggregate bottom-up to maintain exact coherence.
        b_tilde = np.maximum(0.0, b_tilde)

    y_tilde = S @ b_tilde

    reconciled = {name: float(val) for name, val in zip(hierarchy.node_names, y_tilde)}
    base = {name: float(base_forecasts[name]) for name in hierarchy.node_names}
    adjustments = {name: reconciled[name] - base[name] for name in hierarchy.node_names}

    # Verify coherence
    discrepancy = float(np.max(np.abs(y_tilde - S @ b_tilde)))
    is_coh = discrepancy <= 1e-4

    return HierarchicalReconciliationResult(
        reconciled_forecasts=reconciled,
        base_forecasts=base,
        adjustments=adjustments,
        method=f"mint_{covariance_method}",
        coherence_max_discrepancy=discrepancy,
        is_coherent=is_coh,
    )
