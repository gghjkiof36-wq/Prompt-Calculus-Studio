# ComfyUI extension boundaries

- This extension must import without Qt. Shared implementation comes from `../prompt_studio/` through `../build_comfyui.py` and `releases.SHARED_MODULES`; generated package `shared/` is not an independent source.
- Keep existing route/node/storage identifiers compatible during branding changes. A new package folder name does not authorize rewriting saved workflows or ComfyUI user data.
- For bridge, native queue, Web state or result-image changes, read [workflow contracts](../docs/agent-guides/workflow.md). Validate both Python service behavior and the affected JavaScript protocol; a browser stub alone does not establish compatibility with a real ComfyUI frontend.
- Preserve local-route peer/Host/Origin and request-token checks; an origin-less native client and a browser with `Origin: null` are different cases. Service/route changes need the relevant existing security counterexamples, not a blanket relaxation to make one client work.
