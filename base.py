"""
base.py
-------
Shared building blocks for the reconciliation wrapper model.

Every "mini model" (one per rule, R1..R14) is built on top of these types.
The top-level wrapper orchestrates many mini models using a common contract:

    mini_model.run(data: dict[str, DataSource]) -> ReconResult

GenAI extension
---------------
ReconResult now carries optional AI-generated outputs so any engine or the
wrapper can enrich a result without changing the contract:

  - ai_commentary : str    overall, audit-ready narrative for the result
  - confidence    : float  mean match confidence (1.0 for pure exact matching)
  - journals      : DataFrame of draft adjustment journal entries (optional)
  - anomalies     : DataFrame of statistically flagged breaks (optional)

Per-break AI fields (ai_root_cause, ai_category, ai_commentary, ai_confidence)
are attached as columns on the `breaks` / `detail` frames by ai/classify.py.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

import pandas as pd


class ReconType(str, Enum):
    """The kind of reconciliation a rule performs."""
    TRANSACTIONAL = "transactional"   # row-by-row match on a key
    SUMMARY = "summary"               # aggregate first, then compare


class MatchStrategy(str, Enum):
    """How a rule decides two records correspond."""
    EXACT = "exact"                   # equal recon key (+ amount tolerance)
    SEMANTIC = "semantic"             # embedding similarity on narration
    ONE_TO_MANY = "one_to_many"       # combinatorial sum matching


class MatchStatus(str, Enum):
    """Outcome of a single reconciled record."""
    MATCHED = "MATCHED"                       # key present both sides, amounts equal
    SEMANTIC_MATCH = "SEMANTIC_MATCH"         # matched by narration similarity, not key
    ONE_TO_MANY_MATCH = "ONE_TO_MANY_MATCH"   # one record matched to a sum of several
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"       # key present both sides, amounts differ
    MISSING_IN_RIGHT = "MISSING_IN_RIGHT"     # key only in the left (primary) source
    MISSING_IN_LEFT = "MISSING_IN_LEFT"       # key only in the right (secondary) source


# Statuses that count as a successful reconciliation (not a break).
MATCHED_STATUSES = {
    MatchStatus.MATCHED.value,
    MatchStatus.SEMANTIC_MATCH.value,
    MatchStatus.ONE_TO_MANY_MATCH.value,
}


@dataclass
class DataSource:
    """
    A single input feed for a rule.

    role             : logical name the rule refers to it by (e.g. "oms_pos", "wallet")
    df               : the data as a pandas DataFrame
    key_column       : column holding the recon key (e.g. order_id)
    amount_column    : column holding the value to be reconciled
    narration_column : optional free-text column (narration / description) used by
                       semantic matching. Safe to leave None for exact rules.
    """
    role: str
    df: pd.DataFrame
    key_column: str
    amount_column: str
    narration_column: Optional[str] = None

    def normalized(self) -> pd.DataFrame:
        """Return a 2-column frame [recon_key, <role>_amount] with a clean, typed key."""
        out = self.df[[self.key_column, self.amount_column]].copy()
        out.columns = ["recon_key", f"{self.role}_amount"]
        out["recon_key"] = out["recon_key"].astype(str).str.strip()
        return out

    def normalized_with_narration(self) -> pd.DataFrame:
        """
        Like normalized() but also carries a <role>_narration column.

        If this source has no narration_column, the narration falls back to the
        recon key so semantic matching still has *something* to compare.
        """
        out = self.normalized()
        if self.narration_column and self.narration_column in self.df.columns:
            narr = self.df[self.narration_column].astype(str).str.strip()
        else:
            narr = out["recon_key"]
        out[f"{self.role}_narration"] = narr.reset_index(drop=True)
        return out


@dataclass
class ReconResult:
    """Output of one mini model run."""
    rule_id: str
    description: str
    recon_type: ReconType
    recon_key: str
    breaks: pd.DataFrame = field(default_factory=pd.DataFrame)   # all non-matched records
    detail: pd.DataFrame = field(default_factory=pd.DataFrame)   # every record + status
    summary: Dict[str, float] = field(default_factory=dict)

    # -- GenAI-generated outputs (populated by the ai/* modules; optional) ----
    ai_commentary: str = ""                                      # overall narrative
    confidence: float = 1.0                                      # mean match confidence
    journals: pd.DataFrame = field(default_factory=pd.DataFrame) # draft adjustments
    anomalies: pd.DataFrame = field(default_factory=pd.DataFrame)# flagged outliers

    def is_clean(self) -> bool:
        return len(self.breaks) == 0

    def __repr__(self) -> str:
        s = self.summary
        conf = f" conf={self.confidence:.2f}" if self.confidence < 1.0 else ""
        return (
            f"<ReconResult {self.rule_id} | {self.recon_type.value} | key={self.recon_key} | "
            f"matched={s.get('matched', 0)} breaks={s.get('total_breaks', 0)}{conf}>"
        )


class BaseReconModel(ABC):
    """
    Contract every rule's mini model must satisfy.

    Subclasses declare metadata and implement run().
    The wrapper never needs to know which concrete rule it is calling.
    """
    rule_id: str
    description: str
    recon_type: ReconType
    recon_key: str
    required_sources: List[str]                 # logical roles this rule needs in `data`
    matching_strategy: str = MatchStrategy.EXACT.value  # "exact" | "semantic" | "one_to_many"

    def _check_inputs(self, data: Dict[str, DataSource]) -> None:
        missing = [r for r in self.required_sources if r not in data]
        if missing:
            raise ValueError(
                f"[{self.rule_id}] missing required data sources: {missing}. "
                f"Provided: {list(data.keys())}"
            )

    @abstractmethod
    def run(self, data: Dict[str, DataSource]) -> ReconResult:
        ...
