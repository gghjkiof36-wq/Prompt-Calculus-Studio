"""MCP stdio adapter. The host may start this process while the PCS owner is offline."""
import asyncio
from pathlib import Path

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from .contracts import ProbeError, canonical
from .local import exchange, failure
from .service import TOOLS
from ..releases import CURRENT

async def serve(directory: Path, instance: str):
    pending = set()

    def finished(future):
        pending.discard(future)
        if not future.cancelled():
            future.exception()  # also consume errors after a host disconnect
    server = Server("pcs-synthetic-probe", version=CURRENT.folder.lstrip("v"),
                    instructions="A private synthetic PCS connection test. Resource and job IDs are opaque.")

    @server.list_tools()
    async def list_tools():
        return [types.Tool(name=name, description=description, inputSchema=schema.model_json_schema(),
                           annotations=types.ToolAnnotations(readOnlyHint=read_only, destructiveHint=False,
                                                            idempotentHint=True, openWorldHint=False))
                for name, (schema, description, read_only) in TOOLS.items()]

    @server.call_tool(validate_input=False)
    async def call_tool(name, arguments):
        try:
            if name not in TOOLS:
                raise ProbeError("unsupported", "Unknown synthetic probe operation.")
            if len(pending) >= 8:
                raise ProbeError("capacity", "The paired probe is busy.", retryable=True)
            future = asyncio.get_running_loop().run_in_executor(None, exchange, directory, instance, name, arguments)
            pending.add(future)
            future.add_done_callback(finished)
            # The Windows operation retains its slot until it exits, even if the
            # host cancels the wait after a receipt may already have committed.
            outcome = await asyncio.shield(future)
        except ProbeError as error:
            outcome = failure(error)
        return types.CallToolResult(content=[types.TextContent(type="text", text=canonical(outcome).decode())],
                                    structuredContent=outcome, isError=not outcome["ok"])

    async with stdio_server() as (reader, writer):
        await server.run(reader, writer, server.create_initialization_options())
