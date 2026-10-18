import sqlite3
import unittest
from prompt_calculus_studio.sqlite_schema import schema_transaction


class SchemaTransactionTests(unittest.TestCase):
    def test_schema_failure_rolls_back_all_ddl(self):
        with sqlite3.connect(':memory:') as db:
            with self.assertRaises(sqlite3.OperationalError):
                with schema_transaction(db):
                    db.execute('CREATE TABLE first (id)')
                    db.execute('CREATE INVALID second')
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='first'").fetchone())
            self.assertFalse(db.in_transaction)

    def test_schema_does_not_commit_the_callers_transaction(self):
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE existing (value)')
            db.execute('INSERT INTO existing VALUES (1)')
            with schema_transaction(db):
                db.execute('CREATE TABLE added (value)')
            self.assertTrue(db.in_transaction)
            db.rollback()
            self.assertEqual(db.execute('SELECT * FROM existing').fetchall(), [])
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='added'").fetchone())
