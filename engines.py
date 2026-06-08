"""
engines.py
----------
Reusable reconciliation engines. A "mini model" for a rule is mostly just
configuration pointed at one of these engines, so all rules behave consistently.

Currently implemented:
  - transactional_recon : row-by-row match of two sources on a key

Future (for summary rules like R4, R12, R14, etc.):
  - summary_recon       : aggregate each source to a level, then compare
"""

from __future__ import annotations

from typing import Dict

import pandas as pd

from base import DataSource, MatchStatus, ReconResult, ReconType


def transactional_recon(
    rule_id: str,
    description: str,
    recon_key: str,
    left: DataSource,
    right: DataSource,
    tolerance: float = 0.01,
) -> ReconResult:
    """
    Reconcile two sources record-by-record on `recon_key`.

    `left` is the primary source, `right` the secondary. Amounts are compared
    within `tolerance` to absorb rounding noise.

    Classifies every key into one of MatchStatus.
    """
    l = left.normalized()
    r = right.normalized()
    left_amt = f"{left.role}_amount"
    right_amt = f"{right.role}_amount"

    # Detect duplicate keys up front — these break a clean 1:1 join and are
    # surfaced rather than silently aggregated.
    dup_left = l["recon_key"][l["recon_key"].duplicated()].unique().tolist()
    dup_right = r["recon_key"][r["recon_key"].duplicated()].unique().tolist()

    merged = l.merge(r, on="recon_key", how="outer", indicator=True)

    def classify(row) -> str:
        if row["_merge"] == "left_only":
            return MatchStatus.MISSING_IN_RIGHT.value
        if row["_merge"] == "right_only":
            return MatchStatus.MISSING_IN_LEFT.value
        # present both sides
        if abs(float(row[left_amt]) - float(row[right_amt])) <= tolerance:
            return MatchStatus.MATCHED.value
        return MatchStatus.AMOUNT_MISMATCH.value

    merged["status"] = merged.apply(classify, axis=1)
    merged["difference"] = merged[left_amt].fillna(0) - merged[right_amt].fillna(0)
    merged = merged.drop(columns=["_merge"])
    merged = merged.rename(columns={
        left_amt: f"{left.role}_amount",
        right_amt: f"{right.role}_amount",
    })

    breaks = merged[merged["status"] != MatchStatus.MATCHED.value].copy()

    counts = merged["status"].value_counts().to_dict()
    summary = {
        "total_keys": int(len(merged)),
        "matched": int(counts.get(MatchStatus.MATCHED.value, 0)),
        "amount_mismatch": int(counts.get(MatchStatus.AMOUNT_MISMATCH.value, 0)),
        "missing_in_right": int(counts.get(MatchStatus.MISSING_IN_RIGHT.value, 0)),
        "missing_in_left": int(counts.get(MatchStatus.MISSING_IN_LEFT.value, 0)),
        "total_breaks": int(len(breaks)),
        "duplicate_keys_left": len(dup_left),
        "duplicate_keys_right": len(dup_right),
        f"{left.role}_total": round(float(l[left_amt].sum()), 2),
        f"{right.role}_total": round(float(r[right_amt].sum()), 2),
        "net_difference": round(float(merged["difference"].sum()), 2),
    }

    return ReconResult(
        rule_id=rule_id,
        description=description,
        recon_type=ReconType.TRANSACTIONAL,
        recon_key=recon_key,
        breaks=breaks.reset_index(drop=True),
        detail=merged.reset_index(drop=True),
        summary=summary,
    )


# ===========================================================================
# GenAI engines
# ===========================================================================
# These sit alongside transactional_recon() and share the same ReconResult
# contract, so the wrapper and rules treat them identically. They live here so
# that adding a semantic or one-to-many rule stays a copy-configure job.

from typing import List, Optional, Tuple  # noqa: E402
from itertools import combinations          # noqa: E402

from base import MATCHED_STATUSES           # noqa: E402


