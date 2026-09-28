# Workflow and image changes

Read this only when changing workflow identity, Web sync, submission, workspace switching or image provenance.

- PCS bindings identify a workspace, server, workflow and target field. Switching the visible Web tab does not silently rebind a PCS field; reject mismatched or stale ownership rather than selecting the “latest” graph.
- An explicit PCS/Web text-source choice controls the submitted field, including a Web manual empty string. Preserve unbound fields and current live parameters. Do not repair a disagreement by silently restoring old JSON, Canvas values or history.
- On the Repair 5 native path, `comfyui_prompt_studio/native_queue.py` validates the actual prepared payload and frontend capability; no live Web session must fail explicitly. Existing background modules and older effective-state tests do not imply that Web-free execution is enabled.
- Where the effective-state protocol is used, accept revisions at the Storage boundary with scope/epoch/base revision and operation receipts; do not bypass conflict checks or report acceptance before persistence. Read the matching protocol document in the target version when changing it.
- Freeze a run's selected workflow, text and input provenance. Match results to the actual run/`prompt_id` and output node; delayed previews or a “latest image” must not replace a connected input. No connected source means no inherited preview.
- Batch preview, selecting one input, and sequential downstream execution are separate contracts. Confirm the task's accepted behavior and current implementation before changing them; neither old single-image selection nor a future batch requirement is a universal default.
- Relevant controls: `test_binding_submission.py` for actual payload and closed-Web behavior; `test_effective_workflow.py` for revisions/persistence; `test_image_collections.py` and `test_node_images.py` for provenance; affected `native_queue`, `workflow_sync`, `source_receipt` and `node_images` JavaScript tests. Use only the suites present in the target version and relevant to the change. Trace the real UI → payload → queue/history → connected result when claiming real workflow acceptance.
