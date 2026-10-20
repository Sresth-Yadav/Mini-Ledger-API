"""Core ledger tests. Standard library only: `python -m unittest` or `pytest` both work."""
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor

from app import db, ledger


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["LEDGER_DB"] = os.path.join(self.tmp.name, "test.db")
        db.init_db()
        self.conn = db.connect()
        self.bank = ledger.create_account(self.conn, "Funding", "INR", allow_negative=True)
        self.alice = ledger.create_account(self.conn, "Alice", "INR")
        self.bob = ledger.create_account(self.conn, "Bob", "INR")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def pay(self, src, dst, amount, key):
        return ledger.create_transfer(self.conn, src["id"], dst["id"], amount, key)

    def balance(self, acct):
        return ledger.get_account(self.conn, acct["id"])["balance"]

    def test_transfer_moves_money_both_ways(self):
        self.pay(self.bank, self.alice, 10_000, "fund-1")
        self.pay(self.alice, self.bob, 2_500, "pay-1")
        self.assertEqual(self.balance(self.alice), 7_500)
        self.assertEqual(self.balance(self.bob), 2_500)

    def test_ledger_always_sums_to_zero(self):
        self.pay(self.bank, self.alice, 10_000, "fund-1")
        self.pay(self.alice, self.bob, 1, "pay-1")
        self.pay(self.bob, self.alice, 1, "pay-2")
        total = sum(self.balance(a) for a in (self.bank, self.alice, self.bob))
        self.assertEqual(total, 0)

    def test_same_key_same_request_replays_without_moving_money_twice(self):
        self.pay(self.bank, self.alice, 10_000, "fund-1")
        first, replay1 = self.pay(self.alice, self.bob, 3_000, "pay-1")
        second, replay2 = self.pay(self.alice, self.bob, 3_000, "pay-1")
        self.assertFalse(replay1)
        self.assertTrue(replay2)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(self.balance(self.bob), 3_000)

    def test_same_key_different_request_is_rejected(self):
        self.pay(self.bank, self.alice, 10_000, "fund-1")
        self.pay(self.alice, self.bob, 3_000, "pay-1")
        with self.assertRaises(ledger.LedgerError) as ctx:
            self.pay(self.alice, self.bob, 4_000, "pay-1")
        self.assertEqual(ctx.exception.code, "idempotency_key_reuse")
        self.assertEqual(self.balance(self.bob), 3_000)

    def test_insufficient_funds_is_rejected_and_can_be_retried_with_same_key(self):
        with self.assertRaises(ledger.LedgerError) as ctx:
            self.pay(self.alice, self.bob, 100, "pay-1")
        self.assertEqual(ctx.exception.code, "insufficient_funds")
        self.pay(self.bank, self.alice, 500, "fund-1")
        transfer, replayed = self.pay(self.alice, self.bob, 100, "pay-1")
        self.assertFalse(replayed)
        self.assertEqual(self.balance(self.bob), 100)

    def test_validation_errors(self):
        usd = ledger.create_account(self.conn, "Dollar", "USD")
        cases = [
            ((self.alice, self.alice, 1, "k1"), "invalid_transfer"),
            ((self.alice, usd, 1, "k2"), "currency_mismatch"),
            ((self.alice, self.bob, 0, "k3"), "invalid_amount"),
            ((self.alice, self.bob, 1, ""), "missing_idempotency_key"),
        ]
        for args, code in cases:
            with self.assertRaises(ledger.LedgerError) as ctx:
                self.pay(*args)
            self.assertEqual(ctx.exception.code, code)
        with self.assertRaises(ledger.LedgerError) as ctx:
            ledger.create_transfer(self.conn, "acct_missing", self.bob["id"], 1, "k4")
        self.assertEqual(ctx.exception.code, "account_not_found")

    def test_concurrent_requests_with_one_key_move_money_once(self):
        self.pay(self.bank, self.alice, 10_000, "fund-1")

        def attempt(_):
            conn = db.connect()
            try:
                return ledger.create_transfer(conn, self.alice["id"], self.bob["id"], 1_000, "race-1")
            finally:
                conn.close()

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(attempt, range(8)))
        self.assertEqual(len({t["id"] for t, _ in results}), 1)
        self.assertEqual(sum(1 for _, replayed in results if not replayed), 1)
        self.assertEqual(self.balance(self.bob), 1_000)

    def test_concurrent_transfers_never_overdraw(self):
        self.pay(self.bank, self.alice, 1_000, "fund-1")

        def attempt(i):
            conn = db.connect()
            try:
                return ledger.create_transfer(conn, self.alice["id"], self.bob["id"], 400, f"k-{i}")
            except ledger.LedgerError as e:
                return e.code
            finally:
                conn.close()

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(attempt, range(8)))
        self.assertEqual(sum(1 for r in results if isinstance(r, tuple)), 2)
        self.assertEqual(self.balance(self.alice), 200)


if __name__ == "__main__":
    unittest.main()