def _exact_phase(left: DataSource, right: DataSource, tolerance: float):
    """Shared first pass: outer-join on key and classify, carrying narration."""
    l = left.normalized_with_narration()
    r = right.normalized_with_narration()
    left_amt = f"{left.role}_amount"
    right_amt = f"{right.role}_amount"

    merged = l.merge(r, on="recon_key", how="outer", indicator=True)

    def classify(row) -> str:
        if row["_merge"] == "left_only":
            return MatchStatus.MISSING_IN_RIGHT.value
        if row["_merge"] == "right_only":
            return MatchStatus.MISSING_IN_LEFT.value
        if abs(float(row[left_amt]) - float(row[right_amt])) <= tolerance:
            return MatchStatus.MATCHED.value
        return MatchStatus.AMOUNT_MISMATCH.value

    merged["status"] = merged.apply(classify, axis=1)
    merged["difference"] = merged[left_amt].fillna(0) - merged[right_amt].fillna(0)
    merged = merged.drop(columns=["_merge"])
    return merged, left_amt, right_amt


def semantic_recon(
    rule_id: str,
    description: str,
    recon_key: str,
    left: DataSource,
    right: DataSource,
    tolerance: float = 0.01,
    embedder=None,
    sim_threshold: float = 0.72,
    amount_tolerance: Optional[float] = None,
) -> ReconResult:
    """
    Feedback #1/#3 — semantic (fuzzy) matching engine.

    First does the normal exact-key pass. Then, for records that remain
    one-sided (MISSING_IN_RIGHT / MISSING_IN_LEFT), it matches them by narration
    similarity (embeddings + cosine) when amounts also agree within
    `amount_tolerance`. Such pairs become SEMANTIC_MATCH with a confidence equal
    to the similarity, instead of being reported as breaks.

    `amount_tolerance` defaults to `tolerance`; widen it for feeds where fees or
    FX are expected to differ.
    """
    from ai.embeddings import default_embedder, cosine_sim_matrix

    embedder = embedder or default_embedder()
    amount_tolerance = tolerance if amount_tolerance is None else amount_tolerance

    merged, left_amt, right_amt = _exact_phase(left, right, tolerance)
    left_narr = f"{left.role}_narration"
    right_narr = f"{right.role}_narration"

    left_only = merged[merged["status"] == MatchStatus.MISSING_IN_RIGHT.value].copy()
    right_only = merged[merged["status"] == MatchStatus.MISSING_IN_LEFT.value].copy()

    semantic_rows: List[dict] = []
    matched_left_idx: set = set()
    matched_right_idx: set = set()

    if len(left_only) and len(right_only):
        lt = left_only[left_narr].fillna("").astype(str).tolist()
        rt = right_only[right_narr].fillna("").astype(str).tolist()
        # Embed both sides together so IDF (document frequencies) are shared,
        # then slice — this makes distinctive tokens dominate the similarity.
        both = embedder.embed(lt + rt)
        sim = cosine_sim_matrix(both[:len(lt)], both[len(lt):])

        l_idx = left_only.index.tolist()
        r_idx = right_only.index.tolist()
        l_amt = left_only[left_amt].to_numpy(dtype=float)
        r_amt = right_only[right_amt].to_numpy(dtype=float)

        # Candidate pairs above the similarity threshold AND within amount tolerance.
        cands: List[Tuple[float, int, int]] = []
        for i in range(len(l_idx)):
            for j in range(len(r_idx)):
                s = float(sim[i, j])
                if s >= sim_threshold and abs(l_amt[i] - r_amt[j]) <= amount_tolerance:
                    cands.append((s, i, j))
        cands.sort(reverse=True)  # greedily take the strongest matches first

        for s, i, j in cands:
            if i in matched_left_idx or j in matched_right_idx:
                continue
            matched_left_idx.add(i)
            matched_right_idx.add(j)
            lrow = left_only.iloc[i]
            rrow = right_only.iloc[j]
            semantic_rows.append({
                "recon_key": f"{lrow['recon_key']} ~ {rrow['recon_key']}",
                left_amt: float(lrow[left_amt]),
                right_amt: float(rrow[right_amt]),
                left_narr: lrow[left_narr],
                right_narr: rrow[right_narr],
                "status": MatchStatus.SEMANTIC_MATCH.value,
                "difference": float(lrow[left_amt]) - float(rrow[right_amt]),
                "match_method": "semantic",
                "match_confidence": round(s, 3),
            })

    # Rows that survived the exact phase but were NOT semantically paired.
    drop_left = {left_only.index[i] for i in matched_left_idx}
    drop_right = {right_only.index[j] for j in matched_right_idx}
    remaining = merged.drop(index=list(drop_left | drop_right)).copy()
    remaining["match_method"] = remaining["status"].apply(
        lambda s: "exact" if s in (MatchStatus.MATCHED.value,
                                   MatchStatus.AMOUNT_MISMATCH.value) else "none")
    remaining["match_confidence"] = remaining["status"].apply(
        lambda s: 1.0 if s in (MatchStatus.MATCHED.value,
                               MatchStatus.AMOUNT_MISMATCH.value) else 0.0)

    detail = pd.concat([remaining, pd.DataFrame(semantic_rows)],
                       ignore_index=True) if semantic_rows else remaining
    detail = detail.reset_index(drop=True)

    breaks = detail[~detail["status"].isin(MATCHED_STATUSES)].copy()

    counts = detail["status"].value_counts().to_dict()
    matched_conf = detail.loc[detail["status"].isin(MATCHED_STATUSES), "match_confidence"]
    summary = {
        "total_keys": int(len(detail)),
        "matched": int(counts.get(MatchStatus.MATCHED.value, 0)),
        "semantic_matched": int(counts.get(MatchStatus.SEMANTIC_MATCH.value, 0)),
        "amount_mismatch": int(counts.get(MatchStatus.AMOUNT_MISMATCH.value, 0)),
        "missing_in_right": int(counts.get(MatchStatus.MISSING_IN_RIGHT.value, 0)),
        "missing_in_left": int(counts.get(MatchStatus.MISSING_IN_LEFT.value, 0)),
        "total_breaks": int(len(breaks)),
        f"{left.role}_total": round(float(merged[left_amt].fillna(0).sum()), 2),
        f"{right.role}_total": round(float(merged[right_amt].fillna(0).sum()), 2),
        "net_difference": round(float(detail["difference"].sum()), 2),
    }

    result = ReconResult(
        rule_id=rule_id, description=description, recon_type=ReconType.TRANSACTIONAL,
        recon_key=recon_key, breaks=breaks.reset_index(drop=True),
        detail=detail, summary=summary,
    )
    result.confidence = round(float(matched_conf.mean()), 3) if len(matched_conf) else 1.0
    return result


