"""Metadata-only result retention, within the caller's existing transaction."""

SWEEP_BATCH = 128
ACTIVE = ('submitting','queued','running','unconfirmed','results_pending')
ATTEMPT_ACTIVE = ('preparing','submitted','collecting','unconfirmed','results_pending','cancel_requested')


def protected(db, operation):
    job=db.execute("SELECT json_extract(body,'$.state') FROM generation_jobs WHERE id=?",(operation,)).fetchone()
    if job and job[0] in ACTIVE:return True
    attempt=db.execute("SELECT json_extract(body,'$.status') FROM stage_header WHERE id=? AND kind='attempt'",(operation,)).fetchone()
    if attempt and attempt[0] in ATTEMPT_ACTIVE:return True
    if attempt and db.execute("""SELECT 1 FROM stage_header a JOIN stage_header r ON r.id=a.owner,
            json_each(r.body,'$.stack') f WHERE a.id=? AND json_extract(f.value,'$.job')=? LIMIT 1""",
            (operation,operation)).fetchone():return True
    # A complete header alone is not evidence that its frame advanced.
    if attempt and attempt[0]=='complete' and not db.execute(
            "SELECT 1 FROM channel_applied_events WHERE server='local' AND epoch='stage-continuation-v1' AND seq=? AND disposition='local_continuation'",
            (operation+':continue',)).fetchone():return True
    return False


def cursor(db):
    row=db.execute('SELECT after_rowid FROM channel_result_retention WHERE id=1').fetchone()
    return row[0] if row and type(row[0]) is int and row[0]>=0 else 0


def retain(db, removed=(), *, unresolved=()):
    """Delete retired jobs now; sweep at most 128 rows for orphans per retention.

    The keyset cursor progresses across saves, so retained history never turns
    a generation write into an unbounded anti-join. No filesystem cleanup here.
    """
    after=cursor(db)
    for operation in removed:
        if operation not in unresolved and not protected(db,operation):
            db.execute('DELETE FROM channel_result_candidates WHERE operation=?',(operation,))
    rows=db.execute("""SELECT c.rowid,c.operation,a.id,g.id FROM
        (SELECT rowid,operation FROM channel_result_candidates WHERE rowid>? ORDER BY rowid LIMIT ?) c
        LEFT JOIN stage_header a ON a.id=c.operation AND a.kind='attempt'
        LEFT JOIN generation_jobs g ON g.id=c.operation ORDER BY c.rowid""",(after,SWEEP_BATCH)).fetchall()
    for operation in {row[1] for row in rows if row[2] is None or row[3] is None}:
        if operation not in unresolved and not protected(db,operation):
            db.execute('DELETE FROM channel_result_candidates WHERE operation=?',(operation,))
    following=rows[-1][0] if len(rows)==SWEEP_BATCH else 0
    if following!=after:
        db.execute('INSERT OR REPLACE INTO channel_result_retention VALUES (1,?)',(following,))
    return following
