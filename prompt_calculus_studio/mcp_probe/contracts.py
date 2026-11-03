"""Versioned data contracts. Published schemas use JSON Schema 2020-12."""
from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Id = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$", max_length=96)]
Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$", min_length=64, max_length=64)]
Short = Annotated[str, StringConstraints(min_length=1, max_length=256)]
ByteCount = Annotated[int, Field(ge=0, le=65536)]
ReferenceList = Annotated[list[Id], Field(max_length=32)]
Role = Literal["original", "managed_copy", "index", "task_snapshot", "candidate", "adopted"]
State = Literal["queued", "running", "completed", "failed", "cancelled"]
ErrorCode = Literal["invalid_arguments", "invalid_cursor", "not_found", "unsupported",
                    "idempotency_conflict", "capacity", "already_terminal", "instance_mismatch",
                    "authentication_failed", "permission_denied", "service_offline",
                    "transport_timeout", "transport_error", "storage_error"]

def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")

def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()

class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    schema_version: Literal[1] = 1

class Ref(Record):
    id: Id
    revision: Id
    sha256: Digest

class Resource(Ref):
    title: Short
    source_kind: Literal["synthetic", "document", "conversation"]
    source_id: Id
    logical_location: Short
    role: Role
    capabilities: ReferenceList
    availability: Literal["available", "missing", "changed", "unsupported"]

class Selection(Record):
    source: Ref
    branch_id: Id | None
    message_ids: ReferenceList
    # Half-open Unicode code point offsets in this exact source revision.
    start: Annotated[int, Field(ge=0)]
    end: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def ordered_range(self):
        if self.end < self.start:
            raise ValueError("end precedes start")
        return self

class ContextBundle(Record):
    id: Id
    revision: Id
    role: Literal["task_snapshot"] = "task_snapshot"
    selected: Annotated[list[Selection], Field(min_length=1, max_length=32)]
    excluded: ReferenceList
    missing: ReferenceList
    share_mode: Literal["metadata_only", "local_only", "selected_content"]
    used_bytes: ByteCount
    max_bytes: ByteCount

    @model_validator(mode="after")
    def within_budget(self):
        if self.used_bytes > self.max_bytes:
            raise ValueError("bundle exceeds its byte budget")
        return self

class SkillRevision(Record):
    id: Id
    revision: Id
    source: Ref
    entry: Id
    dependencies: Annotated[list[Ref], Field(max_length=32)]
    requires: ReferenceList
    scope: ReferenceList
    max_output_bytes: ByteCount

class AgentRevision(Record):
    id: Id
    revision: Id
    role_name: Short
    model: Ref | None
    skills: Annotated[list[Ref], Field(max_length=16)]
    capabilities: ReferenceList
    scope: ReferenceList
    output_contract: Id
    max_output_bytes: ByteCount

class Fault(Record):
    code: ErrorCode
    message: Short
    retryable: bool

class ProbeError(Exception):
    def __init__(self, code: ErrorCode, message: str, *, retryable=False):
        self.fault = Fault(code=code, message=message, retryable=retryable)
        super().__init__(message)

class Job(Record):
    id: Id
    instance_id: Id
    principal: Id
    request_id: Id
    idempotency_key: Id
    input_digest: Digest
    bundle: ContextBundle
    agent: Ref
    skills: Annotated[list[Ref], Field(max_length=16)]
    model: Ref | None
    status: State
    cancellable: bool
    max_output_bytes: ByteCount
    result_id: Id | None
    error: Fault | None

class Result(Record):
    id: Id
    job_id: Id
    status: Literal["completed", "cancelled", "failed"]
    role: Literal["candidate"] = "candidate"
    text: Annotated[str, StringConstraints(max_length=1024)]
    citations: Annotated[list[Selection], Field(max_length=32)]
    processed: Annotated[list[Selection], Field(max_length=32)]
    missing: ReferenceList
    output_bytes: ByteCount

    @model_validator(mode="after")
    def measure_bytes(self):
        if len(self.text.encode("utf-8")) != self.output_bytes:
            raise ValueError("output_bytes must match the result text")
        return self

class Receipt(Record):
    job: Job
    result: Result

    @model_validator(mode="after")
    def result_belongs_to_job(self):
        if (self.job.id != self.result.job_id or self.job.result_id != self.result.id or
                self.job.status != self.result.status or self.result.output_bytes > self.job.max_output_bytes):
            raise ValueError("result does not match the accepted job")
        return self

TYPES = (Resource, ContextBundle, SkillRevision, AgentRevision, Job, Result, Fault)

def schemas():
    return {kind.__name__: {"$schema": "https://json-schema.org/draft/2020-12/schema",
                            **kind.model_json_schema()} for kind in TYPES}
