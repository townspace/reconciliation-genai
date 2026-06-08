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
    date_window: Optional[int] = None          # tolerance_timing (calendar days)
    date_map: Optional[Dict[str, str]] = None  # tolerance_timing: role -> date column
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


# Dispatcher. New modes register themselves here in later phases.
MODE_ENGINES: Dict[str, Callable[[RuleSpec, dict], ReconResult]] = {
    "exact_key": _exact_key,
    "semantic": _semantic,
    "one_to_many": _one_to_many,
}


def dispatch(spec: RuleSpec, sources: dict) -> ReconResult:
    """Validate inputs and run the engine for this rule's mode."""
    _require(spec, sources)
    engine = MODE_ENGINES.get(spec.mode)
    if engine is None:
        raise ValueError(f"No engine registered for mode '{spec.mode}'")
    return engine(spec, sources)
