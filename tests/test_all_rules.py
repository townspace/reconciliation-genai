"""
Phase 6 — every registered rule is selectable and runnable on sample data.

Builds DataSources from each rule's sample frames the same way the UI does
(column guessing), runs the rule, and asserts a sensible result. Also pins the
expected rule set so a missing/extra registration is caught.
"""

import pytest

from base import DataSource
from registry import all_specs
from samples import sample_data
from wrapper import ReconWrapper

KEY_CANDS = ["batch_id", "deposit_batch", "remittance_id", "txn_id", "wallet_txn_id",
             "order_id", "sub_id", "posting_id", "bank_ref", "journal_id",
             "charge_type", "merchant_id"]
AMT_CANDS = ["wallet_amount_utilized", "gross_amount", "net_amount", "credit_amount",
             "actual_charge", "actual_commission", "rate_pct", "transaction_amount",
             "amount"]
DATE_CANDS = ["txn_date", "settle_date", "date", "value_date", "posting_date"]
NARR_CANDS = ["narration", "description"]

EXPECTED_RULES = {"R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9", "R10",
                  "R11", "R12", "R13"}


def _first(cols, cands):
    for c in cands:
        if c in cols:
            return c
    return cols[0]


def _build_sources(spec):
    frames = sample_data(spec.id)
    rate_map = dict(spec.rate_map or {})
    sources = {}
    for feed in spec.feeds:
        df = frames[feed.role]
        cols = list(df.columns)
        narr = _first(cols, NARR_CANDS) if feed.narration else None
        date = _first(cols, DATE_CANDS) if feed.date else None
        for _label, rkey, cands in feed.extra:
            rate_map[rkey] = _first(cols, cands)
        sources[feed.role] = DataSource(
            feed.role, df, _first(cols, KEY_CANDS), _first(cols, AMT_CANDS),
            narration_column=narr, date_column=date)
    return sources, rate_map


def test_expected_rule_set_registered():
    assert set(all_specs()) == EXPECTED_RULES
    assert len(EXPECTED_RULES) == 13


@pytest.mark.parametrize("rule_id", sorted(EXPECTED_RULES))
def test_rule_runs_on_sample_data(rule_id):
    spec = all_specs()[rule_id]
    sources, rate_map = _build_sources(spec)
    result = ReconWrapper().run_rule(
        rule_id, sources, rate_map=rate_map or None)
    # sensible output: a populated detail frame and a coherent summary
    assert len(result.detail) > 0
    assert "total_breaks" in result.summary
    assert result.recon_key == spec.recon_key


def test_every_rule_has_sample_data():
    for rule_id, spec in all_specs().items():
        frames = sample_data(rule_id)
        for feed in spec.feeds:
            assert feed.role in frames, f"{rule_id} missing sample for '{feed.role}'"
