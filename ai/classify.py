"""
ai/classify.py
--------------
Feedback #2 — Exception Classification & Root Cause.

After matching, breaks are categorised mechanically (MISSING_IN_RIGHT,
AMOUNT_MISMATCH). This module adds the *why*: a root-cause category, an
audit-ready one-line commentary, and a confidence per break. It also writes an
overall narrative onto ReconResult.ai_commentary and a mean ReconResult.confidence.

LLM path  : sends each break (+ recon context) to Claude for a JSON verdict.
Offline   : deterministic heuristics over status, magnitude, sign and narration.
"""

from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd

from base import MATCHED_STATUSES, MatchStatus, ReconResult
from ai.client import LLMClient, default_client

CATEGORIES = [
    "TIMING", "DUPLICATE", "FX_RATE", "FEE_OR_CHARGE",
    "ROUNDING", "DATA_ENTRY", "MISSING_GENUINE", "UNKNOWN",
]

_SYSTEM = (
    "You are a reconciliation analyst. Given one reconciliation break, classify "
    "its most likely root cause and write a short audit-ready note. "
    f"category must be one of: {', '.join(CATEGORIES)}."
)


def _amount_cols(df: pd.DataFrame) -> List[str]:
    return [c for c in df.columns if c.endswith("_amount")]


def _heuristic(row: pd.Series, amt_cols: List[str], typical: float,
               dup_amounts: set) -> Dict[str, object]:
    """Deterministic root-cause guess. Mirrors how an analyst triages quickly."""
    status = row.get("status", "")
    diff = float(row.get("difference", 0) or 0)
    adiff = abs(diff)
    vals = [abs(float(row[c])) for c in amt_cols if pd.notna(row.get(c))]
    base = max(vals) if vals else 0.0
    rel = adiff / base if base else 0.0

    if status == MatchStatus.AMOUNT_MISMATCH.value:
        if rel <= 0.005 or adiff <= 0.05:
            return dict(category="ROUNDING", confidence=0.78,
                        commentary=f"Sub-0.5% delta of {adiff:.2f}; rounding/precision noise.")
        if rel <= 0.05:
            return dict(category="FX_RATE", confidence=0.55,
                        commentary=f"Small {rel*100:.1f}% delta ({adiff:.2f}); likely FX rate or minor fee.")
        if float(adiff).is_integer() and adiff < base:
            return dict(category="FEE_OR_CHARGE", confidence=0.5,
                        commentary=f"Clean delta of {adiff:.2f}; possible fee/charge withheld on one side.")
        return dict(category="DATA_ENTRY", confidence=0.45,
                    commentary=f"Large {rel*100:.0f}% delta ({adiff:.2f}); probable keying/data error — verify.")

    if status in (MatchStatus.MISSING_IN_RIGHT.value, MatchStatus.MISSING_IN_LEFT.value):
        side = "secondary" if status == MatchStatus.MISSING_IN_RIGHT.value else "primary"
        if base in dup_amounts:
            return dict(category="DUPLICATE", confidence=0.5,
                        commentary=f"Amount {base:.2f} also appears elsewhere; check for duplicate posting.")
        if typical and adiff > 10 * typical:
            return dict(category="MISSING_GENUINE", confidence=0.55,
                        commentary=f"Large one-sided item ({adiff:.2f}) absent from {side} feed; likely genuinely missing — escalate.")
        return dict(category="TIMING", confidence=0.5,
                    commentary=f"Present in one feed only; likely timing — confirm clearing in adjacent period on the {side} feed.")

    return dict(category="UNKNOWN", confidence=0.3,
                commentary="Unclassified break — manual review required.")