def aggregate_match_recon(
    rule_id: str,
    description: str,
    recon_key: str,
    many: DataSource,
    bank: DataSource,
    tolerance: float = 0.01,
) -> ReconResult:
    """
    Mode 4 — N:1 aggregation / bank reco (R9, R10, R12).

    Group the `many` feed by its key (the settlement batch / value date used as
    the recon key), sum the amounts, then match each group total to the single
    `bank` line with the same key.

      AGGREGATE_MATCH    group total equals the bank line (± tolerance)
      AMOUNT_MISMATCH    group and bank line exist but totals differ (partial)
      MISSING_IN_RIGHT   transaction group with no bank line (unallocated group)
      MISSING_IN_LEFT    bank line with no transaction group (unmatched credit)

    Money is summed with Decimal to avoid float drift across many rows.
    """
    from decimal import Decimal

    l = many.normalized()
    r = bank.normalized()
    many_amt = f"{many.role}_amount"
    bank_amt = f"{bank.role}_amount"

    dup_bank = r["recon_key"][r["recon_key"].duplicated()].unique().tolist()

    # Aggregate the many-side by key (Decimal sum), keeping a member count.
    grouped = (l.groupby("recon_key")[many_amt]
               .apply(lambda s: float(sum(Decimal(str(v)) for v in s)))
               .reset_index())
    sizes = l.groupby("recon_key").size().reset_index(name="group_size")
    grouped = grouped.merge(sizes, on="recon_key")

    merged = grouped.merge(r, on="recon_key", how="outer", indicator=True)
    tol = Decimal(str(tolerance))

    statuses, reasons = [], []
    for _, row in merged.iterrows():
        if row["_merge"] == "left_only":
            statuses.append(MatchStatus.MISSING_IN_RIGHT.value)
            reasons.append("transaction group has no bank line (unallocated)")
        elif row["_merge"] == "right_only":
            statuses.append(MatchStatus.MISSING_IN_LEFT.value)
            reasons.append("bank line has no transaction group (unmatched credit)")
        else:
            diff = Decimal(str(row[many_amt])) - Decimal(str(row[bank_amt]))
            if abs(diff) <= tol:
                statuses.append(MatchStatus.AGGREGATE_MATCH.value)
                reasons.append("")
            else:
                statuses.append(MatchStatus.AMOUNT_MISMATCH.value)
                reasons.append(f"group total {row[many_amt]} vs bank {row[bank_amt]} "
                               f"(off by {float(diff):+.2f})")

    merged["status"] = statuses
    merged["break_reason"] = reasons
    merged["group_size"] = merged["group_size"].fillna(0).astype(int)
    merged["difference"] = merged[many_amt].fillna(0) - merged[bank_amt].fillna(0)
    merged = merged.drop(columns=["_merge"])

    breaks = merged[~merged["status"].isin(MATCHED_STATUSES)].copy()
    counts = merged["status"].value_counts().to_dict()
    summary = {
        "total_groups": int(len(merged)),
        "aggregate_matched": int(counts.get(MatchStatus.AGGREGATE_MATCH.value, 0)),
        "amount_mismatch": int(counts.get(MatchStatus.AMOUNT_MISMATCH.value, 0)),
        "unallocated_groups": int(counts.get(MatchStatus.MISSING_IN_RIGHT.value, 0)),
        "unmatched_bank_lines": int(counts.get(MatchStatus.MISSING_IN_LEFT.value, 0)),
        "total_breaks": int(len(breaks)),
        "duplicate_bank_keys": len(dup_bank),
        f"{many.role}_total": round(float(l[many_amt].sum()), 2),
        f"{bank.role}_total": round(float(r[bank_amt].sum()), 2),
        "net_difference": round(float(merged["difference"].sum()), 2),
    }

    return ReconResult(
        rule_id=rule_id, description=description, recon_type=ReconType.TRANSACTIONAL,
        recon_key=recon_key, breaks=breaks.reset_index(drop=True),
        detail=merged.reset_index(drop=True), summary=summary,
    )


