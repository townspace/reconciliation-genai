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
# R4  —  OMS-B2B vs channel-partner transactions (EXACT)
# ---------------------------------------------------------------------------
register(RuleSpec(
    id="R4",
    label="R4 — OMS-B2B vs channel-partner txns (exact)",
    description=("OMS B2B order amounts must match the channel-partner "
                 "transaction report on the shared order id"),
    mode="exact_key",
    recon_key="order_id",
    feeds=[
        FeedSpec("oms_b2b", "OMS B2B orders"),
        FeedSpec("channel_partner", "Channel-partner transactions"),
    ],
))

# ---------------------------------------------------------------------------
# R5  —  Internal wallet vs PG/EDC report (EXACT, report-to-report)
# ---------------------------------------------------------------------------
register(RuleSpec(
    id="R5",
    label="R5 — Internal wallet vs PG/EDC (exact)",
    description=("Internal wallet report reconciled to the PG/EDC report on the "
                 "shared transaction id"),
    mode="exact_key",
    recon_key="txn_id",
    feeds=[
        FeedSpec("internal_wallet", "Internal wallet report"),
        FeedSpec("pg_edc", "PG/EDC report"),
    ],
))

# ---------------------------------------------------------------------------
# R6  —  PG/EDC vs internal subscription report (EXACT, report-to-report)
# ---------------------------------------------------------------------------
register(RuleSpec(
    id="R6",
    label="R6 — PG/EDC vs internal subscription (exact)",
    description=("PG/EDC report reconciled to the internal subscription report "
                 "on the shared subscription id"),
    mode="exact_key",
    recon_key="sub_id",
    feeds=[
        FeedSpec("pg_edc", "PG/EDC report"),
        FeedSpec("subscription", "Internal subscription report"),
    ],
))


# ===========================================================================
# Mode 2 — tolerance + timing (fees + value-date lag)
# ===========================================================================

# ---------------------------------------------------------------------------
# R8  —  PG/EDC transactions vs PG/EDC settlement (TOLERANCE_TIMING)
# ---------------------------------------------------------------------------
register(RuleSpec(
    id="R8",
    label="R8 — PG/EDC txn vs settlement (fees + timing)",
    description=("PG/EDC transactions reconciled to the provider settlement, "
                 "allowing a processing fee and a value-date lag"),
    mode="tolerance_timing",
    recon_key="txn_id",
    feeds=[
        FeedSpec("pg_txn", "PG/EDC transactions (gross)", date=True),
        FeedSpec("pg_settlement", "PG/EDC settlement (net of fee)", date=True),
    ],
    tolerance=0.01,
    fee_tolerance_pct=3.0,     # processing fee up to ~3%
    date_window=2,             # settlement may lag up to 2 days
))

# ---------------------------------------------------------------------------
# R7  —  Internal wallet vs wallet provider (TOLERANCE_TIMING)
# ---------------------------------------------------------------------------
register(RuleSpec(
    id="R7",
    label="R7 — Internal wallet vs wallet provider (fees + timing)",
    description=("Internal wallet ledger reconciled to the wallet provider report, "
                 "allowing a provider fee and a settlement-date lag"),
    mode="tolerance_timing",
    recon_key="wallet_txn_id",
    feeds=[
        FeedSpec("wallet_internal", "Internal wallet ledger", date=True),
        FeedSpec("wallet_provider", "Wallet provider report", date=True),
    ],
    tolerance=0.01,
    fee_tolerance_pct=2.5,
    date_window=3,
))


# ===========================================================================
# Mode 3 — rate validation (expected charge from a rate master)
# ===========================================================================

