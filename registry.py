"""
registry.py
-----------
Declarative rule registry + mode dispatcher (Phase 1).

A rule is no longer a hand-written class; it is a `RuleSpec` data record that
names a **mode** (the matching algorithm) plus the configuration that mode needs.
`MODE_ENGINES` maps each mode to a function `(spec, sources) -> ReconResult`, so
adding a rule for an existing mode is just a registry entry.

Modes
-----
  exact_key        : 1:1 key join, amount equality (± tolerance)   [R1, R4, R5, R6]
  semantic         : narration-similarity match                    [R2]
  one_to_many      : combinatorial sum match                       [R3]
  tolerance_timing : key join allowing fee + value-date lag        [R7, R8]   (Phase 3)
  rate_validation  : expected fee from a rate master vs actual      [R11, R13] (Phase 4)
  aggregate_match  : group/sum many txns, match to one bank line    [R9,R10,R12](Phase 5)

The rule *records* themselves live in `rules.py`; importing that module
registers them via `register()`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from base import ReconResult, ReconType


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
@dataclass
class FeedSpec:
    """One input feed a rule expects, by logical role."""
    role: str
    hint: str = ""
    narration: bool = False     # does this feed carry a free-text narration?
    date: bool = False          # does this feed carry a value/posting date?
    # Extra column pickers for modes that need more than key/amount, as
    # (label, rate_map_key, [candidate column names]) — e.g. rate_validation's
    # base amount and rate-lookup key.
    extra: list = field(default_factory=list)


@dataclass
class RuleSpec:
    """Declarative description of a rule. Fields unused by a mode stay None."""
    id: str
    label: str
    description: str
    mode: str
    recon_key: str
    feeds: List[FeedSpec]
    recon_type: ReconType = ReconType.TRANSACTIONAL
    # cross-mode knobs (only the relevant ones are read by each engine)
    tolerance: float = 0.01
    amount_tolerance: Optional[float] = None   # semantic / tolerance_timing
    sim_threshold: Optional[float] = None      # semantic
    fee_tolerance: Optional[float] = None       # tolerance_timing: absolute fee allowance
    fee_tolerance_pct: Optional[float] = None   # tolerance_timing: % fee allowance
    date_window: Optional[int] = None          # tolerance_timing (calendar days)
    group_by: Optional[str] = None             # aggregate_match: left date/batch column
    rate_map: Optional[Dict[str, str]] = None  # rate_validation: lookup + formula config
    notes: str = ""

    @property
    def required_roles(self) -> List[str]:
        return [f.role for f in self.feeds]

    @property
    def left_role(self) -> str:
        return self.feeds[0].role

    @property
    def right_role(self) -> Optional[str]:
        return self.feeds[1].role if len(self.feeds) > 1 else None


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
RULES: Dict[str, RuleSpec] = {}


def register(spec: RuleSpec) -> RuleSpec:
    if spec.id in RULES:
        raise ValueError(f"Duplicate rule id: {spec.id}")
    if spec.mode not in MODE_ENGINES:
        raise ValueError(f"Rule {spec.id} uses unknown mode '{spec.mode}'. "
                         f"Known: {sorted(MODE_ENGINES)}")
    RULES[spec.id] = spec
    return spec


def _ensure_loaded() -> None:
    """Import the rule records (side-effecting registration) exactly once."""
    if not RULES:
        import rules  # noqa: F401  (registers RuleSpec records)


def all_specs() -> Dict[str, RuleSpec]:
    _ensure_loaded()
    return RULES


# ---------------------------------------------------------------------------
# Mode engines: (spec, sources) -> ReconResult
# ---------------------------------------------------------------------------
def _require(spec: RuleSpec, sources: dict) -> None:
    missing = [r for r in spec.required_roles if r not in sources]
    if missing:
        raise ValueError(
            f"[{spec.id}] missing required data sources: {missing}. "
            f"Provided: {list(sources.keys())}")


def _exact_key(spec: RuleSpec, sources: dict) -> ReconResult:
    from engines import transactional_recon
    return transactional_recon(
        rule_id=spec.id, description=spec.description, recon_key=spec.recon_key,
        left=sources[spec.left_role], right=sources[spec.right_role],
        tolerance=spec.tolerance,
    )


def _semantic(spec: RuleSpec, sources: dict) -> ReconResult:
    from engines import semantic_recon
    return semantic_recon(
        rule_id=spec.id, description=spec.description, recon_key=spec.recon_key,
        left=sources[spec.left_role], right=sources[spec.right_role],
        sim_threshold=spec.sim_threshold if spec.sim_threshold is not None else 0.40,
        amount_tolerance=spec.amount_tolerance if spec.amount_tolerance is not None
        else spec.tolerance,
    )


def _one_to_many(spec: RuleSpec, sources: dict) -> ReconResult:
    from engines import one_to_many_recon
    return one_to_many_recon(
        rule_id=spec.id, description=spec.description, recon_key=spec.recon_key,
        one=sources[spec.left_role], many=sources[spec.right_role],
    )


def _rate_validation(spec: RuleSpec, sources: dict) -> ReconResult:
    from engines import rate_validation_recon
    return rate_validation_recon(
        rule_id=spec.id, description=spec.description, recon_key=spec.recon_key,
        txn=sources[spec.left_role], rate_master=sources[spec.right_role],
        rate_map=spec.rate_map or {}, tolerance=spec.tolerance,
    )


def _tolerance_timing(spec: RuleSpec, sources: dict) -> ReconResult:
    from engines import tolerance_timing_recon
    return tolerance_timing_recon(
        rule_id=spec.id, description=spec.description, recon_key=spec.recon_key,
        left=sources[spec.left_role], right=sources[spec.right_role],
        tolerance=spec.tolerance,
        fee_tolerance=spec.fee_tolerance or 0.0,
        fee_tolerance_pct=spec.fee_tolerance_pct or 0.0,
        date_window=spec.date_window if spec.date_window is not None else 2,
    )


# Dispatcher. New modes register themselves here in later phases.
MODE_ENGINES: Dict[str, Callable[[RuleSpec, dict], ReconResult]] = {
    "exact_key": _exact_key,
    "semantic": _semantic,
    "one_to_many": _one_to_many,
    "tolerance_timing": _tolerance_timing,
    "rate_validation": _rate_validation,
}


# Generalised result columns (Phase 2). Every mode produces these so the UI and
# later pipeline have a stable schema; a mode leaves the ones it does not use
# empty, and the UI hides all-empty columns.
GENERALISED_COLUMNS = [
    "expected_amount",   # rate_validation: computed from a rate master
    "actual_amount",     # rate_validation / tolerance_timing: observed value
    "break_reason",      # human-readable decomposition of a break
    "computed_from",     # provenance note (e.g. "base x rate")
]


def _ensure_schema(result: ReconResult) -> ReconResult:
    """Add the generalised columns (empty) to detail/breaks if a mode omitted them.

    Uses pandas.NA so the UI's drop-empty logic hides untouched columns; existing
    columns (recon_key, status, difference, <role>_amount, ...) are left intact,
    so exact_key output is unchanged.
    """
    import pandas as pd
    for frame_name in ("detail", "breaks"):
        df = getattr(result, frame_name)
        if df is None or not len(df.columns):
            continue
        for col in GENERALISED_COLUMNS:
            if col not in df.columns:
                df[col] = pd.NA
    return result


def dispatch(spec: RuleSpec, sources: dict, tolerance: Optional[float] = None,
             rate_map: Optional[Dict[str, str]] = None) -> ReconResult:
    """Validate inputs and run the engine for this rule's mode.

    `tolerance` (if given) overrides the rule's configured tolerance for this run.
    `rate_map` (if given) is merged over the rule's rate_map, so the UI can remap
    rate-validation columns for uploaded data — neither mutates the registry.
    """
    _require(spec, sources)
    overrides = {}
    if tolerance is not None:
        overrides["tolerance"] = tolerance
    if rate_map:
        overrides["rate_map"] = {**(spec.rate_map or {}), **rate_map}
    if overrides:
        from dataclasses import replace
        spec = replace(spec, **overrides)
    engine = MODE_ENGINES.get(spec.mode)
    if engine is None:
        raise ValueError(f"No engine registered for mode '{spec.mode}'")
    return _ensure_schema(engine(spec, sources))
