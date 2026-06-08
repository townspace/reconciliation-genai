"""
Phase 5 — Mode 4: N:1 aggregation / bank reco (R9, R10, R12).

Asserts many transactions reconcile to one bank credit, partial matches are
flagged, and unallocated groups / unmatched bank lines surface as breaks.
"""

from base import DataSource
from samples import sample_data
from wrapper import ReconWrapper


def _r12_sources():
    f = sample_data("R12")
    return {
        "settlement": DataSource("settlement", f["settlement"], "batch_id", "amount"),
        "bank": DataSource("bank", f["bank"], "batch_id", "credit_amount"),
    }


def test_r12_aggregate_match_and_breaks():
    result = ReconWrapper().run_rule("R12", _r12_sources())
    by_key = {r["recon_key"]: r for _, r in result.detail.iterrows()}

    assert by_key["B1"]["status"] == "AGGREGATE_MATCH"   # 100+250+150 == 500
    assert by_key["B1"]["group_size"] == 3
    assert by_key["B2"]["status"] == "AMOUNT_MISMATCH"   # 500 vs 450 (partial)
    assert by_key["B3"]["status"] == "MISSING_IN_RIGHT"  # unallocated group
    assert by_key["B4"]["status"] == "MISSING_IN_LEFT"   # unmatched bank credit


def test_r12_summary_surfaces_unmatched():
    s = ReconWrapper().run_rule("R12", _r12_sources()).summary
    assert s["aggregate_matched"] == 1
    assert s["amount_mismatch"] == 1
    assert s["unallocated_groups"] == 1
    assert s["unmatched_bank_lines"] == 1
    assert s["total_breaks"] == 3


def test_r12_breaks_not_hidden():
    result = ReconWrapper().run_rule("R12", _r12_sources())
    break_keys = set(result.breaks["recon_key"])
    assert {"B2", "B3", "B4"} == break_keys
    assert "B1" not in break_keys


def test_r9_and_r10_run():
    f9 = sample_data("R9")
    r9 = ReconWrapper().run_rule("R9", {
        "cms": DataSource("cms", f9["cms"], "deposit_batch", "amount"),
        "bank": DataSource("bank", f9["bank"], "deposit_batch", "credit_amount"),
    })
    by9 = {r["recon_key"]: r["status"] for _, r in r9.detail.iterrows()}
    assert by9["D1"] == "AGGREGATE_MATCH"      # 1000+500 == 1500
    assert by9["D2"] == "AGGREGATE_MATCH"      # 750+750 == 1500
    assert by9["D3"] == "MISSING_IN_RIGHT"     # group, no bank
    assert by9["D4"] == "MISSING_IN_LEFT"      # bank, no group

    f10 = sample_data("R10")
    r10 = ReconWrapper().run_rule("R10", {
        "channel_txn": DataSource("channel_txn", f10["channel_txn"],
                                  "remittance_id", "amount"),
        "bank": DataSource("bank", f10["bank"], "remittance_id", "credit_amount"),
    })
    by10 = {r["recon_key"]: r["status"] for _, r in r10.detail.iterrows()}
    assert by10["R1"] == "AGGREGATE_MATCH"     # 200+300+500 == 1000
    assert by10["R2"] == "AMOUNT_MISMATCH"     # 1200 vs 1150
    assert by10["R3"] == "MISSING_IN_RIGHT"    # unallocated