# ---------------------------------------------------------------------------
# R13  —  B2C service-charge master vs PG/EDC settlement (RATE_VALIDATION)
# ---------------------------------------------------------------------------
register(RuleSpec(
    id="R13",
    label="R13 — Service-charge validation (PG/EDC settlement vs rate master)",
    description=("Validate the B2C service charge on each PG/EDC settlement against "
                 "the service-charge rate master (expected = base × rate%)"),
    mode="rate_validation",
    recon_key="txn_id",
    feeds=[
        FeedSpec("settlement", "PG/EDC settlement (actual charge)",
                 extra=[("Base amount column", "base_column", ["base_amount"]),
                        ("Rate lookup column", "lookup_key", ["charge_type"])]),
        FeedSpec("rate_master", "Service-charge rate master"),
    ],
    rate_map={"base_column": "base_amount", "lookup_key": "charge_type",
              "rate_is_pct": True},
    tolerance=0.01,
))

# ---------------------------------------------------------------------------
# R11  —  Merchant commission master vs channel-partner txns (RATE_VALIDATION)
# ---------------------------------------------------------------------------
register(RuleSpec(
    id="R11",
    label="R11 — Commission validation (channel-partner txns vs rate master)",
    description=("Validate merchant commission on channel-partner transactions "
                 "against the commission rate master (expected = base × rate%)"),
    mode="rate_validation",
    recon_key="txn_id",
    feeds=[
        FeedSpec("channel_partner", "Channel-partner txns (actual commission)",
                 extra=[("Base amount column", "base_column", ["base_amount"]),
                        ("Rate lookup column", "lookup_key", ["merchant_id"])]),
        FeedSpec("commission_master", "Merchant commission rate master"),
    ],
    rate_map={"base_column": "base_amount", "lookup_key": "merchant_id",
              "rate_is_pct": True},
    tolerance=0.01,
))


# ===========================================================================
# Mode 4 — N:1 aggregation / bank reco (group + sum -> one bank line)
# ===========================================================================

# ---------------------------------------------------------------------------
# R12  —  Settlement batch vs bank statement (AGGREGATE_MATCH)
# ---------------------------------------------------------------------------
register(RuleSpec(
    id="R12",
    label="R12 — Settlement batch vs bank credit (N:1)",
    description=("Many settlement postings in a batch roll up to a single bank "
                 "credit; group by batch and match the total to the bank line"),
    mode="aggregate_match",
    recon_key="batch_id",
    feeds=[
        FeedSpec("settlement", "Settlement postings (many per batch)"),
        FeedSpec("bank", "Bank statement (one credit per batch)"),
    ],
    group_by="batch_id",
    tolerance=0.01,
))

# ---------------------------------------------------------------------------
# R9  —  Cash deposit + CMS vs bank statement (AGGREGATE_MATCH)
# ---------------------------------------------------------------------------
register(RuleSpec(
    id="R9",
    label="R9 — Cash/CMS deposits vs bank credit (N:1)",
    description=("Cash deposit / CMS collection slips grouped by deposit batch and "
                 "matched to the single bank credit for that batch"),
    mode="aggregate_match",
    recon_key="deposit_batch",
    feeds=[
        FeedSpec("cms", "Cash/CMS deposit slips (many per batch)"),
        FeedSpec("bank", "Bank statement (one credit per batch)"),
    ],
    group_by="deposit_batch",
    tolerance=0.01,
))

# ---------------------------------------------------------------------------
# R10  —  Channel-partner txns vs bank statement (AGGREGATE_MATCH)
# ---------------------------------------------------------------------------
register(RuleSpec(
    id="R10",
    label="R10 — Channel-partner txns vs bank credit (N:1)",
    description=("Channel-partner transactions grouped by remittance batch and "
                 "matched to the single bank remittance credit"),
    mode="aggregate_match",
    recon_key="remittance_id",
    feeds=[
        FeedSpec("channel_txn", "Channel-partner txns (many per remittance)"),
        FeedSpec("bank", "Bank statement (one credit per remittance)"),
    ],
    group_by="remittance_id",
    tolerance=0.01,
))


# ---------------------------------------------------------------------------
# R14 — TODO. The build-plan diagram defines R1–R13 only; there is no R14 on it.
# Not inventing behaviour. If a 14th rule is later specified, register it here
# with its mode and feeds rather than guessing.
# ---------------------------------------------------------------------------


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