def rate_validation_recon(
    rule_id: str,
    description: str,
    recon_key: str,
    txn: DataSource,
    rate_master: DataSource,
    rate_map: dict,
    tolerance: float = 0.01,
) -> ReconResult:
    """
    Mode 3 — rate validation (R11, R13).

    There is no second transaction amount to join against; instead the EXPECTED
    charge is computed from a rate master and compared to the ACTUAL deduction.

    `txn` carries the recon key (`txn.key_column`) and the actual charge
    (`txn.amount_column`). `rate_map` names the rest:
      base_column : column in txn holding the base amount
      lookup_key  : column in txn used to look up the rate
      rate_is_pct : True if the master stores a percentage (default True)
    `rate_master` provides the lookup (`key_column`) and the rate
    (`amount_column`).

    expected = base × rate (rate/100 when rate_is_pct). On-rate rows MATCH;
    off-rate rows are AMOUNT_MISMATCH; rows with no rate in the master are breaks.
    Money is computed with Decimal.
    """
    from decimal import Decimal

    base_col = rate_map["base_column"]
    lookup_col = rate_map["lookup_key"]
    rate_is_pct = rate_map.get("rate_is_pct", True)

    mdf = rate_master.df
    rate_lookup = dict(zip(mdf[rate_master.key_column].astype(str).str.strip(),
                           mdf[rate_master.amount_column]))
    tol = Decimal(str(tolerance))

    rows = []
    for _, t in txn.df.iterrows():
        key = str(t[txn.key_column]).strip()
        base = Decimal(str(t[base_col]))
        actual = Decimal(str(t[txn.amount_column]))
        lk = str(t[lookup_col]).strip()

        if lk not in rate_lookup or pd.isna(rate_lookup[lk]):
            rows.append({
                "recon_key": key, "lookup": lk, "base_amount": float(base),
                "expected_amount": None, "actual_amount": float(actual),
                "status": MatchStatus.AMOUNT_MISMATCH.value,
                "difference": float(actual),
                "break_reason": f"no rate for '{lk}' in master",
                "computed_from": "",
            })
            continue

        rate = Decimal(str(rate_lookup[lk]))
        expected = base * (rate / Decimal(100) if rate_is_pct else rate)
        diff = actual - expected
        on_rate = abs(diff) <= tol
        unit = "%" if rate_is_pct else "x"
        rows.append({
            "recon_key": key, "lookup": lk, "base_amount": float(base),
            "expected_amount": round(float(expected), 2),
            "actual_amount": float(actual),
            "status": MatchStatus.MATCHED.value if on_rate
            else MatchStatus.AMOUNT_MISMATCH.value,
            "difference": round(float(diff), 2),
            "break_reason": "" if on_rate
            else f"expected {round(float(expected),2)} at {rate}{unit}, actual {actual}",
            "computed_from": f"{base} x {rate}{unit}",
        })

    detail = pd.DataFrame(rows)
    breaks = detail[~detail["status"].isin(MATCHED_STATUSES)].copy()
    counts = detail["status"].value_counts().to_dict()
    not_found = int((detail["expected_amount"].isna()).sum())
    summary = {
        "total_keys": int(len(detail)),
        "matched": int(counts.get(MatchStatus.MATCHED.value, 0)),
        "off_rate": int(counts.get(MatchStatus.AMOUNT_MISMATCH.value, 0)) - not_found,
        "rate_not_found": not_found,
        "amount_mismatch": int(counts.get(MatchStatus.AMOUNT_MISMATCH.value, 0)),
        "total_breaks": int(len(breaks)),
        f"{txn.role}_actual_total": round(float(detail["actual_amount"].sum()), 2),
        "expected_total": round(float(detail["expected_amount"].dropna().sum()), 2),
        "net_difference": round(float(detail["difference"].sum()), 2),
    }

    return ReconResult(
        rule_id=rule_id, description=description, recon_type=ReconType.TRANSACTIONAL,
        recon_key=recon_key, breaks=breaks.reset_index(drop=True),
        detail=detail.reset_index(drop=True), summary=summary,
    )


