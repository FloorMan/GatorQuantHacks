"""The 17 evidence scenarios (evidence/cases.py) as unit tests.

Each scenario runs mpex on the network model; it must pass every check, keep
every invariant after every step, conserve cash and shares, and settle nothing twice.
"""
import unittest

from evidence.cases import CASES
from evidence.runner import run_case


class ScenarioTests(unittest.TestCase):
    pass


def _make(cid):
    def test(self):
        result = run_case(cid)
        self.assertEqual(result["status"], "PASS", result["failures"])
    return test


for _cid, _title, _fn in CASES:
    setattr(ScenarioTests, "test_" + _cid.replace("-", "_"), _make(_cid))

if __name__ == "__main__":
    unittest.main()