def _llm_classify(client: LLMClient, row: pd.Series, amt_cols: List[str],
                  ctx: Dict[str, object]) -> Optional[Dict[str, object]]:
    fields = {c: (None if pd.isna(row.get(c)) else row.get(c))
              for c in amt_cols + ["status", "difference", "recon_key"]}
    narr = {c: row.get(c) for c in row.index if c.endswith("_narration")}
    prompt = (
        f"Recon context: {ctx}\n"
        f"Break: {fields}\n"
        f"Narrations: {narr}\n"
        'Return JSON: {"category": "...", "confidence": 0.0-1.0, "commentary": "one sentence"}'
    )
    resp = client.complete_json(_SYSTEM, prompt, max_tokens=200)
    if not isinstance(resp, dict) or "category" not in resp:
        return None
    cat = str(resp.get("category", "UNKNOWN")).upper()
    if cat not in CATEGORIES:
        cat = "UNKNOWN"
    try:
        conf = float(resp.get("confidence", 0.5))
    except Exception:
        conf = 0.5
    return dict(category=cat, confidence=max(0.0, min(1.0, conf)),
                commentary=str(resp.get("commentary", "")).strip())


def classify_breaks(result: ReconResult, client: Optional[LLMClient] = None,
                    max_llm_rows: int = 40) -> ReconResult:
    """Attach ai_category / ai_commentary / ai_confidence to every break.

    Also sets result.ai_commentary (overall narrative) and result.confidence
    (mean per-break confidence). Returns the same ReconResult for chaining.
    """
    client = client or default_client()
    breaks = result.breaks
    if breaks is None or len(breaks) == 0:
        result.ai_commentary = "Clean reconciliation — no breaks to classify."
        result.confidence = 1.0
        return result

    amt_cols = _amount_cols(breaks)
    diffs = breaks.get("difference")
    typical = float(diffs.abs().median()) if diffs is not None and len(diffs) else 0.0
    dup_amounts = set()
    for c in amt_cols:
        vals = breaks[c].dropna().round(2)
        dup_amounts |= set(vals[vals.duplicated()].tolist())
    ctx = {"rule": result.rule_id, "key": result.recon_key,
           "typical_break_abs": round(typical, 2),
           "total_breaks": int(len(breaks))}

    cats, notes, confs = [], [], []
    use_llm = getattr(client, "live", False) and len(breaks) <= max_llm_rows
    for _, row in breaks.iterrows():
        verdict = _llm_classify(client, row, amt_cols, ctx) if use_llm else None
        if verdict is None:
            verdict = _heuristic(row, amt_cols, typical, dup_amounts)
        cats.append(verdict["category"])
        confs.append(round(float(verdict["confidence"]), 2))
        notes.append(verdict["commentary"])

    breaks = breaks.copy()
    breaks["ai_category"] = cats
    breaks["ai_confidence"] = confs
    breaks["ai_commentary"] = notes
    result.breaks = breaks.reset_index(drop=True)

    # merge per-break commentary back into detail (matched rows stay blank)
    if result.detail is not None and len(result.detail):
        det = result.detail.copy()
        note_map = dict(zip(breaks["recon_key"], notes))
        cat_map = dict(zip(breaks["recon_key"], cats))
        det["ai_category"] = det["recon_key"].map(cat_map).fillna("")
        det["ai_commentary"] = det["recon_key"].map(note_map).fillna("")
        result.detail = det

    # overall narrative + confidence
    cat_counts = pd.Series(cats).value_counts().to_dict()
    summary_bits = ", ".join(f"{n}× {c.lower().replace('_', ' ')}"
                             for c, n in cat_counts.items())
    overall = (f"{len(breaks)} break(s) on {result.rule_id}: {summary_bits}. "
               f"Net difference {result.summary.get('net_difference', 0)}.")
    if getattr(client, "live", False):
        nice = client.complete(
            "You write a 2-3 sentence executive reconciliation summary.",
            f"Rule {result.rule_id}. Break categories: {cat_counts}. "
            f"Net difference {result.summary.get('net_difference', 0)}. "
            f"Total keys {result.summary.get('total_keys', 0)}.", max_tokens=180)
        if nice:
            overall = nice.strip()
    result.ai_commentary = overall
    result.confidence = round(sum(confs) / len(confs), 2) if confs else 1.0
    return result
