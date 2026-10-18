"""Group schema DDL into one durable commit, preserving caller transactions."""
from contextlib import contextmanager


@contextmanager
def schema_transaction(db):
    # sqlite3's implicit transaction starts for DML, not CREATE TABLE/INDEX.
    # A context manager alone otherwise fsyncs every DDL statement separately.
    db.execute('SAVEPOINT pcs_schema')
    try:
        yield
    except BaseException:
        db.execute('ROLLBACK TO pcs_schema')
        db.execute('RELEASE pcs_schema')
        raise
    else:
        db.execute('RELEASE pcs_schema')
