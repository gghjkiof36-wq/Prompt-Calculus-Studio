import importlib.util
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

if not importlib.util.find_spec("mcp"):
    raise unittest.SkipTest("optional PCS MCP probe environment required")

from jsonschema import Draft202012Validator
from pydantic import ValidationError
from prompt_calculus_studio.mcp_probe.contracts import ContextBundle, ProbeError, Result, Selection, schemas
from prompt_calculus_studio.mcp_probe.service import LIMIT, ProbeService, recipe

class ProbeContractTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.instance = str(uuid4())
        self.service = ProbeService(self.root, self.instance)
        self.addCleanup(self.service.db.close)

    def create(self, key="repeatable", resource="sample-blue"):
        return self.service.dispatch("pcs_create_test_job", {"resource_id":resource, "idempotency_key":key}, str(uuid4()))

    def test_wire_contracts_validate_actual_receipts_and_bound_ranges(self):
        receipt = self.create()
        job, result = receipt["job"], receipt["result"]
        resource = self.service.dispatch("pcs_probe_resources", {}, str(uuid4()))["resources"][0]
        skill, agent = recipe()
        fault = ProbeError("invalid_arguments", "Bad input").fault
        samples = dict(Resource=resource, ContextBundle=job["bundle"], SkillRevision=skill.model_dump(),
                       AgentRevision=agent.model_dump(), Job=job, Result=result, Fault=fault.model_dump())
        for name, schema in schemas().items():
            with self.subTest(name=name):
                Draft202012Validator.check_schema(schema)
                Draft202012Validator(schema).validate(samples[name])
        with self.assertRaises(ValidationError):
            ContextBundle.model_validate(dict(job["bundle"], used_bytes=300))
        with self.assertRaises(ValidationError):
            Selection.model_validate(dict(result["processed"][0], end=-1))
        with self.assertRaises(ValidationError):
            Result.model_validate(dict(result, output_bytes=0))
        self.assertEqual(result["citations"], job["bundle"]["selected"])
        self.assertEqual(result["role"], "candidate")
        self.assertIsNone(job["model"])

    def test_concurrent_retries_freeze_one_record_and_conflicting_input_fails(self):
        def submit(_):
            other = ProbeService(self.root, self.instance)
            try:
                return other.dispatch("pcs_create_test_job", {"resource_id":"sample-green", "idempotency_key":"k"}, str(uuid4()))
            finally:
                other.db.close()
        with ThreadPoolExecutor(max_workers=3) as pool:
            receipts = list(pool.map(submit, range(6)))
        self.assertTrue(all(r == receipts[0] for r in receipts))
        with self.assertRaises(ProbeError) as rejected:
            self.create("k")
        self.assertEqual(rejected.exception.fault.code, "idempotency_conflict")
        self.assertEqual(self.service.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 1)
        with self.assertRaises(ProbeError) as wrong:
            ProbeService(self.root, str(uuid4()))
        self.assertEqual(wrong.exception.fault.code, "instance_mismatch")

    def test_storage_rollback_and_retry_at_capacity(self):
        self.service.db.execute("CREATE TRIGGER reject_job BEFORE INSERT ON jobs BEGIN SELECT RAISE(ABORT, 'fixture'); END")
        with self.assertRaises(sqlite3.Error):
            self.create()
        self.assertEqual(self.service.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 0)
        self.service.db.execute("DROP TRIGGER reject_job")
        first = self.create()
        for idx in range(1, LIMIT):
            self.create("fixture-"+str(idx))
        self.assertEqual(self.create(), first)
        with self.assertRaises(ProbeError) as full:
            self.create("over-limit")
        self.assertEqual(full.exception.fault.code, "capacity")
