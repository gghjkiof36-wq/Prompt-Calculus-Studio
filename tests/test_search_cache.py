import json,sqlite3,tempfile,time,unittest
from pathlib import Path
from prompt_studio.core import Storage,initial_state


class SearchCacheTests(unittest.TestCase):
    def setUp(self): self.temp=tempfile.TemporaryDirectory(); self.store=Storage(self.temp.name)
    def tearDown(self): self.store.close(); self.temp.cleanup()
    def test_old_database_retains_document_cache_and_accepted_words(self):
        self.store.close(); path=Path(self.temp.name)/'legacy'; path.mkdir()
        db=sqlite3.connect(path/'studio.sqlite3'); db.execute('CREATE TABLE cache(key TEXT PRIMARY KEY,body TEXT NOT NULL,saved REAL NOT NULL)')
        db.execute('INSERT INTO cache VALUES (?,?,?)',('old',json.dumps([dict(value='smile')]),time.time())); db.commit(); db.close()
        self.store=Storage(path); state=initial_state(); self.store.save(state); self.store.use(state['workspace'],'kept',dict(value='kept'))
        self.assertEqual(self.store.cached('old'),[dict(value='smile')]); self.store.clear_cache()
        self.assertIsNone(self.store.cached('old')); self.assertEqual(self.store.load(),state)
        self.assertEqual(self.store.familiar(state['workspace'],'ke')[0]['value'],'kept')
    def test_lru_count_budget_and_utf8_bytes(self):
        self.store.CACHE_LIMIT=2
        for key in ('one','two'): self.store.cache(key,[dict(value=key)])
        self.store.cached('one'); self.store.cache('three',[dict(value='three')])
        self.assertIsNone(self.store.cached('two')); self.assertIsNotNone(self.store.cached('one'))
        self.store.clear_cache(); self.store.CACHE_BYTES=90
        for key in ('one','two','three'): self.store.cache(key,[dict(value='中文字'*4)])
        count,size=self.store.db.execute('SELECT COUNT(*),SUM(length(CAST(body AS BLOB))) FROM cache').fetchone()
        self.assertEqual(count,1); self.assertLessEqual(size,90)
        self.store.cache('large',[dict(value='字'*100)]); self.assertIsNone(self.store.cached('large'))
    def test_disabled_keeps_old_records_and_epoch_changes_on_reenable(self):
        self.store.cache('kept',[1]); epoch=self.store.cache_epoch; self.store.set_cache_enabled(False)
        self.assertGreater(self.store.cache_epoch,epoch); self.assertIsNone(self.store.cached('kept'))
        self.store.cache('new',[2]); self.store.set_cache_enabled(True)
        self.assertEqual(self.store.cached('kept'),[1]); self.assertIsNone(self.store.cached('new'))
        self.store.close(); self.store=Storage(self.temp.name); self.assertEqual(self.store.cached('kept'),[1])
    def test_positive_stale_can_be_read_offline_empty_expires_after_hour(self):
        self.store.cache('positive',[1]); self.store.cache('empty',[])
        with self.store.db: self.store.db.execute('UPDATE cache SET saved=?',(time.time()-90000,))
        self.assertFalse(self.store.cache_fresh('positive')); self.assertEqual(self.store.cached('positive'),[1])
        self.assertIsNone(self.store.cached('positive',86400)); self.assertIsNone(self.store.cached('empty'))
