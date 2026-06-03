"""
ai/detect.py
------------
Feedback #3 — Intelligent Rule Suggestion.

Analyse uploaded file headers + sample rows and auto-detect what each feed is
(bank statement, GL export, wallet ledger, OMS/POS, payment gateway), guess the
key / amount / narration columns, and recommend which rule + matching strategy
to run. Replaces having the user hand-pick everything in build_config_interactively().

LLM path : Claude reads the profiles and proposes role + columns + rule.
Offline  : keyword heuristics over column names. Always returns a usable config.
"""

from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd

from base import DataSource, MatchStrategy
from ai.client import LLMClient, default_client

# Column-name keyword signatures for each source type.
_SOURCE_SIGNATURES = {
    "bank":    ["value date", "value_date", "narration", "cheque", "debit", "credit", "balance"],
    "gl":      ["journal", "gl account", "gl_account", "ledger", "voucher", "account code"],
    "wallet":  ["wallet", "txn_id", "transaction_amount", "wallet_txn"],
    "oms_pos": ["order", "store", "wallet_amount_utilized", "pos", "till"],
    "pg_edc":  ["payment_id", "mid", "merchant", "rrn", "auth", "settlement", "edc"],
}
_KEY_HINTS = ["order_id", "order", "payment_id", "txn", "reference", "ref", "rrn",
              "journal", "id", "utr", "cheque"]
_AMOUNT_HINTS = ["amount", "amt", "value", "debit", "credit", "transaction_amount",
                 "wallet_amount_utilized", "settlement"]
_NARRATION_HINTS = ["narration", "description", "desc", "remarks", "particulars",
                    "memo", "details"]

_SYSTEM = (
    "You classify financial data feeds for reconciliation. For each feed, name "
    "its type, its recon key column, its amount column, and any narration column."
)


def _score_type(columns: List[str]) -> str:
    low = [c.lower() for c in columns]
    blob = " ".join(low)
    best, best_score = "unknown", 0
    for src, kws in _SOURCE_SIGNATURES.items():
        score = sum(1 for kw in kws if kw in blob)
        if score > best_score:
            best, best_score = src, score
    return best


def _pick(columns: List[str], hints: List[str]) -> Optional[str]:
    low = {c.lower(): c for c in columns}
    for h in hints:                              # exact-ish match first
        for lc, orig in low.items():
            if lc == h:
                return orig
    for h in hints:                              # then substring
        for lc, orig in low.items():
            if h in lc:
                return orig
    return None


def profile_frame(name: str, df: pd.DataFrame, sample_rows: int = 3) -> Dict[str, object]:
    """Lightweight profile of a single feed for detection."""
    cols = list(df.columns)
    return {
        "name": name,
        "columns": cols,
        "dtypes": {c: str(df[c].dtype) for c in cols},
        "sample": df.head(sample_rows).to_dict(orient="records"),
        "n_rows": int(len(df)),
    }


def _heuristic_detect(name: str, df: pd.DataFrame) -> Dict[str, object]:
    cols = list(df.columns)
    stype = _score_type(cols)
    key = _pick(cols, _KEY_HINTS)
    amount = _pick(cols, _AMOUNT_HINTS)
    narration = _pick(cols, _NARRATION_HINTS)
    return dict(name=name, source_type=stype, key_column=key,
                amount_column=amount, narration_column=narration,
                rationale=f"Header keywords best match '{stype}'.")


def detect_sources(frames: Dict[str, pd.DataFrame],
                   client: Optional[LLMClient] = None) -> Dict[str, object]:
    """Profile each feed, guess its config, and recommend a rule + strategy.

    Returns:
        {
          "feeds": [ {name, source_type, key_column, amount_column,
                      narration_column, rationale}, ... ],
          "recommended_rule": str | None,
          "recommended_strategy": str,
          "ready_sources": {role: DataSource},   # plug straight into the wrapper
          "notes": str,
        }
    """
    client = client or default_client()
    profiles = [profile_frame(n, df) for n, df in frames.items()]

    feeds: List[Dict[str, object]] = []
    if getattr(client, "live", False):
        prompt = ("Feeds:\n" + "\n".join(str(p) for p in profiles) +
                  '\nReturn JSON list, one object per feed: '
                  '{"name","source_type","key_column","amount_column",'
                  '"narration_column","rationale"}.')
        resp = client.complete_json(_SYSTEM, prompt, max_tokens=700)
        if isinstance(resp, list):
            by_name = {str(r.get("name")): r for r in resp if isinstance(r, dict)}
            for name, df in frames.items():
                r = by_name.get(name)
                if r:
                    feeds.append({
                        "name": name,
                        "source_type": r.get("source_type", "unknown"),
                        "key_column": r.get("key_column"),
                        "amount_column": r.get("amount_column"),
                        "narration_column": r.get("narration_column"),
                        "rationale": r.get("rationale", "Model classification."),
                    })
                else:
                    feeds.append(_heuristic_detect(name, df))
    if not feeds:                                # offline or LLM miss
        feeds = [_heuristic_detect(n, df) for n, df in frames.items()]

    # Build ready-to-run DataSources keyed by detected source_type (role).
    ready: Dict[str, DataSource] = {}
    for f, (name, df) in zip(feeds, frames.items()):
        role = f.get("source_type") or name
        key = f.get("key_column") or df.columns[0]
        amount = f.get("amount_column") or df.columns[-1]
        ready[role] = DataSource(role=role, df=df, key_column=key,
                                 amount_column=amount,
                                 narration_column=f.get("narration_column"))

    # Recommend a rule by matching detected roles against the registry.
    rule, strategy, note = _recommend_rule(set(ready), feeds)
    return dict(feeds=feeds, recommended_rule=rule,
                recommended_strategy=strategy, ready_sources=ready, notes=note)


def _recommend_rule(roles: set, feeds: List[Dict[str, object]]):
    """Match detected roles to a registered rule; pick a sensible strategy."""
    try:
        from rules import RULE_REGISTRY
    except Exception:
        RULE_REGISTRY = {}

    best_rule, best_overlap = None, 0
    for rid, cls in RULE_REGISTRY.items():
        need = set(getattr(cls, "required_sources", []))
        overlap = len(need & roles)
        if overlap > best_overlap:
            best_rule, best_overlap = rid, overlap

    has_narration = any(f.get("narration_column") for f in feeds)
    has_bank = any(f.get("source_type") == "bank" for f in feeds)
    strategy = (MatchStrategy.SEMANTIC.value
                if (has_narration and has_bank) else MatchStrategy.EXACT.value)

    if best_rule:
        note = (f"Detected roles {sorted(roles)} best match rule {best_rule}; "
                f"suggested strategy '{strategy}'.")
    else:
        note = (f"No registered rule fully matches roles {sorted(roles)}. "
                f"Suggested strategy '{strategy}' — configure a new rule.")
    return best_rule, strategy, note
