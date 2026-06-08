"""
Phase 2 — generalised result schema + tolerance override.

Asserts every result carries the generalised columns, that an exact_key rule
leaves them empty (so the UI hides them), and that the tolerance override changes
classification without mutating the registry.
"""

import registry
from base import DataSource
from registry import GENERALISED_COLUMNS
from samples import sample_data
from wrapper import ReconWrapper


def _r1_sources():
    f = sample_data("R1")
    return {
        "oms_pos": DataSource("oms_pos", f["oms_pos"], "order_id", "wallet_amount_utilized"),
        "wallet":  DataSource("wallet", f["wallet"], "order_id", "transaction_amount"),
    }


def test_generalised_columns_present():
    result = ReconWrapper().run_rule("R1", _r1_sources())
    for col in GENERALISED_COLUMNS:
        assert col in result.detail.columns
        assert col in result.breaks.columns


def test_exact_key_leaves_generalised_columns_empty():
    # They must be entirely NA so the UI's drop-empty logic hides them, keeping
    # exact_key output visually identical to before.
    result = ReconWrapper().run_rule("R1", _r1_sources())
    for col in GENERALISED_COLUMNS:
        assert result.detail[col].isna().all()


def test_tolerance_override_changes_classification():
    w = ReconWrapper()
    strict = w.run_rule("R1", _r1_sources(), tolerance=0.0)
    assert strict.summary["matched"] == 2
    assert strict.summary["amount_mismatch"] == 1     # A102: 250 vs 245

    loose = w.run_rule("R1", _r1_sources(), tolerance=10.0)
    assert loose.summary["matched"] == 3              # A102 now within tolerance
    assert loose.summary["amount_mismatch"] == 0


def test_tolerance_override_does_not_mutate_registry():
    ReconWrapper().run_rule("R1", _r1_sources(), tolerance=99.0)
    assert registry.all_specs()["R1"].tolerance == 0.01   # registry unchanged
