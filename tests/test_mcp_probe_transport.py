"""Synthetic real-process MCP client, adapter and PCS named-pipe owner."""
import asyncio
from contextlib import asynccontextmanager, contextmanager
from datetime import timedelta
import importlib.util
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
from uuid import uuid4

if sys.platform != "win32" or not importlib.util.find_spec("mcp"):
    raise unittest.SkipTest("optional Windows PCS MCP probe environment required")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import win32api
import win32event
from prompt_calculus_studio.mcp_probe.contracts import Receipt
from prompt_calculus_studio.mcp_probe.local import make_pair, read_pair

ROOT = Path(__file__).resolve().parents[1]

def command(mode, directory, instance):
    return ["-m", "prompt_calculus_studio.mcp_probe", mode, "--profile", str(directory), "--instance", instance]

@contextmanager
def owner(directory, instance):
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errors:
        p = subprocess.Popen([sys.executable,*command("owner", directory, instance)], cwd=ROOT,
                             stdout=subprocess.PIPE, stderr=errors, creationflags=subprocess.CREATE_NO_WINDOW)
        ready = queue.Queue()
        worker = None
        threading.Thread(target=lambda: ready.put(p.stdout.readline()), daemon=True).start()
        try:
            line = ready.get(timeout=15)
            if not line:
                errors.seek(0)
                raise RuntimeError(errors.read())
            info = json.loads(line)
            assert info["instance_id"] == instance
            # Windows venv launches a separate interpreter. Pin this fixture's real
            # service process before yielding so an exit is observable, not assumed.
            worker = win32api.OpenProcess(0x00100001, False, info["pid"])  # SYNCHRONIZE | TERMINATE
            yield
        finally:
            if worker is not None:
                try:
                    if win32event.WaitForSingleObject(worker, 0) == win32event.WAIT_TIMEOUT:
                        win32api.TerminateProcess(worker, 0)
                    assert win32event.WaitForSingleObject(worker, 5000) == win32event.WAIT_OBJECT_0
                finally:
                    worker.Close()
            elif p.poll() is None:
                p.terminate()
            p.wait(timeout=5)
            p.stdout.close()

@asynccontextmanager
async def connected(directory, instance):
    params = StdioServerParameters(command=sys.executable, args=command("stdio", directory, instance),
                                   cwd=ROOT, env={"PYTHONUTF8":"1"})
    async with stdio_client(params) as (read,write):
        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=8)) as session:
            init = await session.initialize()
            yield session, init

class ProbeTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_official_client_live_owner_offline_auth_and_isolation(self):
        self.calls = []
        async def call(session, name, args, code=None):
            res = await session.call_tool(name, args)
            value = res.structuredContent
            self.assertEqual(json.loads(res.content[0].text), value)
            self.assertIs(res.isError, code is not None, value)
            self.assertEqual(value["ok"], code is None, value)
            if code:
                self.assertEqual(value["error"]["code"], code, value)
            self.calls.append(dict(tool=name, arguments=args, outcome=value))
            return value.get("data")
        namespace = str(uuid4())
        other = str(uuid4())
        with tempfile.TemporaryDirectory() as area:
            directory = Path(area)/"synthetic"
            directory.mkdir()
            async with connected(directory, namespace) as (session, init):
                self.assertEqual(init.serverInfo.version, "0.8.10-pre-alpha.2")
                tools = (await session.list_tools()).tools
                self.assertEqual(len(tools),4)
                self.assertFalse(next(t for t in tools if t.name=="pcs_create_test_job").annotations.readOnlyHint)
                await call(session, "pcs_probe_resources", {}, "service_offline")
                with owner(directory, namespace):
                    page = await call(session, "pcs_probe_resources", {"limit":1})
                    self.assertEqual(page["instance_id"], namespace)
                    second = await call(session, "pcs_probe_resources", {"cursor":page["next_cursor"]})
                    self.assertIsNone(second["next_cursor"])
                    await call(session, "pcs_probe_resources", {"cursor":"foreign"}, "invalid_cursor")
                    await call(session, "pcs_probe_resources", {"limit":True}, "invalid_arguments")
                    await call(session, "pcs_unknown_test_operation", {}, "unsupported")
                    args = {"resource_id":page["resources"][0]["id"], "idempotency_key":"stage-one"}
                    receipt = await call(session, "pcs_create_test_job", args)
                    Receipt.model_validate(receipt)
                    self.assertEqual(await call(session, "pcs_create_test_job", args), receipt)
                    await call(session, "pcs_create_test_job", dict(args, resource_id="sample-green"), "idempotency_conflict")
                    lookup = {"job_id": receipt["job"]["id"]}
                    self.assertEqual(await call(session, "pcs_get_test_job", lookup), receipt)
                    await call(session, "pcs_get_test_job", {"job_id":"unknown"}, "not_found")
                    await call(session, "pcs_get_test_job", dict(lookup, principal="operator2"), "invalid_arguments")
                    await call(session, "pcs_get_test_job", {"job_id":"../secret"}, "invalid_arguments")
                    await call(session, "pcs_cancel_test_job", lookup, "already_terminal")
                    async with connected(directory, other) as (wrong, _):
                        await call(wrong, "pcs_probe_resources", {}, "instance_mismatch")
                    b = Path(area)/"other"
                    with owner(b, other):
                        async with connected(b, other) as (foreign, _):
                            await call(foreign, "pcs_get_test_job", lookup, "not_found")
                    pairing = directory/"a01-pair.bin"
                    old = pairing.read_bytes()
                    # A valid current-user encrypted record still cannot forge the live owner's key.
                    pipe, _ = read_pair(directory, namespace)
                    make_pair(directory, namespace, pipe.rsplit("-", 1)[1], bytes(32))
                    await call(session, "pcs_probe_resources", {}, "authentication_failed")
                    pairing.write_bytes(b"corrupt test pairing")
                    await call(session, "pcs_probe_resources", {}, "authentication_failed")
                    pairing.write_bytes(old)
                await call(session, "pcs_get_test_job", lookup, "service_offline")
                with owner(directory, namespace):
                    self.assertEqual(await call(session, "pcs_get_test_job", lookup), receipt)
                if output := os.environ.get("PCS_A01_TEST_RECEIPT"):
                    Path(output).write_text(json.dumps(dict(provenance="official SDK client, not ChatGPT",
                        initialization=init.model_dump(mode="json"), tools=[t.name for t in tools],
                        calls=self.calls), ensure_ascii=False, indent=2), encoding="utf-8")
