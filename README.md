# Springer Capital — Data Engineer Intern Take-Home

## Overview
A Pandas pipeline for profiling seven referral-program source tables, standardizing fields and timestamps, joining data at one row per referral, and applying the assignment's business validation rules. The final CSV is designed to contain 46 rows, one per `user_referrals` record.

## Files
- `your_script.py` — load, profile, clean, join, validate, and export.
- `data/` — the seven source CSV files supplied with the test.
- `output/data_profiling.csv` — null counts, distinct counts, types, and row counts by source column.
- `output/referral_rewards_report.csv` — requested report columns and boolean validity flag.
- `output/validation_audit.csv` — human-readable explanation for each decision.
- `data_dictionary.xlsx` — business-friendly field definitions.

## Run locally (Python 3.11 recommended)
```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
python your_script.py --input-dir data --output-dir output
```
The command writes files under `output/`. The report is outside any container when using the Docker volume command below.

## Run with Docker
Build:
```bash
docker build -t springer-referral-pipeline .
```
Run from the project root (PowerShell and bash):
```bash
docker run --rm -v "${PWD}/data:/app/data:ro" -v "${PWD}/output:/app/output" springer-referral-pipeline
```
Create the local `output` folder first if it does not exist. Docker writes the report and profiling files to that mounted host folder.

## Business rules implemented
A record is valid if either:
1. Reward days are positive, status is `Berhasil`, a transaction ID exists, transaction is `PAID` and `NEW`, transaction occurs after referral creation chronologically (UTC event ordering) and in the same local calendar month after timezone conversion, referrer's membership is not expired at referral time, referrer is not deleted, and a log marks the reward granted; or
2. Status is `Menunggu` or `Tidak Berhasil` and no positive reward is assigned.

The PDF's explicit invalid conditions also override the pending/failed-without-reward valid scenario, including a transaction before the referral, and a PAID transaction after referral creation when no reward is assigned. All other cases are `False` and should be reviewed as potential rule violations, not treated as proof of intentional fraud. The extra `validation_audit.csv` contains a reason. The assignment's invalid scenarios are included in the overall false classification.

## Important assumptions to review
- The provided `user_referral_logs` table has multiple log records per referral. To preserve 46 report rows, the script uses the latest log as the current details row and the latest explicitly granted log for `reward_granted_at`.
- Lead source category is matched using `user_referrals.referee_id = lead_log.lead_id`, as the table description says `referee_id` is used when `referral_source` is `Lead`. The same lead join provides a timezone fallback when the referrer's timezone is missing.
- Source timestamps are parsed as UTC. Referral/update timestamps are displayed in the referrer's `timezone_homeclub`; if that is unavailable, the lead's `timezone_location` is used, then UTC. Transaction timestamps use `timezone_transaction`, then the referrer's timezone, the lead timezone, and finally UTC. Chronological comparisons use UTC instants; the same-month condition uses the localized calendar month.
- `referral_rewards.reward_value` examples are text such as `10 days`; numeric day count is extracted for `num_reward_days`.
- The PDF asks for null handling while also requiring exactly 46 output rows and the complete report schema. The supplied CSVs contain unmatched relationships: only a small subset of `user_referral_logs.user_referral_id` values match `user_referrals.referral_id`, and none of the `TRUE` grant logs match a referral ID or a supplied transaction ID. Consequently, the source data cannot support all `referral_details_id` and `reward_granted_at` values. The pipeline preserves the 46 referral rows and leaves these unsupported facts null rather than fabricating IDs or timestamps. This is a source-data limitation that should be disclosed, not silently filled with made-up values.
- The assignment's sample names are illustrative. User names/phones in supplied CSVs appear tokenized identifiers, so no real identity is inferred.

## Tests
Run the automated integration checks from the project root:
```bash
python -m unittest discover -s tests -v
```
The tests verify the 46-row/22-column output, unique referral IDs, profiling/audit outputs, non-null validity flags, and the explicit invalid condition for transactions before referral creation.

## Output validation
The script raises an error unless exactly 46 rows are written. It also uses merge cardinality validation to catch unexpected join duplication. Review `data_profiling.csv` and `validation_audit.csv` before submitting.

## Security
No credentials or external services are required. Do not commit secrets or real personal data to a public repository without authorization.
