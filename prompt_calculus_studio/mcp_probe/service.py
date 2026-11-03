"""Application operations over a bounded, explicitly synthetic instance."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .contracts import (AgentRevision, ContextBundle, Id, Job, ProbeError, Receipt, Ref,
                        Resource, Result, Selection, SkillRevision, canonical, digest)
from ..releases import CURRENT

LIMIT = 128
OWNER = "paired-operator"
# Fixed public fixtures are the entire resource boundary.
TEXT = {"sample-blue": "Blue bell.", "sample-green": "Green leaf."}
REVISION = "r1"

def ref(name, content):
    return Ref(id=name, revision=REVISION, sha256=digest(content))

def sample_resource(key):
    source = ref(key, TEXT[key].encode())
    return Resource(**source.model_dump(), source_kind="synthetic", source_id=key,
                    title=TEXT[key], logical_location=key, role="original",
                    capabilities=["describe"], availability="available")

def recipe():
    skill = SkillRevision(id="describe", revision=REVISION, source=ref("recipe", b"describe fixture"),
                          entry="SKILL.md", dependencies=[], requires=["describe"],
                          scope=list(TEXT), max_output_bytes=256)
    skill_ref = ref(skill.id, canonical(skill.model_dump()))
    agent = AgentRevision(id="probe", revision=REVISION, role_name="Synthetic fixture reader",
                          skills=[skill_ref], model=None, scope=list(TEXT), capabilities=["describe"],
                          output_contract="Result.v1", max_output_bytes=256)
    return skill, agent

class Inputs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

class Page(Inputs):
    limit: int = Field(default=2, ge=1, le=2)
    cursor: Id | None = None

class Create(Inputs):
    resource_id: Id
    idempotency_key: Id

class Lookup(Inputs):
    job_id: Id

TOOLS = {
    "pcs_probe_resources": (Page, "List the synthetic PCS fixture resources and capabilities.", True),
    "pcs_create_test_job": (Create, "Create a synthetic fixture receipt. Reuse the same idempotency key on retry.", False),
    "pcs_get_test_job": (Lookup, "Read a synthetic job and its candidate result by the returned job ID.", True),
    "pcs_cancel_test_job": (Lookup, "Check cancellation of a synthetic job. This immediate probe finishes at acceptance.", False),
}

class ProbeService:
    def __init__(self, directory: Path, instance: str):
        self.instance = instance
        self.db = sqlite3.connect(directory / "synthetic-jobs.sqlite3", timeout=2)
        try:
            with self.db:
                self.db.execute("CREATE TABLE IF NOT EXISTS identity(slot INTEGER PRIMARY KEY CHECK(slot=1), instance TEXT NOT NULL)")
                self.db.execute("INSERT OR IGNORE INTO identity VALUES(1, ?)", (instance,))
                if self.db.execute("SELECT instance FROM identity WHERE slot=1").fetchone() != (instance,):
                    raise ProbeError("instance_mismatch", "This profile belongs to a different PCS instance.")
                self.db.execute("""CREATE TABLE IF NOT EXISTS jobs(
                    job_id TEXT PRIMARY KEY, principal TEXT NOT NULL, idem TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    receipt TEXT NOT NULL, UNIQUE(principal, idem))""")
        except BaseException:
            self.db.close()
            raise
        skill, agent = recipe()
        self.agent = ref(agent.id, canonical(agent.model_dump()))
        self.skills = agent.skills

    def dispatch(self, method: str, arguments: dict, request_id: str) -> dict:
        spec = TOOLS.get(method)
        if spec is None:
            raise ProbeError("unsupported", "This operation is outside the probe capabilities.")
        try:
            values = spec[0].model_validate(arguments)
        except ValidationError:
            raise ProbeError("invalid_arguments", "Arguments do not match the declared tool schema.") from None
        if isinstance(values, Page):
            return self.page(values)
        if isinstance(values, Create):
            return self.create(values, request_id)
        row = self.db.execute("SELECT receipt FROM jobs WHERE principal=? AND job_id=?",
                              (OWNER, values.job_id)).fetchone()
        if row is None:
            raise ProbeError("not_found", "No such job in this paired instance.")
        if method == "pcs_cancel_test_job":
            raise ProbeError("already_terminal", "This synthetic job already completed.")
        return Receipt.model_validate_json(row[0]).model_dump()

    def page(self, page: Page):
        keys = tuple(TEXT)
        if page.cursor not in (None, "r1-after-blue"):
            raise ProbeError("invalid_cursor", "Unknown cursor for this synthetic collection.")
        start = 1 if page.cursor else 0
        rows = [sample_resource(key).model_dump() for key in keys[start:start+page.limit]]
        return dict(schema_version=1, instance_id=self.instance, version=CURRENT.version, synthetic=True,
                    capabilities=list(TOOLS), model_inference=False, max_jobs=LIMIT, resources=rows,
                    next_cursor="r1-after-blue" if start+len(rows) < len(keys) else None)

    def create(self, args: Create, request_id: str):
        key = args.resource_id
        if key not in TEXT:
            raise ProbeError("not_found", "No such synthetic resource in this instance.")
        raw = TEXT[key].encode()
        source = ref(key, raw)
        span = Selection(source=source, branch_id=None, message_ids=[], start=0, end=len(TEXT[key]))
        bundle = ContextBundle(id="bundle-"+key, revision=REVISION, selected=[span], excluded=[],
                               missing=[], share_mode="selected_content", used_bytes=len(raw), max_bytes=256)
        fingerprint = digest(canonical({"operation":"describe.v1", "instance": self.instance,
            "principal": OWNER, "bundle": bundle.model_dump(), "agent": self.agent.model_dump()}))
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            old = self.db.execute("SELECT fingerprint,receipt FROM jobs WHERE principal=? AND idem=?",
                                  (OWNER, args.idempotency_key)).fetchone()
            if old:
                if old[0] != fingerprint:
                    raise ProbeError("idempotency_conflict", "This key already belongs to different input.")
                return Receipt.model_validate_json(old[1]).model_dump()
            if self.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] >= LIMIT:
                raise ProbeError("capacity", "The synthetic profile has reached its job limit.")
            job_id, result_id = "job-"+uuid4().hex, "result-"+uuid4().hex
            job = Job(id=job_id, instance_id=self.instance, principal=OWNER, request_id=request_id,
                      idempotency_key=args.idempotency_key, input_digest=fingerprint, bundle=bundle,
                      agent=self.agent, skills=self.skills, model=None, status="completed", cancellable=False,
                      max_output_bytes=256, result_id=result_id, error=None)
            text = "Synthetic PCS result: " + TEXT[key]
            result = Result(id=result_id, job_id=job_id, status="completed", text=text,
                            citations=[span], processed=[span], missing=[], output_bytes=len(text.encode()))
            receipt = Receipt(job=job, result=result)
            self.db.execute("INSERT INTO jobs VALUES(?,?,?,?,?)",
                            (job_id, OWNER, args.idempotency_key, fingerprint, receipt.model_dump_json()))
        return receipt.model_dump()
