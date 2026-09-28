# Product state contracts

- An empty manual draft (`""`) is valid and differs from automatic text (`None`). Use `drafts.edit/clear`; preserve separate list/canvas drafts and the original `draft_base`.
- Canvas changes go through the existing commit/undo transaction. Preserve ownership, connections and ordering together; visual position is not Prompt order. `multi_output` owns unique field bindings, and unbound workflow fields keep their original values.
- Load/import/restore conversion belongs in `state_loading.prepare_state` and the corresponding storage boundary, not UI refresh. Unsupported data must not replace the saved document; never rebuild historical generation text from today's Prompt. For schema or restore changes read [compatibility](../docs/DATA_COMPATIBILITY.md).
- `ChangeCoordinator` saves and refreshes; it never starts generation. Layout, Notes and settings changes do not unconditionally recompile Prompt or resend it. `refresh=False` does not mean skip required text synchronization.
- Generation uses the state frozen for that run. Reject stale callbacks by operation/data identity; disconnecting stops later submissions but does not prove the server cancelled an accepted job. Do not retry an uncertain submission automatically.
- For workflow selection, Web synchronization, submission or result images, read [workflow contracts](../docs/agent-guides/workflow.md). For CivitAI, Catalog, credentials or download changes, read [asset contracts](../docs/agent-guides/assets.md).
