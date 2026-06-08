"""
rules.py
--------
The rule registry contents. Each rule is now a declarative `RuleSpec` (see
registry.py), not a hand-written class. Importing this module registers every
rule via `register()`.

Adding a rule for an EXISTING mode is just a `register(RuleSpec(...))` call —
no engine code. New modes are added in registry.py (`MODE_ENGINES`).

Backwards compatibility: `RULE_REGISTRY` is kept as an alias of the spec
registry so any external caller importing it still works.
"""

from __future__ import annotations

from base import ReconType
from registry import RULES as RULE_REGISTRY  # noqa: F401  (back-compat alias)
from registry import FeedSpec, RuleSpec, register

# ---------------------------------------------------------------------------
# R1  —  OMS/POS wallet utilization vs internal wallet transaction (EXACT)
# ---------------------------------------------------------------------------
register(RuleSpec(
    id="R1",
    label="R1 — Exact match (OMS/POS wallet vs internal wallet)",
    description=("Wallet amount utilized (OMS/POS) must match transaction amount "
                 "(internal wallet) using Order ID as the recon key"),
    mode="exact_key",
    recon_key="order_id",
    recon_type=ReconType.TRANSACTIONAL,
    feeds=[
        FeedSpec("oms_pos", "Primary feed (e.g. OMS/POS export)"),
        FeedSpec("wallet", "Secondary feed (e.g. internal wallet)"),
    ],
))

# ---------------------------------------------------------------------------
# R2  —  Bank statement vs GL (SEMANTIC, narration-based)
# ---------------------------------------------------------------------------
register(RuleSpec(
    id="R2",
    label="R2 — Semantic match (bank narration vs GL description)",
    description=("Bank statement lines vs GL postings, matched semantically on "
                 "narration/description when reference keys do not align"),
    mode="semantic",
    recon_key="narration",
    recon_type=ReconType.TRANSACTIONAL,
    feeds=[
        FeedSpec("bank", "Bank statement lines", narration=True),
        FeedSpec("gl", "GL postings", narration=True),
    ],
    # Threshold is embedder-dependent; ~0.40 suits the offline LocalEmbedder.
    sim_threshold=0.40,
    amount_tolerance=0.01,
))

# ---------------------------------------------------------------------------
# R3  —  Split settlements vs single order (ONE-TO-MANY)
# ---------------------------------------------------------------------------
register(RuleSpec(
    id="R3",
    label="R3 — One-to-many (split settlements summing to one order)",
    description=("Single OMS order amount reconciled to a combination of split "
                 "wallet/PG settlement postings that sum to it"),
    mode="one_to_many",
    recon_key="order_id",
    recon_type=ReconType.TRANSACTIONAL,
    feeds=[
        FeedSpec("oms_pos", "Single side (orders)"),
        FeedSpec("wallet", "Split side (postings)"),
    ],
))


# ---------------------------------------------------------------------------
# Template for the next rule (copy, adapt, register):
#
# register(RuleSpec(
#     id="R4",
#     label="R4 — ...",
#     description="...",
#     mode="exact_key",          # or semantic / one_to_many / tolerance_timing /
#                                #    rate_validation / aggregate_match
#     recon_key="payment_id",
#     feeds=[FeedSpec("oms_b2b", "..."), FeedSpec("channel_partner", "...")],
# ))
# ---------------------------------------------------------------------------
