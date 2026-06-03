"""
rules.py
--------
The per-rule "mini models". Each rule is a small class that:
  - declares its metadata (id, type, key, required sources, matching_strategy)
  - maps the wrapper's generic inputs onto an engine call

Strategies (see base.MatchStrategy):
  - "exact"        -> transactional_recon   (R1)
  - "semantic"     -> semantic_recon        (R2: bank narration vs GL description)
  - "one_to_many"  -> one_to_many_recon     (R3: split settlements vs single order)

Adding the next rule stays a copy-configure-register job; the engine is chosen
by the rule's matching_strategy.
"""

from __future__ import annotations

from typing import Dict, Type

from base import BaseReconModel, DataSource, MatchStrategy, ReconResult, ReconType
from engines import transactional_recon, semantic_recon, one_to_many_recon


# ---------------------------------------------------------------------------
# Rule registry: the wrapper discovers available mini models from here.
# ---------------------------------------------------------------------------
RULE_REGISTRY: Dict[str, Type[BaseReconModel]] = {}


def register(cls: Type[BaseReconModel]) -> Type[BaseReconModel]:
    RULE_REGISTRY[cls.rule_id] = cls
    return cls


# ---------------------------------------------------------------------------
# R1  —  OMS/POS wallet utilization vs Internal wallet transaction (EXACT)
# ---------------------------------------------------------------------------
@register
class R1WalletPOSRecon(BaseReconModel):
    """
    R1: Wallet amount utilized from the OMS/POS report must match the
    transaction amount from the internal wallet report, on Order ID.
    """
    rule_id = "R1"
    description = (
        "Wallet amount utilized (OMS/POS) must match transaction amount "
        "(internal wallet) using Order ID as the recon key"
    )
    recon_type = ReconType.TRANSACTIONAL
    recon_key = "order_id"
    required_sources = ["oms_pos", "wallet"]
    matching_strategy = MatchStrategy.EXACT.value

    def run(self, data: Dict[str, DataSource]) -> ReconResult:
        self._check_inputs(data)
        return transactional_recon(
            rule_id=self.rule_id,
            description=self.description,
            recon_key=self.recon_key,
            left=data["oms_pos"],   # primary
            right=data["wallet"],   # secondary
        )


# ---------------------------------------------------------------------------
# R2  —  Bank statement vs GL (SEMANTIC, narration-based)
# ---------------------------------------------------------------------------
@register
class R2BankGLRecon(BaseReconModel):
    """
    R2: Bank statement lines must reconcile to GL postings. Keys (bank ref vs
    journal id) rarely align, so matching is on narration similarity + amount.
    """
    rule_id = "R2"
    description = (
        "Bank statement lines vs GL postings, matched semantically on "
        "narration/description when reference keys do not align"
    )
    recon_type = ReconType.TRANSACTIONAL
    recon_key = "narration"
    required_sources = ["bank", "gl"]
    matching_strategy = MatchStrategy.SEMANTIC.value

    def run(self, data: Dict[str, DataSource]) -> ReconResult:
        self._check_inputs(data)
        return semantic_recon(
            rule_id=self.rule_id,
            description=self.description,
            recon_key=self.recon_key,
            left=data["bank"],      # primary
            right=data["gl"],       # secondary
            # Threshold is embedder-dependent. The offline LocalEmbedder scores
            # true pairs ~0.43-0.72 with a ~0.06 noise floor, so 0.40 sits in the
            # gap (well above noise, below every true match). A hosted embedder
            # (Voyage/OpenAI) scores higher — raise to ~0.8 there.
            sim_threshold=0.40,
            amount_tolerance=0.01,
        )


# ---------------------------------------------------------------------------
# R3  —  Split settlements vs single order (ONE-TO-MANY)
# ---------------------------------------------------------------------------
@register
class R3SplitSettlementRecon(BaseReconModel):
    """
    R3: A single OMS order can be settled by several wallet/PG postings. Match
    one order to the COMBINATION of postings that sum to it.
    """
    rule_id = "R3"
    description = (
        "Single OMS order amount reconciled to a combination of split "
        "wallet/PG settlement postings that sum to it"
    )
    recon_type = ReconType.TRANSACTIONAL
    recon_key = "order_id"
    required_sources = ["oms_pos", "wallet"]
    matching_strategy = MatchStrategy.ONE_TO_MANY.value

    def run(self, data: Dict[str, DataSource]) -> ReconResult:
        self._check_inputs(data)
        return one_to_many_recon(
            rule_id=self.rule_id,
            description=self.description,
            recon_key=self.recon_key,
            one=data["oms_pos"],    # the single side
            many=data["wallet"],    # the split side
            max_group_size=4,
        )


# ---------------------------------------------------------------------------
# Template for the next rule (copy, adapt, register):
#
# @register
# class R4MerchantPGRecon(BaseReconModel):
#     rule_id = "R4"
#     description = "..."
#     recon_type = ReconType.TRANSACTIONAL          # or ReconType.SUMMARY
#     recon_key = "payment_id"
#     required_sources = ["oms_pos", "pg_edc"]
#     matching_strategy = MatchStrategy.EXACT.value # or SEMANTIC / ONE_TO_MANY
#
#     def run(self, data):
#         self._check_inputs(data)
#         return transactional_recon(
#             rule_id=self.rule_id, description=self.description,
#             recon_key=self.recon_key,
#             left=data["oms_pos"], right=data["pg_edc"],
#         )
# ---------------------------------------------------------------------------
