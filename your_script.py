"""Springer Capital referral profiling and business-rule validation pipeline.

Usage:
    python your_script.py --input-dir data --output-dir output

The pipeline preserves one output row per source referral (46 for the supplied
assessment data). Missing source facts are not fabricated. They remain null in
fields where a real identifier/timestamp/value cannot be inferred; the audit
file explains the relevant validation failures.
"""
from pathlib import Path
import argparse
import json
import re
import pandas as pd

# The supplied archive uses singular `lead_log.csv` and plural names for others.
TABLE_FILES = {
    "lead_log": "lead_log.csv",
    "paid_transactions": "paid_transactions.csv",
    "referral_rewards": "referral_rewards.csv",
    "user_logs": "user_logs.csv",
    "user_referral_logs": "user_referral_logs.csv",
    "user_referral_statuses": "user_referral_statuses.csv",
    "user_referrals": "user_referrals.csv",
}


def load_tables(input_dir: str) -> dict[str, pd.DataFrame]:
    """Load all seven source CSVs while preserving IDs as strings."""
    root = Path(input_dir)
    tables = {}
    for table, filename in TABLE_FILES.items():
        path = root / filename
        if not path.exists():
            raise FileNotFoundError(f"Required source CSV not found: {path}")
        # IDs must remain strings (some are hash-like IDs); pandas infers other types.
        tables[table] = pd.read_csv(path, dtype={
            "id": "string", "referral_id": "string", "user_referral_id": "string",
            "referrer_id": "string", "referee_id": "string", "user_id": "string",
            "lead_id": "string", "transaction_id": "string",
            "source_transaction_id": "string", "referral_reward_id": "string",
            "user_referral_status_id": "string",
        })
    return tables


def profile_tables(tables: dict[str, pd.DataFrame], output_dir: str) -> None:
    """Profile every source column with row count, null count and distinct count."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for table_name, df in tables.items():
        for col in df.columns:
            s = df[col]
            rows.append({
                "table_name": table_name,
                "column_name": col,
                "pandas_data_type": str(s.dtype),
                "row_count": int(len(df)),
                "null_count": int(s.isna().sum()),
                "null_percent": round(float(s.isna().mean() * 100), 2) if len(df) else 0.0,
                "distinct_value_count": int(s.nunique(dropna=True)),
                "duplicate_row_count": int(df.duplicated().sum()),
            })
    pd.DataFrame(rows).to_csv(out_dir / "data_profiling.csv", index=False)
    summary = {
        name: {"rows": int(len(df)), "columns": int(len(df.columns))}
        for name, df in tables.items()
    }
    (out_dir / "data_profiling_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )


def parse_utc(series: pd.Series) -> pd.Series:
    """Parse source timestamps as UTC; invalid/missing values become NaT."""
    return pd.to_datetime(series, utc=True, errors="coerce")


def localize_series(utc_series: pd.Series, zones: pd.Series) -> pd.Series:
    """Convert UTC timestamps to each row's timezone; return naive local datetimes."""
    values = []
    for timestamp, zone in zip(utc_series, zones):
        if pd.isna(timestamp):
            values.append(pd.NaT)
            continue
        try:
            values.append(timestamp.tz_convert(str(zone)).tz_localize(None))
        except (ValueError, TypeError, KeyError):
            # If the source timezone is missing/invalid, use UTC as a transparent fallback.
            values.append(timestamp.tz_convert("UTC").tz_localize(None))
    return pd.Series(pd.to_datetime(values, errors="coerce"), index=utc_series.index)


def parse_reward_days(value):
    """Parse values like '10 days' as 10. Missing reward is represented as 0 days."""
    if pd.isna(value):
        return 0
    match = re.search(r"-?\d+", str(value))
    return int(match.group()) if match else 0


def as_bool(series: pd.Series) -> pd.Series:
    """Normalize common CSV boolean representations to actual bool values."""
    return series.astype("string").str.strip().str.lower().isin(["true", "1", "yes", "t"])


def first_existing(series: pd.Series, fallback: pd.Series) -> pd.Series:
    """Use fallback only when the preferred source value is missing/blank."""
    preferred = series.copy()
    blank = preferred.astype("string").str.strip().eq("").fillna(False)
    return preferred.mask(preferred.isna() | blank, fallback)


