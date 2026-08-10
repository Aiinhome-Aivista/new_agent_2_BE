import unittest
from datetime import date
from services.recurring_deliverable_service import RecurringDeliverableService

class TestRecurringDeliverableService(unittest.TestCase):
    def test_monthly_occurrences(self):
        parent = {"name": "Test Monthly"}
        start = date(2026, 4, 15)
        end = date(2027, 3, 31)
        occs = RecurringDeliverableService._generate_occurrences("MONTHLY", start, end, parent)
        self.assertEqual(len(occs), 12)
        self.assertEqual(occs[0]["period_key"], "2026-04")
        self.assertEqual(occs[0]["due_date"], date(2026, 4, 30))
        self.assertEqual(occs[-1]["period_key"], "2027-03")
        self.assertEqual(occs[-1]["due_date"], date(2027, 3, 31))

    def test_monthly_partial_skip(self):
        parent = {"name": "Test Monthly Skip"}
        start = date(2026, 4, 25)
        end = date(2026, 6, 30)
        occs = RecurringDeliverableService._generate_occurrences("MONTHLY", start, end, parent)
        self.assertEqual(len(occs), 2)  # May and June
        self.assertEqual(occs[0]["period_key"], "2026-05")
        
    def test_weekly_occurrences(self):
        parent = {"name": "Test Weekly"}
        start = date(2026, 4, 1)
        end = date(2026, 4, 14)
        occs = RecurringDeliverableService._generate_occurrences("WEEKLY", start, end, parent)
        self.assertEqual(len(occs), 2)
        self.assertEqual(occs[0]["due_date"], date(2026, 4, 5))
        self.assertEqual(occs[1]["due_date"], date(2026, 4, 12))

    def test_quarterly_occurrences(self):
        parent = {"name": "Test Q"}
        start = date(2026, 2, 1)
        end = date(2026, 10, 31)
        occs = RecurringDeliverableService._generate_occurrences("QUARTERLY", start, end, parent)
        self.assertEqual(len(occs), 3)
        self.assertEqual(occs[0]["due_date"], date(2026, 3, 31))
        self.assertEqual(occs[1]["due_date"], date(2026, 6, 30))
        self.assertEqual(occs[2]["due_date"], date(2026, 9, 30))

    def test_yearly_occurrences(self):
        parent = {"name": "Test Y"}
        start = date(2026, 4, 1)
        end = date(2028, 6, 30)
        occs = RecurringDeliverableService._generate_occurrences("YEARLY", start, end, parent)
        self.assertEqual(len(occs), 3)
        self.assertEqual(occs[0]["due_date"], date(2026, 12, 31))
        self.assertEqual(occs[1]["due_date"], date(2027, 12, 31))
        self.assertEqual(occs[2]["due_date"], date(2028, 6, 30))

if __name__ == '__main__':
    unittest.main()