def tolerance_timing_recon(
    rule_id: str,
    description: str,
    recon_key: str,
    left: DataSource,
    right: DataSource,
    tolerance: float = 0.01,
    fee_tolerance: float = 0.0,
    fee_tolerance_pct: float = 0.0,
    date_window: int = 2,
) -> ReconResult:
    """
    Mode 2 — tolerance + timing (R7, R8).

    Key join where the settlement amount may differ from the transaction amount
    by an allowed FEE and the value date may LAG by up to `date_window` days.
    Decomposes the difference instead of dumping it into AMOUNT_MISMATCH:

      MATCHED                  amounts equal (≤ tolerance) and same date
      TIMING_DIFFERENCE        amounts equal, date lag within the window
      FEE_DIFFERENCE           shortfall within the allowed fee, date within window
      AMOUNT_MISMATCH          shortfall beyond the fee, or lag beyond the window
      MISSING_IN_RIGHT/LEFT    one-sided

    Money is compared with Decimal to avoid float-equality artefacts. The allowed
    fee is `max(fee_tolerance, fee_tolerance_pct% of the left amount)`.
    """
    from decimal import Decimal

    l = left.normalized_with_date()
    r = right.normalized_with_date()
    left_amt = f"{left.role}_amount"
    right_amt = f"{right.role}_amount"
    left_date = f"{left.role}_date"
    right_date = f"{right.role}_date"

    dup_left = l["recon_key"][l["recon_key"].duplicated()].unique().tolist()
    dup_right = r["recon_key"][r["recon_key"].duplicated()].unique().tolist()

    merged = l.merge(r, on="recon_key", how="outer", indicator=True)

    def _dec(v) -> Optional[Decimal]:
        if pd.isna(v):
            return None
        return Decimal(str(v))

    tol = Decimal(str(tolerance))
    fee_abs = Decimal(str(fee_tolerance))
    fee_pct = Decimal(str(fee_tolerance_pct))

    statuses, reasons = [], []
    for _, row in merged.iterrows():
        if row["_merge"] == "left_only":
            statuses.append(MatchStatus.MISSING_IN_RIGHT.value)
            reasons.append("no settlement record")
            continue
        if row["_merge"] == "right_only":
            statuses.append(MatchStatus.MISSING_IN_LEFT.value)
            reasons.append("no transaction record")
            continue

        lv, rv = _dec(row[left_amt]), _dec(row[right_amt])
        diff = (lv - rv) if (lv is not None and rv is not None) else Decimal(0)
        abs_diff = abs(diff)
        fee_allow = max(fee_abs, (fee_pct / Decimal(100)) * (lv or Decimal(0)))

        ld, rd = row.get(left_date), row.get(right_date)
        if pd.isna(ld) or pd.isna(rd):
            lag = 0
        else:
            lag = abs((pd.Timestamp(ld).normalize() - pd.Timestamp(rd).normalize()).days)

        if abs_diff <= tol:                       # amounts effectively equal
            if lag == 0:
                statuses.append(MatchStatus.MATCHED.value)
                reasons.append("")
            elif lag <= date_window:
                statuses.append(MatchStatus.TIMING_DIFFERENCE.value)
                reasons.append(f"value-date lag {lag}d (≤ {date_window}d)")
            else:
                statuses.append(MatchStatus.AMOUNT_MISMATCH.value)
                reasons.append(f"value-date lag {lag}d exceeds window {date_window}d")
        elif diff > 0 and abs_diff <= fee_allow and lag <= date_window:
            statuses.append(MatchStatus.FEE_DIFFERENCE.value)
            reasons.append(f"fee {abs_diff} within allowance {fee_allow}"
                           + (f", lag {lag}d" if lag else ""))
        else:
            statuses.append(MatchStatus.AMOUNT_MISMATCH.value)
            if diff > 0:
                reasons.append(f"shortfall {abs_diff} exceeds fee allowance {fee_allow}")
            else:
                reasons.append(f"settlement exceeds transaction by {abs_diff}")

    merged["status"] = statuses
    merged["break_reason"] = reasons
    merged["difference"] = merged[left_amt].fillna(0) - merged[right_amt].fillna(0)
    merged = merged.drop(columns=["_merge"])

    breaks = merged[~merged["status"].isin(MATCHED_STATUSES)].copy()
    counts = merged["status"].value_counts().to_dict()
    summary = {
        "total_keys": int(len(merged)),
        "matched": int(counts.get(MatchStatus.MATCHED.value, 0)),
        "timing_difference": int(counts.get(MatchStatus.TIMING_DIFFERENCE.value, 0)),
        "fee_difference": int(counts.get(MatchStatus.FEE_DIFFERENCE.value, 0)),
        "amount_mismatch": int(counts.get(MatchStatus.AMOUNT_MISMATCH.value, 0)),
        "missing_in_right": int(counts.get(MatchStatus.MISSING_IN_RIGHT.value, 0)),
        "missing_in_left": int(counts.get(MatchStatus.MISSING_IN_LEFT.value, 0)),
        "total_breaks": int(len(breaks)),
        "duplicate_keys_left": len(dup_left),
        "duplicate_keys_right": len(dup_right),
        f"{left.role}_total": round(float(l[left_amt].sum()), 2),
        f"{right.role}_total": round(float(r[right_amt].sum()), 2),
        "net_difference": round(float(merged["difference"].sum()), 2),
    }

    return ReconResult(
        rule_id=rule_id, description=description, recon_type=ReconType.TRANSACTIONAL,
        recon_key=recon_key, breaks=breaks.reset_index(drop=True),
        detail=merged.reset_index(drop=True), summary=summary,
    )


