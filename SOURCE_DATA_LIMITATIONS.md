# Source data limitations found during validation

The pipeline runs on the supplied CSVs and preserves the required 46 output rows and 22 columns. However, the assignment also asks for null handling. Some missing report values cannot be truthfully filled from the supplied data without inventing identifiers or timestamps.

Observed with the supplied files:

- `user_referrals.csv` contains 46 unique referral IDs.
- Only 7 of those IDs match a `user_referral_logs.user_referral_id` value. Therefore, 39 `referral_details_id` values have no matching source log.
- The source contains 17 logs with `is_reward_granted = TRUE`, but none of their `user_referral_id` values match a `user_referrals.referral_id`.
- The granted-log `source_transaction_id` values also do not match IDs in `user_referrals.transaction_id` or `paid_transactions.transaction_id`. Thus, the supplied files do not support deriving `reward_granted_at` for the referral rows.
- Some `referrer_id` and transaction values have no corresponding records in `user_logs` or `paid_transactions`; these are retained as missing rather than fabricated.

The pipeline writes these gaps to `output/data_quality_summary.json` and records per-row validation explanations in `output/validation_audit.csv`. This preserves the required 46-row output and makes source-data quality visible. A fully null-free report cannot be guaranteed from these source files while also preserving the required data types and avoiding fabricated facts; the upstream dataset/keys would need correction for those fields.
