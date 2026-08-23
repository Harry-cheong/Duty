import json
import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inputs import build_availability_from_input

def clerks(*names):
    return pd.DataFrame({"RANK & NAME": list(names)})

def response(rows):
    payload = [[clerk, unavail, pref] for clerk, unavail, pref in rows]
    return json.loads(json.dumps(payload))

SLOTS = ["01-08-26 (AM)", "01-08-26 (PM)", "02-08-26 (AM)", "02-08-26 (PM)", "03-08-26"]
DAYS = ["Saturday", "Saturday", "Sunday", "Sunday", "Monday"]
CLERK = "LCP Harry Cheong"

class UnavailableDateParsingTests(unittest.TestCase):
    def matrix(self, unavail, pref):
        df = build_availability_from_input(
            clerks(CLERK),
            response([(CLERK, unavail, pref)]),
            SLOTS,
            DAYS
        )
        return {slot: int(df.loc[CLERK, slot]) for slot in SLOTS}

    def test_day_mark_every_slot_on_that_date(self):
        m = self.matrix(unavail=[1], pref=[3])
        self.assertEqual(m["01-08-26 (AM)"], 0)
        self.assertEqual(m["01-08-26 (PM)"], 0)
        self.assertEqual(m["02-08-26 (AM)"], 1)
        self.assertEqual(m["03-08-26"], 2)

    def test_shift(self):
        m = self.matrix(unavail=[], pref=["AM"])
        self.assertEqual(m["01-08-26 (AM)"], 2)
        self.assertEqual(m["02-08-26 (AM)"], 2)

    def test_no_response(self):
        m = self.matrix(unavail=[], pref=[])
        self.assertEqual(m["01-08-26 (AM)"], 1)
        self.assertEqual(m["01-08-26 (PM)"], 1)
        self.assertEqual(m["02-08-26 (AM)"], 1)
        self.assertEqual(m["02-08-26 (PM)"], 1)
        self.assertEqual(m["03-08-26"], 1)

        
    def test_two_word_day_type_token(self):
        # Sample Token "Weekends AM"
        m = self.matrix(unavail=["Weekends AM"], pref=[])
        self.assertEqual(m["01-08-26 (AM)"], 0)
        self.assertEqual(m["01-08-26 (PM)"], 1)
        self.assertEqual(m["02-08-26 (AM)"], 0)

    def test_two_word_day_token(self):
        # Sample Token "1 (AM)"
        m = self.matrix(unavail=["1 AM"], pref=[])
        self.assertEqual(m["01-08-26 (AM)"], 0)

    def test_string_day_number_matches(self):
        m = self.matrix(unavail=[], pref=["3"])
        self.assertEqual(m["03-08-26"], 2)

    def test_parenthesized_shift(self):
        m = self.matrix(unavail=["1 (AM)"], pref=["2 (PM)"])
        self.assertEqual(m["01-08-26 (AM)"], 0)
        self.assertEqual(m["01-08-26 (PM)"], 1)
        self.assertEqual(m["02-08-26 (AM)"], 1)
        self.assertEqual(m["02-08-26 (PM)"], 2)

    def test_24hr_marks_every_slot_on_that_date(self):
        m = self.matrix(unavail=["1 (24hr)"], pref=[])
        self.assertEqual(m["01-08-26 (AM)"], 0)
        self.assertEqual(m["01-08-26 (PM)"], 0)
        self.assertEqual(m["02-08-26 (AM)"], 1)

    def test_clerk_name_match_is_case_insensitive(self):
        df = build_availability_from_input(
            clerks("PTE RYAN CHOO HONG YU"),
            response([("PTE Ryan Choo Hong Yu", [1], [])]),
            SLOTS,
            DAYS,
        )
        self.assertEqual(int(df.loc["PTE RYAN CHOO HONG YU", "01-08-26 (AM)"]), 0)

    def test_unknown_clerk_is_skipped(self):
        df = build_availability_from_input(
            clerks(CLERK),
            response([("PTE Nobody", [1], ["Tuesday"])]),
            SLOTS,
            DAYS,
        )
        self.assertEqual(list(df.index), [CLERK])
        self.assertTrue(all(int(df.loc[CLERK, slot]) == 1 for slot in SLOTS))

if __name__ == "__main__":
    unittest.main()