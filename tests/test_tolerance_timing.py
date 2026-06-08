"""
Phase 3 — Mode 2: tolerance + timing (R7, R8).

Asserts fee and timing differences are classified as their own reconciled
categories (not dumped into AMOUNT_MISMATCH), while a genuine over-fee shortfall
is still a break.
"""

from base import DataSource
from samples import sample_data
from wrapper import ReconWrapper


def _r8_sources():
    f = sample_data("R8")
    return {
        "pg_txn": DataSource("pg_txn", f["pg_txn"], "txn_id", "gross_amount",
                             date_column="txn_date"),
        "pg_settlement": DataSource("pg_settlement", f["pg_settlement"], "txn_id",
                                    "net_amount", date_column="settle_date"),
    }


def test_r8_decomposes_fee_and_timing():
    result = ReconWrapper().run_rule("R8", _r8_sources())
    by_key = dict(zip(result.detail["recon_key"], result.detail["status"]))
    assert by_key["T1"] == "FEE_DIFFERENCE"        # 1000 -> 980 (2% fee), +1d
    assert by_key["T2"] == "TIMING_DIFFERENCE"     # 500 == 500, +2d lag
    assert by_key["T3"] == "MATCHED"               # 750 == 750, same date
    assert by_key["T4"] == "AMOUNT_MISMATCH"       # 1200 -> 1000 (16.7% > 3%)
    assert by_key["T5"] == "MISSING_IN_RIGHT"
    assert by_key["T6"] == "MISSING_IN_LEFT"


def test_r8_summary_counts():
    s = ReconWrapper().run_rule("R8", _r8_sources()).summary
    assert s["matched"] == 1
    assert s["timing_difference"] == 1
    assert s["fee_difference"] == 1
    assert s["amount_mismatch"] == 1
    # Only the genuine break + the two one-sided records are breaks; fee/timing
    # differences are reconciled-with-reason.
    assert s["total_breaks"] == 3


def test_r8_genuine_break_is_flagged():
    result = ReconWrapper().run_rule("R8", _r8_sources())
    break_keys = set(result.breaks["recon_key"])
    assert "T4" in break_keys          # over-fee shortfall
    assert "T1" not in break_keys      # explained fee is not a break
    assert "T2" not in break_keys      # explained timing lag is not a break


def test_r8_break_reason_populated():
    result = ReconWrapper().run_rule("R8", _r8_sources())
    reasons = dict(zip(result.detail["recon_key"], result.detail["break_reason"]))
    assert "fee" in reasons["T1"].lower()
    assert "lag" in reasons["T2"].lower()
    assert reasons["T3"] == ""


def test_r7_runs_and_classifies():
    f = sample_data("R7")
    sources = {
        "wallet_internal": DataSource("wallet_internal", f["wallet_internal"],
                                      "wallet_txn_id", "amount", date_column="date"),
        "wallet_provider": DataSource("wallet_provider", f["wallet_provider"],
                                      "wallet_txn_id", "amount", date_column="date"),
    }
    by_key = dict(zip(*[ReconWrapper().run_rule("R7", sources).detail[c]
                        for c in ("recon_key", "status")]))
    assert by_key["W1"] == "FEE_DIFFERENCE"
    assert by_key["W2"] == "TIMING_DIFFERENCE"
    assert by_key["W3"] == "MATCHED"
    assert by_key["W4"] == "AMOUNT_MISMATCH"
    assert by_key["W5"] == "MISSING_IN_RIGHT"
