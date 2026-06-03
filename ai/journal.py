"""
ai/journal.py
-------------
Feedback #5 — Auto-Journal Generation.

For each break, draft a balanced adjustment journal entry: Dr/Cr accounts
(inferred from a GL mapping), an adjustment narrative, and a financial-statement
impact note — in a shape ready for ERP upload.

LLM path : Claude proposes accounts + narrative from the break + GL mapping.
Offline  : deterministic double-entry from the sign of the difference and a
           default suspense-based mapping. Always balanced (Dr total == Cr total).
"""

from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd

from base import MatchStatus, ReconResult
from ai.client import LLMClient, default_client

# A minimal default chart-of-accounts mapping; override via gl_mapping=.
DEFAULT_GL_MAPPING = {
    "suspense": "1999 - Reconciliation Suspense",
    "left": "1100 - Primary Ledger Control",
    "right": "2100 - Counterparty Ledger Control",
    "fees": "5400 - Bank/Processing Fees",
    "fx": "7200 - FX Gain/Loss",
}

_SYSTEM = (
    "You are a financial controller drafting adjustment journals for "
    "reconciliation breaks. Keep entries balanced and ERP-ready."
)


def _amount_cols(df: pd.DataFrame) -> List[str]:
    return [c for c in df.columns if c.endswith("_amount")]


def _heuristic_entry(row: pd.Series, gl: Dict[str, str]) -> Dict[str, object]:
    status = row.get("status", "")
    diff = float(row.get("difference", 0) or 0)
    amt = round(abs(diff), 2)
    key = row.get("recon_key", "")
    category = row.get("ai_category", "")

    # Choose the contra account: a recognised cost category if classified, else suspense.
    contra = gl["suspense"]
    if category == "FEE_OR_CHARGE":
        contra = gl["fees"]
    elif category in ("FX_RATE", "ROUNDING"):
        contra = gl["fx"]

    if status == MatchStatus.MISSING_IN_RIGHT.value:
        # Present in primary, absent in counterparty -> recognise the missing item.
        dr, cr = gl["right"], contra
        narrative = f"Record item {key} present in primary but missing in counterparty feed."
        fs_impact = "Increases counterparty control balance; clears suspense on settlement."
    elif status == MatchStatus.MISSING_IN_LEFT.value:
        dr, cr = contra, gl["left"]
        narrative = f"Record item {key} present in counterparty but missing in primary feed."
        fs_impact = "Increases primary control balance; clears suspense on settlement."
    elif status == MatchStatus.AMOUNT_MISMATCH.value:
        if diff > 0:   # primary > counterparty
            dr, cr = contra, gl["right"]
            narrative = f"Adjust {key}: primary exceeds counterparty by {amt:.2f}."
        else:
            dr, cr = gl["left"], contra
            narrative = f"Adjust {key}: counterparty exceeds primary by {amt:.2f}."
        fs_impact = "Brings the two control accounts into agreement for the period."
    else:
        dr, cr = contra, contra
        narrative = f"Review item {key} ({status})."
        fs_impact = "No P&L impact pending classification."

    return dict(recon_key=key, dr_account=dr, cr_account=cr, amount=amt,
                narrative=narrative, fs_impact=fs_impact,
                source_status=status, confidence=0.5)


def generate_journals(result: ReconResult, gl_mapping: Optional[Dict[str, str]] = None,
                      client: Optional[LLMClient] = None,
                      max_llm_rows: int = 30) -> pd.DataFrame:
    """Return a DataFrame of draft journal entries (also stored on result.journals)."""
    client = client or default_client()
    gl = {**DEFAULT_GL_MAPPING, **(gl_mapping or {})}
    breaks = result.breaks
    if breaks is None or len(breaks) == 0:
        result.journals = pd.DataFrame()
        return result.journals

    entries: List[Dict[str, object]] = []
    use_llm = getattr(client, "live", False) and len(breaks) <= max_llm_rows
    for _, row in breaks.iterrows():
        entry = None
        if use_llm:
            amt_cols = _amount_cols(breaks)
            fields = {c: (None if pd.isna(row.get(c)) else row.get(c))
                      for c in amt_cols + ["status", "difference", "recon_key",
                                           "ai_category"]}
            prompt = (
                f"GL mapping: {gl}\nBreak: {fields}\n"
                'Return JSON {"dr_account","cr_account","amount","narrative",'
                '"fs_impact","confidence"} for a single balanced adjustment.'
            )
            resp = client.complete_json(_SYSTEM, prompt, max_tokens=260)
            if isinstance(resp, dict) and "dr_account" in resp:
                try:
                    amt = round(float(resp.get("amount", abs(float(row.get("difference", 0))))), 2)
                except Exception:
                    amt = round(abs(float(row.get("difference", 0) or 0)), 2)
                entry = dict(recon_key=row.get("recon_key", ""),
                             dr_account=str(resp["dr_account"]),
                             cr_account=str(resp.get("cr_account", "")),
                             amount=amt,
                             narrative=str(resp.get("narrative", "")),
                             fs_impact=str(resp.get("fs_impact", "")),
                             source_status=row.get("status", ""),
                             confidence=round(float(resp.get("confidence", 0.7)), 2))
        if entry is None:
            entry = _heuristic_entry(row, gl)
        entries.append(entry)

    journals = pd.DataFrame(entries, columns=[
        "recon_key", "dr_account", "cr_account", "amount",
        "narrative", "fs_impact", "source_status", "confidence",
    ])
    result.journals = journals.reset_index(drop=True)
    return result.journals
