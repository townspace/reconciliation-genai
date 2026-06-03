"""
ai/anomaly.py
-------------
Feedback #6 — Anomaly Detection & Pattern Recognition.

Flag breaks that are unusual relative to the rest: "this 5M mismatch is 10x the
typical break for this rule — escalate." Uses a robust (median/MAD) outlier
score so a few extreme values don't hide the rest, plus optional recurring-pattern
notes when a grouping column is available. An LLM, when present, adds a short
narrative pattern summary.

Statistics run fully offline; Claude is additive, never required.
"""

from __future__ import annotations

from typing import List, Optional

import numpy as np
import pandas as pd

from base import ReconResult
from ai.client import LLMClient, default_client


def _robust_scores(values: np.ndarray) -> np.ndarray:
    """Modified z-score using median absolute deviation (robust to outliers)."""
    if len(values) == 0:
        return values
    med = np.median(values)
    mad = np.median(np.abs(values - med))
    if mad == 0:
        # fall back to mean/std if MAD collapses (many identical values)
        std = values.std()
        if std == 0:
            return np.zeros_like(values, dtype=float)
        return (values - values.mean()) / std
    return 0.6745 * (values - med) / mad


def detect_anomalies(result: ReconResult, group_column: Optional[str] = None,
                     z_threshold: float = 3.5, ratio_threshold: float = 10.0,
                     client: Optional[LLMClient] = None) -> pd.DataFrame:
    """Score breaks for outlier magnitude; flag and annotate. Stored on result.anomalies."""
    client = client or default_client()
    breaks = result.breaks
    if breaks is None or len(breaks) == 0:
        result.anomalies = pd.DataFrame()
        return result.anomalies

    diffs = breaks.get("difference")
    if diffs is None:
        result.anomalies = pd.DataFrame()
        return result.anomalies

    mags = diffs.abs().to_numpy(dtype=float)
    scores = _robust_scores(mags)
    median_mag = float(np.median(mags)) if len(mags) else 0.0

    flags, notes = [], []
    for m, z in zip(mags, scores):
        ratio = (m / median_mag) if median_mag else 0.0
        is_anom = abs(z) >= z_threshold or ratio >= ratio_threshold
        flags.append(bool(is_anom))
        if is_anom:
            notes.append(f"{ratio:.1f}x the typical break ({median_mag:.2f}); escalate.")
        else:
            notes.append("")

    out = breaks.copy()
    out["anomaly_score"] = np.round(scores, 2)
    out["anomaly_flag"] = flags
    out["anomaly_note"] = notes
    anomalies = out[out["anomaly_flag"]].reset_index(drop=True)

    # Optional recurring-pattern note over a grouping column (e.g. store_id, entity).
    pattern_notes: List[str] = []
    if group_column and group_column in breaks.columns:
        grp = breaks.groupby(group_column)["difference"].agg(["count", "sum"])
        recurring = grp[grp["count"] >= 2]
        for g, r in recurring.iterrows():
            pattern_notes.append(
                f"{group_column}={g} breaks {int(r['count'])}× (net {r['sum']:.2f}); "
                f"consider a standing tolerance/rule.")

    if pattern_notes:
        existing = result.ai_commentary
        result.ai_commentary = (existing + " Patterns: " + " ".join(pattern_notes)).strip()

    result.anomalies = anomalies
    return anomalies
