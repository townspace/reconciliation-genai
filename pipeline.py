"""
pipeline.py
-----------
Phase 7 — orchestration / end-to-end trace.

Turns isolated rules into the connected money trace the target diagram depicts:
OMS / internal data -> recon bucket 1 -> recon bucket 2 -> bank reco. A `Pipeline`
is an ordered list of `Stage`s (each a rule on a lane). The runner executes every
stage and produces:

  - a per-stage summary (lane, matched, breaks) — the "pipeline view" data, and
  - an end-to-end trace per flow that attributes a break to the FIRST hop where
    the flow breaks.

The golden-path spine (R2 -> R8 -> R12, with R13 validating the service charge)
exercises every new mode at once; `golden_path()` wires it on the sample data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import pandas as pd

from base import MATCHED_STATUSES, DataSource
from wrapper import ReconWrapper

# Lane order reflects the four-lane structure of the target diagram.
LANES = ["OMS/internal", "recon-1", "recon-2", "bank"]


def is_break(status: str) -> bool:
    return status not in MATCHED_STATUSES


@dataclass
class Stage:
    name: str
    rule_id: str
    lane: str


@dataclass
class PipelineResult:
    results: Dict[str, "object"]          # stage name -> ReconResult
    stage_summary: pd.DataFrame
    stages: List[Stage]


class Pipeline:
    """An ordered set of reconciliation stages forming a money trace."""

    def __init__(self, stages: List[Stage]) -> None:
        self.stages = stages

    def run(self, sources_per_stage: Dict[str, Dict[str, DataSource]],
            wrapper: Optional[ReconWrapper] = None,
            rate_map_per_stage: Optional[Dict[str, dict]] = None) -> PipelineResult:
        wrapper = wrapper or ReconWrapper()
        rate_map_per_stage = rate_map_per_stage or {}
        results, rows = {}, []
        for st in self.stages:
            res = wrapper.run_rule(st.rule_id, sources_per_stage[st.name],
                                   rate_map=rate_map_per_stage.get(st.name))
            results[st.name] = res
            total = int(len(res.detail))
            breaks = int(len(res.breaks))
            rows.append({
                "lane": st.lane, "stage": st.name, "rule": st.rule_id,
                "total": total, "matched": total - breaks, "breaks": breaks,
                "status": "clean" if breaks == 0 else "breaks",
            })
        order = {lane: i for i, lane in enumerate(LANES)}
        summary = pd.DataFrame(rows)
        summary = summary.sort_values(
            by="lane", key=lambda s: s.map(lambda x: order.get(x, 99))
        ).reset_index(drop=True)
        return PipelineResult(results=results, stage_summary=summary,
                              stages=self.stages)

    def trace(self, result: PipelineResult, flows: List[dict]) -> pd.DataFrame:
        """End-to-end trace: for each flow (a dict of stage_name -> recon_key),
        record the status at each hop and the first hop where it breaks.

        Flows must contain a 'flow_id'; remaining items map a stage name to the
        recon_key that represents this flow in that stage.
        """
        status_by_stage = {
            st.name: dict(zip(result.results[st.name].detail["recon_key"],
                              result.results[st.name].detail["status"]))
            for st in self.stages
        }
        rows = []
        for flow in flows:
            row = {"flow_id": flow["flow_id"]}
            first_break = ""
            for st in self.stages:
                key = flow.get(st.name)
                status = status_by_stage[st.name].get(key, "ABSENT") if key else "—"
                row[st.name] = status
                if not first_break and status != "—" and (
                        status == "ABSENT" or is_break(status)):
                    first_break = st.name
            row["first_break"] = first_break or "(clean)"
            rows.append(row)
        return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Golden-path spine wired on the built-in sample data.
# ---------------------------------------------------------------------------
def _sources_for(rule_id: str):
    """Build DataSources for a stage from its sample frames (UI-style guessing)."""
    from samples import sample_data
    from registry import all_specs

    key_c = ["batch_id", "deposit_batch", "remittance_id", "txn_id", "wallet_txn_id",
             "order_id", "sub_id", "posting_id", "bank_ref", "journal_id",
             "charge_type", "merchant_id"]
    amt_c = ["wallet_amount_utilized", "gross_amount", "net_amount", "credit_amount",
             "actual_charge", "actual_commission", "rate_pct", "transaction_amount",
             "amount"]
    date_c = ["txn_date", "settle_date", "date", "value_date", "posting_date"]
    narr_c = ["narration", "description"]

    def first(cols, cands):
        return next((c for c in cands if c in cols), cols[0])

    spec = all_specs()[rule_id]
    frames = sample_data(rule_id)
    rate_map = dict(spec.rate_map or {})
    sources = {}
    for feed in spec.feeds:
        df = frames[feed.role]
        cols = list(df.columns)
        for _l, rkey, cands in feed.extra:
            rate_map[rkey] = first(cols, cands)
        sources[feed.role] = DataSource(
            feed.role, df, first(cols, key_c), first(cols, amt_c),
            narration_column=first(cols, narr_c) if feed.narration else None,
            date_column=first(cols, date_c) if feed.date else None)
    return sources, rate_map


def golden_path():
    """Return (pipeline, sources_per_stage, rate_map_per_stage, flows) for the
    R2 -> R8 -> R13 -> R12 spine on the sample data."""
    stages = [
        Stage("semantic-match", "R2", "recon-1"),
        Stage("settlement-timing", "R8", "recon-2"),
        Stage("charge-validation", "R13", "recon-2"),
        Stage("bank-reco", "R12", "bank"),
    ]
    sources_per_stage, rate_map_per_stage = {}, {}
    for st in stages:
        src, rmap = _sources_for(st.rule_id)
        sources_per_stage[st.name] = src
        if rmap:
            rate_map_per_stage[st.name] = rmap

    # Conceptual money flows, each picking a representative key per stage. These
    # demonstrate first-breaking-hop attribution across the spine.
    flows = [
        {"flow_id": "FLOW-1 (clean)",
         "semantic-match": "BNK-1 ~ J-900", "settlement-timing": "T3",
         "charge-validation": "S1", "bank-reco": "B1"},
        {"flow_id": "FLOW-2 (breaks at settlement)",
         "semantic-match": "BNK-2 ~ J-901", "settlement-timing": "T4",
         "charge-validation": "S3", "bank-reco": "B1"},
        {"flow_id": "FLOW-3 (breaks at charge)",
         "semantic-match": "BNK-3 ~ J-902", "settlement-timing": "T1",
         "charge-validation": "S2", "bank-reco": "B1"},
        {"flow_id": "FLOW-4 (breaks at bank)",
         "semantic-match": "BNK-1 ~ J-900", "settlement-timing": "T2",
         "charge-validation": "S1", "bank-reco": "B2"},
    ]
    return Pipeline(stages), sources_per_stage, rate_map_per_stage, flows