def build_report(tables: dict[str, pd.DataFrame], output_dir: str) -> pd.DataFrame:
    referrals = tables["user_referrals"].copy()
    if "referral_id" not in referrals or referrals["referral_id"].isna().any():
        raise ValueError("user_referrals must contain a non-null referral_id for every row")
    if referrals["referral_id"].duplicated().any():
        raise ValueError("Source user_referrals contains duplicate referral_id values")

    # Normalize source strings used in joins and flags.
    logs = tables["user_referral_logs"].copy()
    logs["user_referral_id"] = logs["user_referral_id"].astype("string").str.strip()
    logs["created_at_utc"] = parse_utc(logs["created_at"])
    logs["grant_flag"] = as_bool(logs["is_reward_granted"])
    logs = logs.sort_values(["created_at_utc", "id"], kind="stable", na_position="first")

    # Latest log supplies the detail ID and latest state. Aggregate grant history
    # separately so a later FALSE log cannot erase an earlier TRUE grant event.
    latest_logs = logs.drop_duplicates("user_referral_id", keep="last").rename(
        columns={"id": "referral_details_id", "created_at_utc": "latest_log_at_utc"}
    )
    granted_logs = logs.loc[logs["grant_flag"]].sort_values(
        ["created_at_utc", "id"], kind="stable"
    ).drop_duplicates("user_referral_id", keep="last")
    granted_logs = granted_logs[["user_referral_id", "created_at_utc"]].rename(
        columns={"created_at_utc": "reward_granted_at_utc"}
    )
    grant_summary = logs.groupby("user_referral_id", as_index=False)["grant_flag"].any().rename(
        columns={"grant_flag": "any_reward_granted"}
    )

    # As described in the ERD, logs reference user_referrals.referral_id. The
    # provided dataset has only seven direct matches; unmatched rows are retained
    # and reported as source-data mismatches rather than inventing detail IDs.
    referrals = referrals.merge(
        latest_logs[["user_referral_id", "referral_details_id", "source_transaction_id", "latest_log_at_utc"]],
        left_on="referral_id", right_on="user_referral_id", how="left", validate="one_to_one"
    )
    referrals = referrals.merge(
        granted_logs, left_on="referral_id", right_on="user_referral_id", how="left",
        validate="one_to_one", suffixes=("", "_grant")
    )
    referrals = referrals.merge(
        grant_summary, left_on="referral_id", right_on="user_referral_id", how="left",
        validate="one_to_one", suffixes=("", "_summary")
    )
    referrals["any_reward_granted"] = referrals["any_reward_granted"].eq(True)

    # Prefer transaction ID from the referral record; use log transaction ID only as fallback.
    referrals["transaction_id"] = first_existing(
        referrals["transaction_id"], referrals["source_transaction_id"]
    )

    # Status and reward lookup tables are dimension tables: deduplicate keys before joining.
    statuses = tables["user_referral_statuses"][["id", "description"]].drop_duplicates("id", keep="last")
    referrals = referrals.merge(
        statuses, left_on="user_referral_status_id", right_on="id", how="left",
        validate="many_to_one"
    ).rename(columns={"description": "referral_status"})

    rewards = tables["referral_rewards"].copy()
    rewards["num_reward_days"] = rewards["reward_value"].map(parse_reward_days).astype("Int64")
    rewards = rewards[["id", "reward_value", "num_reward_days"]].drop_duplicates("id", keep="last")
    referrals = referrals.merge(
        rewards, left_on="referral_reward_id", right_on="id", how="left",
        validate="many_to_one", suffixes=("", "_reward")
    )
    # No assigned reward means zero reward days, consistent with the valid pending/failed scenario.
    referrals["num_reward_days"] = referrals["num_reward_days"].fillna(0).astype("Int64")

    # User logs may contain snapshots. Select the latest membership expiry date per user
    # (and stable last source row for other user attributes) to avoid join multiplication.
    users = tables["user_logs"].copy()
    users["membership_expired_date"] = pd.to_datetime(users["membership_expired_date"], errors="coerce")
    users["is_deleted_bool"] = as_bool(users["is_deleted"])
    users["_source_order"] = range(len(users))
    users = users.sort_values(["membership_expired_date", "_source_order"], na_position="first")
    users = users.drop_duplicates("user_id", keep="last")

    referrer = users.rename(columns={
        "user_id": "referrer_id", "name": "referrer_name",
        "phone_number": "referrer_phone_number", "homeclub": "referrer_homeclub",
        "timezone_homeclub": "referrer_timezone",
        "membership_expired_date": "referrer_membership_expired_date",
        "is_deleted_bool": "referrer_is_deleted",
    })[["referrer_id", "referrer_name", "referrer_phone_number", "referrer_homeclub",
       "referrer_timezone", "referrer_membership_expired_date", "referrer_is_deleted"]]
    referrals = referrals.merge(referrer, on="referrer_id", how="left", validate="many_to_one")

    referee = users.rename(columns={
        "user_id": "referee_id", "name": "referee_name_from_users",
        "phone_number": "referee_phone_from_users",
    })[["referee_id", "referee_name_from_users", "referee_phone_from_users"]]
    referrals = referrals.merge(referee, on="referee_id", how="left", validate="many_to_one")
    referrals["referee_name"] = first_existing(referrals["referee_name"], referrals["referee_name_from_users"])
    referrals["referee_phone"] = first_existing(referrals["referee_phone"], referrals["referee_phone_from_users"])

    transactions = tables["paid_transactions"].copy().drop_duplicates("transaction_id", keep="last")
    transactions = transactions.rename(columns={
        "transaction_status": "transaction_status_raw",
        "transaction_at": "transaction_at_raw",
        "timezone_transaction": "transaction_timezone",
        "transaction_type": "transaction_type_raw",
    })
    referrals = referrals.merge(
        transactions[["transaction_id", "transaction_status_raw", "transaction_at_raw",
                      "transaction_location", "transaction_timezone", "transaction_type_raw"]],
        on="transaction_id", how="left", validate="many_to_one"
    )

    # Lead rows are related to referee_id for referrals whose source is Lead.
    # Their timezone is a fallback if the referrer's timezone is unavailable.
    leads = tables["lead_log"][["lead_id", "source_category", "timezone_location"]].drop_duplicates("lead_id", keep="last")
    referrals = referrals.merge(
        leads, left_on="referee_id", right_on="lead_id", how="left",
        validate="many_to_one"
    )

    # Referral/update timestamps use the referrer's club timezone, falling back
    # to the lead timezone and then UTC. Transaction timestamps use their own
    # timezone, then the referrer's timezone, the lead timezone, and UTC.
    referrals["referral_at_utc"] = parse_utc(referrals["referral_at"])
    referrals["transaction_at_utc"] = parse_utc(referrals["transaction_at_raw"])
    referrals["updated_at_utc"] = parse_utc(referrals["updated_at"])
    referral_zone = referrals["referrer_timezone"].combine_first(referrals["timezone_location"]).fillna("UTC")
    transaction_zone = (
        referrals["transaction_timezone"]
        .combine_first(referrals["referrer_timezone"])
        .combine_first(referrals["timezone_location"])
        .fillna("UTC")
    )
    referrals["referral_at_local"] = localize_series(referrals["referral_at_utc"], referral_zone)
    referrals["transaction_at_local"] = localize_series(referrals["transaction_at_utc"], transaction_zone)
    referrals["updated_at_local"] = localize_series(referrals["updated_at_utc"], referral_zone)
    referrals["reward_granted_at_local"] = localize_series(
        referrals["reward_granted_at_utc"], referral_zone
    )
    source = referrals["referral_source"].astype("string").str.strip().str.casefold()
    referrals["referral_source_category"] = pd.NA
    referrals.loc[source.eq("user sign up"), "referral_source_category"] = "Online"
    referrals.loc[source.eq("draft transaction"), "referral_source_category"] = "Offline"
    referrals.loc[source.eq("lead"), "referral_source_category"] = referrals.loc[source.eq("lead"), "source_category"]

    status = referrals["referral_status"].astype("string").str.strip().str.casefold()
    tx_status = referrals["transaction_status_raw"].astype("string").str.strip().str.casefold()
    tx_type = referrals["transaction_type_raw"].astype("string").str.strip().str.casefold()
    reward_positive = referrals["num_reward_days"].gt(0)
    has_tx = referrals["transaction_id"].notna() & referrals["transaction_id"].astype("string").str.strip().ne("")
    paid = tx_status.eq("paid").fillna(False)
    is_new = tx_type.eq("new").fillna(False)
    # Apply event ordering and same-month checks to the localized timestamps.
    after = referrals["transaction_at_utc"].gt(referrals["referral_at_utc"]).fillna(False)
    same_month = (
        referrals["transaction_at_local"].dt.year.eq(referrals["referral_at_local"].dt.year)
        & referrals["transaction_at_local"].dt.month.eq(referrals["referral_at_local"].dt.month)
    ).fillna(False)
    transaction_before_referral = (
        referrals["transaction_at_utc"].lt(referrals["referral_at_utc"])
    ).fillna(False)
    # If expiry is missing, evidence is insufficient to affirm active membership; fail closed.
    ref_date = referrals["referral_at_local"].dt.date
    expiry_date = referrals["referrer_membership_expired_date"].dt.date
    membership_ok = expiry_date.notna() & (expiry_date >= ref_date)
    not_deleted = referrals["referrer_is_deleted"].eq(False)
    granted_flag = referrals["any_reward_granted"].fillna(False).astype(bool)
    successful = status.eq("berhasil").fillna(False)
    pending_or_failed = status.isin(["menunggu", "tidak berhasil"]).fillna(False)

    # Valid scenarios from the PDF. For successful rewards, all ten conditions must pass.
    
    valid_success = (
        reward_positive
        & successful
        & has_tx
        & paid
        & is_new
        & after
        & same_month
        & membership_ok
        & not_deleted
        & granted_flag
    )

    # A pending/failed referral without a reward is not valid if
    # a paid transaction exists after referral creation.
    paid_after_referral_without_reward = (
        ~reward_positive
        & has_tx
        & paid
        & after
    )

    # The PDF's explicit invalid condition 5 (transaction before referral)
    # overrides the pending/failed-without-reward valid scenario.
    valid_no_reward = (
        pending_or_failed
        & ~reward_positive
        & ~paid_after_referral_without_reward
        & ~transaction_before_referral
    )

    referrals["is_business_logic_valid"] = (
        valid_success | valid_no_reward
    ).fillna(False).astype(bool)

    # Explain failed rules in a separate audit file.
    reasons = []
    for i, row in referrals.iterrows():
        flags = []
        if row["is_business_logic_valid"]:
            reasons.append("Meets a valid scenario specified in the assignment")
            continue
        if pd.isna(row.get("referral_details_id")):
            flags.append("No matching user_referral_logs record for referral_id")
        if pd.isna(row.get("referral_status")):
            flags.append("Missing referral status lookup")
        if row["referral_status"] if pd.notna(row.get("referral_status")) else False:
            if str(row["referral_status"]).strip().casefold() == "berhasil" and not reward_positive.loc[i]:
                flags.append("Successful referral has no positive reward")
        if bool(reward_positive.loc[i]) and not bool(successful.loc[i]):
            flags.append("Positive reward assigned to non-successful referral")
        if bool(reward_positive.loc[i]) and not bool(has_tx.loc[i]):
            flags.append("Positive reward without transaction ID")
        if bool(successful.loc[i]) and bool(reward_positive.loc[i]):
            checks = {"transaction not PAID": paid.loc[i], "transaction type not NEW": is_new.loc[i],
                      "transaction not after referral": after.loc[i], "transaction not in same month": same_month.loc[i],
                      "membership expiry missing/expired": membership_ok.loc[i],
                      "referrer account deleted/unknown": not_deleted.loc[i],
                      "no matching reward-granted log": granted_flag.loc[i]}
            flags.extend(message for message, passed in checks.items() if not bool(passed))
        if (not bool(reward_positive.loc[i])) and bool(has_tx.loc[i]) and bool(paid.loc[i]) and bool(after.loc[i]):
            flags.append("Paid post-referral transaction exists but no reward is assigned")
        if bool(has_tx.loc[i]) and bool(transaction_before_referral.loc[i]):
            flags.append("Transaction predates referral")
        reasons.append("; ".join(dict.fromkeys(flags)) or "Does not satisfy a valid scenario; review source data")

    # Required output schema and order from pages 7-8 of the PDF.
    out = pd.DataFrame({
        "referral_details_id": pd.to_numeric(referrals["referral_details_id"], errors="coerce").astype("Int64"),
        "referral_id": referrals["referral_id"],
        "referral_source": referrals["referral_source"],
        "referral_source_category": referrals["referral_source_category"],
        "referral_at": referrals["referral_at_local"],
        "referrer_id": referrals["referrer_id"],
        "referrer_name": referrals["referrer_name"],
        "referrer_phone_number": referrals["referrer_phone_number"],
        "referrer_homeclub": referrals["referrer_homeclub"],
        "referee_id": referrals["referee_id"],
        "referee_name": referrals["referee_name"],
        "referee_phone": referrals["referee_phone"],
        "referral_status": referrals["referral_status"],
        "num_reward_days": referrals["num_reward_days"],
        "transaction_id": referrals["transaction_id"],
        "transaction_status": referrals["transaction_status_raw"],
        "transaction_at": referrals["transaction_at_local"],
        "transaction_location": referrals["transaction_location"],
        "transaction_type": referrals["transaction_type_raw"],
        "updated_at": referrals["updated_at_local"],
        "reward_granted_at": referrals["reward_granted_at_local"],
        "is_business_logic_valid": referrals["is_business_logic_valid"].astype(bool),
    })

    # Initcap text values, while preserving club names as supplied by the source.
    for col in ["referrer_name", "referee_name", "referral_source", "referral_status",
                "transaction_status", "transaction_type", "referral_source_category"]:
        out[col] = out[col].map(lambda value: value.title() if isinstance(value, str) else value)
    for col in ["referral_at", "transaction_at", "updated_at", "reward_granted_at"]:
        out[col] = pd.to_datetime(out[col], errors="coerce").dt.strftime("%Y-%m-%d %H:%M:%S")

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_dir / "referral_rewards_report.csv", index=False)
    audit = out[["referral_id", "is_business_logic_valid"]].copy()
    audit["validation_reason"] = reasons
    audit.to_csv(out_dir / "validation_audit.csv", index=False)

    # Data-quality audit: explicit counts for missing values and unmatched logs.
    # Make the source-key limitation measurable so it is visible to reviewers.
    referral_ids = set(referrals["referral_id"].dropna().astype(str).str.strip())
    raw_logs = tables["user_referral_logs"].copy()
    log_referral_ids = set(raw_logs["user_referral_id"].dropna().astype(str).str.strip())
    granted_mask = as_bool(raw_logs["is_reward_granted"])
    granted_log_ids = set(
        raw_logs.loc[granted_mask, "user_referral_id"].dropna().astype(str).str.strip()
    )
    quality = {
        "referral_rows_expected": 46,
        "referral_rows_output": int(len(out)),
        "referrals_without_matching_log": int(out["referral_details_id"].isna().sum()),
        "unique_referral_ids_with_matching_log": int(len(referral_ids & log_referral_ids)),
        "granted_log_rows_in_source": int(granted_mask.sum()),
        "granted_log_referral_ids_matching_source_referrals": int(len(referral_ids & granted_log_ids)),
        "missing_values_by_output_column": {k: int(v) for k, v in out.isna().sum().items()},
        "note": "Source gaps are retained and reported; IDs/timestamps are never fabricated. The source data has no match for the grant-log records through the documented referral key.",
    }
    (out_dir / "data_quality_summary.json").write_text(json.dumps(quality, indent=2), encoding="utf-8")
    if len(out) != 46:
        raise ValueError(f"Expected 46 referral rows, got {len(out)}")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Profile and validate referral rewards")
    parser.add_argument("--input-dir", default="data")
    parser.add_argument("--output-dir", default="output")
    args = parser.parse_args()
    tables = load_tables(args.input_dir)
    profile_tables(tables, args.output_dir)
    report = build_report(tables, args.output_dir)
    print(f"Completed: {len(report)} referral rows")
    print(f"Valid: {int(report['is_business_logic_valid'].sum())}; invalid: {int((~report['is_business_logic_valid']).sum())}")
    print(f"Report: {Path(args.output_dir) / 'referral_rewards_report.csv'}")
    print(f"Profiling: {Path(args.output_dir) / 'data_profiling.csv'}")


if __name__ == "__main__":
    main()