def one_to_many_recon(
    rule_id: str,
    description: str,
    recon_key: str,
    one: DataSource,
    many: DataSource,
    tolerance: float = 0.01,
    max_group_size: int = 4,
) -> ReconResult:
    """
    Feedback #4 — one-to-many match discovery.

    Each record on the `one` side is matched against COMBINATIONS of up to
    `max_group_size` records on the `many` side whose amounts sum to it (within
    `tolerance`). Useful for payment splits, partial settlements and batch
    postings — e.g. three wallet debits of 100/250/150 settling one 500 order.

    Group search is bounded (combinations up to max_group_size) to stay tractable;
    the first qualifying group per `one` record is taken, then its members are
    consumed so they can't match again.
    """
    one_n = one.normalized()
    many_n = many.normalized()
    one_amt = f"{one.role}_amount"
    many_amt = f"{many.role}_amount"

    available = list(many_n.index)
    rows: List[dict] = []
    matched_one = 0

    for _, orow in one_n.iterrows():
        target = float(orow[one_amt])
        pool = [(idx, float(many_n.loc[idx, many_amt])) for idx in available]
        found: Optional[List[int]] = None

        # try smallest groups first (1 member, then 2, ...)
        for size in range(1, max_group_size + 1):
            if found:
                break
            for combo in combinations(pool, size):
                if abs(sum(v for _, v in combo) - target) <= tolerance:
                    found = [idx for idx, _ in combo]
                    break

        if found is not None:
            for idx in found:
                available.remove(idx)
            matched_one += 1
            member_keys = many_n.loc[found, "recon_key"].tolist()
            rows.append({
                "recon_key": orow["recon_key"],
                one_amt: target,
                many_amt: round(float(many_n.loc[found, many_amt].sum()), 2),
                "matched_members": ", ".join(map(str, member_keys)),
                "group_size": len(found),
                "status": MatchStatus.ONE_TO_MANY_MATCH.value,
                "difference": round(target - float(many_n.loc[found, many_amt].sum()), 2),
                "match_method": "one_to_many",
                "match_confidence": 1.0,
            })
        else:
            rows.append({
                "recon_key": orow["recon_key"], one_amt: target, many_amt: None,
                "matched_members": "", "group_size": 0,
                "status": MatchStatus.MISSING_IN_RIGHT.value,
                "difference": target, "match_method": "none", "match_confidence": 0.0,
            })

    # leftover `many` records that were never consumed
    for idx in available:
        mrow = many_n.loc[idx]
        rows.append({
            "recon_key": mrow["recon_key"], one_amt: None,
            many_amt: float(mrow[many_amt]), "matched_members": "", "group_size": 0,
            "status": MatchStatus.MISSING_IN_LEFT.value,
            "difference": -float(mrow[many_amt]),
            "match_method": "none", "match_confidence": 0.0,
        })

    detail = pd.DataFrame(rows)
    breaks = detail[~detail["status"].isin(MATCHED_STATUSES)].copy()
    counts = detail["status"].value_counts().to_dict()
    summary = {
        "total_one_records": int(len(one_n)),
        "one_to_many_matched": int(counts.get(MatchStatus.ONE_TO_MANY_MATCH.value, 0)),
        "missing_in_right": int(counts.get(MatchStatus.MISSING_IN_RIGHT.value, 0)),
        "missing_in_left": int(counts.get(MatchStatus.MISSING_IN_LEFT.value, 0)),
        "total_breaks": int(len(breaks)),
        f"{one.role}_total": round(float(one_n[one_amt].sum()), 2),
        f"{many.role}_total": round(float(many_n[many_amt].sum()), 2),
        "net_difference": round(float(detail["difference"].fillna(0).sum()), 2),
    }

    return ReconResult(
        rule_id=rule_id, description=description, recon_type=ReconType.TRANSACTIONAL,
        recon_key=recon_key, breaks=breaks.reset_index(drop=True),
        detail=detail.reset_index(drop=True), summary=summary,
    )
