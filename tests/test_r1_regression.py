"""
R1 regression baseline (Phase 0).
---------------------------------
Locks in the exact behaviour of R1 (exact_key mode) on the built-in sample data.
This snapshot MUST keep passing unchanged through every later phase of the build
plan — it is the guardrail that proves the refactors do not alter R1.

The snapshot is order-independent: it keys every assertion by recon_key, so it
does not depend on pandas' row ordering after the outer join.
"""

from base import DataSource
from samples import sample_data
from wrapper import ReconWrapper


def _run_r1():
    frames = sample_data("R1")
    sources = {
        "oms_pos": DataSource("oms_pos", frames["oms_pos"],
                              "order_id", "wallet_amount_utilized"),
        "wallet":  DataSource("wallet", frames["wallet"],
                              "order_id", "transaction_amount"),
    }
    # enrich=False: the regression baseline is pure reconciliation, independent
    # of the AI layer (which must never change classification).
    return ReconWrapper().run_rule("R1", sources, enrich=False)


# Expected per-key outcomes on the built-in R1 sample data.
EXPECTED_STATUS = {
    "A101": "MATCHED",            # 500.00 == 500.00
    "A102": "AMOUNT_MISMATCH",    # 250.00 vs 245.00
    "A103": "MATCHED",            # 120.00 == 120.00
    "A104": "MISSING_IN_RIGHT",   # OMS only
    "A105": "MISSING_IN_LEFT",    # wallet only
    "A106": "MISSING_IN_RIGHT",   # OMS only
}
EXPECTED_DIFF = {
    "A101": 0.00,
    "A102": 5.00,
    "A103": 0.00,
    "A104": 999.00,
    "A105": -300.00,
    "A106": 50000.00,
}


def test_r1_summary_counts():
    result = _run_r1()
    s = result.summary
    assert s["total_keys"] == 6
    assert s["matched"] == 2
    assert s["amount_mismatch"] == 1
    assert s["missing_in_right"] == 2
    assert s["missing_in_left"] == 1
    assert s["total_breaks"] == 4
    assert s["duplicate_keys_left"] == 0
    assert s["duplicate_keys_right"] == 0


def test_r1_per_row_status_snapshot():
    result = _run_r1()
    got = dict(zip(result.detail["recon_key"], result.detail["status"]))
    assert got == EXPECTED_STATUS


def test_r1_per_row_difference_snapshot():
    result = _run_r1()
    got = {k: round(float(v), 2)
           for k, v in zip(result.detail["recon_key"], result.detail["difference"])}
    assert got == EXPECTED_DIFF


def test_r1_breaks_are_the_non_matched_rows():
    result = _run_r1()
    break_keys = set(result.breaks["recon_key"])
    expected_breaks = {k for k, v in EXPECTED_STATUS.items() if v != "MATCHED"}
    assert break_keys == expected_breaks
    # confidence is 1.0 for pure exact matching (no AI enrichment)
    assert result.confidence == 1.0
