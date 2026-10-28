"""Integration checks for the supplied Springer Capital assessment data."""
import subprocess
import sys
import unittest
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_COLUMNS = [
    "referral_details_id", "referral_id", "referral_source",
    "referral_source_category", "referral_at", "referrer_id",
    "referrer_name", "referrer_phone_number", "referrer_homeclub",
    "referee_id", "referee_name", "referee_phone", "referral_status",
    "num_reward_days", "transaction_id", "transaction_status",
    "transaction_at", "transaction_location", "transaction_type",
    "updated_at", "reward_granted_at", "is_business_logic_valid",
]

class PipelineIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run([
            sys.executable, "your_script.py", "--input-dir", "data",
            "--output-dir", "output_test",
        ], cwd=ROOT, check=True, capture_output=True, text=True)
        cls.report = pd.read_csv(ROOT / "output_test" / "referral_rewards_report.csv")
        cls.audit = pd.read_csv(ROOT / "output_test" / "validation_audit.csv")

    def test_report_schema_and_row_count(self):
        self.assertEqual(list(self.report.columns), EXPECTED_COLUMNS)
        self.assertEqual(len(self.report), 46)
        self.assertEqual(self.report["referral_id"].nunique(), 46)

    def test_boolean_flag_has_no_nulls(self):
        self.assertFalse(self.report["is_business_logic_valid"].isna().any())

    def test_explicit_pre_referral_invalid_condition(self):
        source = pd.read_csv(ROOT / "data" / "user_referrals.csv", dtype=str)
        tx = pd.read_csv(ROOT / "data" / "paid_transactions.csv", dtype=str)
        joined = source.merge(tx, on="transaction_id", how="inner")
        joined["referral_utc"] = pd.to_datetime(joined["referral_at"], utc=True, errors="coerce")
        joined["transaction_utc"] = pd.to_datetime(joined["transaction_at"], utc=True, errors="coerce")
        before = set(joined.loc[joined["transaction_utc"] < joined["referral_utc"], "referral_id"])
        flagged = self.report.set_index("referral_id")["is_business_logic_valid"]
        self.assertTrue(all(not bool(flagged.loc[rid]) for rid in before))

    def test_profiling_and_audit_outputs(self):
        profile = pd.read_csv(ROOT / "output_test" / "data_profiling.csv")
        self.assertEqual(profile["table_name"].nunique(), 7)
        self.assertTrue({"null_count", "distinct_value_count"}.issubset(profile.columns))
        self.assertEqual(len(self.audit), 46)

if __name__ == "__main__":
    unittest.main()
