"""
wrapper.py
----------
The top-level "wrapper model". It does not contain recon logic itself — it
orchestrates the mini models (R1..R14) registered in rules.py.

User flow it supports:
  1. User picks the type of reconciliation (transactional / summary), OR lets
     the AI auto-detect feeds and recommend a rule (build_config_with_ai).
  2. User specifies the data sources (and which roles they map to).
  3. Wrapper selects the matching mini model(s) and runs them.
  4. Optionally, the wrapper ENRICHES results with GenAI: root-cause commentary,
     draft journals, and anomaly flags.

Entry points:
  - ReconWrapper.run_rule(rule_id, sources, enrich=...)  -> one rule (+AI)
  - ReconWrapper.run_all(sources_per_rule, enrich=...)    -> several rules
  - ReconWrapper.enrich(result, ...)                      -> AI pass over a result
  - build_config_interactively(wrapper)                   -> prompt-driven setup
  - build_config_with_ai(frames, client=...)              -> AI auto-detection
"""

from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd

from base import DataSource, ReconResult, ReconType
from registry import all_specs, dispatch


class ReconWrapper:
    """Orchestrates the rules declared in the registry, dispatching by mode."""

    def __init__(self) -> None:
        # the declarative rule registry (id -> RuleSpec), loaded on demand
        self.rules = all_specs()

    # -- discovery -------------------------------------------------------
    def list_rules(self, recon_type: Optional[ReconType] = None) -> List[str]:
        ids = sorted(self.rules)
        if recon_type is None:
            return ids
        return [r for r in ids if self.rules[r].recon_type == recon_type]

    def describe(self, rule_id: str) -> str:
        m = self.rules[rule_id]
        return (f"{m.id} [{m.recon_type.value}/{m.mode}] key={m.recon_key} "
                f"sources={m.required_roles}\n   {m.description}")

    # -- execution -------------------------------------------------------
    def run_rule(self, rule_id: str, sources: Dict[str, DataSource],
                 enrich: bool = False, client=None,
                 gl_mapping: Optional[Dict[str, str]] = None,
                 group_column: Optional[str] = None,
                 tolerance: Optional[float] = None,
                 rate_map: Optional[Dict[str, str]] = None) -> ReconResult:
        if rule_id not in self.rules:
            raise KeyError(f"Unknown rule '{rule_id}'. Available: {self.list_rules()}")
        result = dispatch(self.rules[rule_id], sources, tolerance=tolerance,
                          rate_map=rate_map)
        if enrich:
            result = self.enrich(result, client=client, gl_mapping=gl_mapping,
                                 group_column=group_column)
        return result

    def run_all(self, sources_per_rule: Dict[str, Dict[str, DataSource]],
                enrich: bool = False, **kw) -> Dict[str, ReconResult]:
        """Run several rules at once; sources_per_rule maps rule_id -> its sources."""
        return {rid: self.run_rule(rid, src, enrich=enrich, **kw)
                for rid, src in sources_per_rule.items()}

    # -- GenAI enrichment ------------------------------------------------
    @staticmethod
    def enrich(result: ReconResult, client=None,
               gl_mapping: Optional[Dict[str, str]] = None,
               group_column: Optional[str] = None,
               do_classify: bool = True, do_journals: bool = True,
               do_anomalies: bool = True) -> ReconResult:
        """Run the GenAI passes over a finished ReconResult (in place)."""
        from ai import classify_breaks, generate_journals, detect_anomalies
        if do_classify:
            classify_breaks(result, client=client)         # root cause + commentary
        if do_anomalies:
            detect_anomalies(result, group_column=group_column, client=client)
        if do_journals:
            generate_journals(result, gl_mapping=gl_mapping, client=client)
        return result

    # -- reporting -------------------------------------------------------
    @staticmethod
    def summary_table(results: Dict[str, ReconResult]) -> pd.DataFrame:
        rows = []
        for rid, res in results.items():
            row = {"rule": rid, "type": res.recon_type.value, "key": res.recon_key}
            row.update(res.summary)
            if res.confidence < 1.0:
                row["confidence"] = res.confidence
            rows.append(row)
        return pd.DataFrame(rows)


def build_config_interactively(wrapper: ReconWrapper) -> dict:
    """
    Minimal prompt-driven config collector reflecting the requested user inputs:
    type of reconciliation -> available rules -> chosen rule -> number of sources.
    Returns a plain dict describing what to run (data still supplied separately).
    """
    print("Reconciliation types:", [t.value for t in ReconType])
    rtype = input("Type of reconciliation: ").strip().lower()
    recon_type = ReconType(rtype)

    rules = wrapper.list_rules(recon_type)
    print(f"Available {rtype} rules: {rules}")
    rule_id = input("Choose rule id: ").strip().upper()

    spec = wrapper.rules[rule_id]
    print(f"Rule {rule_id} expects {len(spec.required_roles)} sources: "
          f"{spec.required_roles}")
    n = int(input("Number of data sources you will provide: ").strip())

    return {"recon_type": recon_type.value, "rule_id": rule_id,
            "num_sources": n, "expected_sources": spec.required_roles}


def build_config_with_ai(frames: Dict[str, pd.DataFrame], client=None) -> dict:
    """
    Feedback #3 — AI-driven alternative to build_config_interactively().

    Inspects the headers + sample rows of each uploaded feed, detects what each
    one is, guesses key/amount/narration columns, and recommends a rule and a
    matching strategy. Returns the detection plus ready-to-run DataSources.
    """
    from ai import detect_sources
    return detect_sources(frames, client=client)
