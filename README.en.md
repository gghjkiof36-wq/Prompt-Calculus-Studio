# Prompt Calculus Studio (PCS)

[繁體中文](README.md)

PCS is a local-first Windows tool for composing reusable prompts and connecting them to ComfyUI. Organize characters, styles and scenes as modules, arrange them in a list or Canvas, and keep manual text and saved workspaces.

## What it does

- Compose prompts with ordering, weights and tag exclusions.
- Arrange Canvas modules, text destinations and image connections.
- Choose PCS text or ComfyUI manual text for an explicitly bound CLIP field.
- Submit to the native ComfyUI queue, view output batches, and route image sources through optional pre-scheduling.
- Organize local models and images, and inspect available PNG metadata and module snapshots.

## Download and start

These instructions target **0.83 Alpha 1**, a prerelease. Get `PCS-v0.83-Alpha1-source.zip`, the matching `PCS-v0.83-Alpha1-ComfyUI.zip`, and `SHA256SUMS.txt` from the [release page](https://github.com/gghjkiof36-wq/Prompt-Culculus-Studio/releases/tag/v0.83-alpha.1). Availability is determined by the actual published assets. No Windows EXE is included. The earlier [0.82 Repair 5 release](https://github.com/gghjkiof36-wq/Prompt-Culculus-Studio/releases/tag/v0.82-alpha.1-repair.5) and [0.81 release](https://github.com/gghjkiof36-wq/Prompt-Culculus-Studio/releases/tag/v0.81-alpha.2-ui-repair.1) remain available with their own instructions.

Use Windows 64-bit and Python 3.12. Extract the source ZIP and run these commands beside `run.py`:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py --v083-alpha
```

Choose the list or Canvas interface, select a module and an item, adjust the resulting prompt, then copy the text. For generation, install the matching extension, restart ComfyUI and refresh its browser page; connect the desktop library and bind the intended workflow/CLIP field. See [installation](docs/GETTING_STARTED.md) and the [ComfyUI guide](COMFYUI_GUIDE.md) (Chinese).

## Limits

Keep PCS, ComfyUI and the bound browser page open. Without pre-scheduling, each click submits directly to the native ComfyUI queue using the text and image captured at that click; a count of 3 submits 3 jobs. With pre-scheduling connected, an idle workflow starts immediately. Only later input arriving while busy is saved for execution after the current job fully completes. Fields not routed through pre-scheduling are read at dispatch time.

The bottom bar contains count, run, cancel and task count. Cancellation targets the specified PCS job, leaving unrelated native jobs alone. Pausing or closing PCS does not lock ComfyUI's native Run button. Previous waiting records are retained without automatic replay.

Image collections follow click counts without pre-scheduling; connected pre-scheduling supports batch supply with up to ten active/waiting items and refill after completion. Browser-free execution, automatic cross-workflow chaining and AI text interpretation are outside this release. The user reported normal operation after updating; this is not a claim that every GPU or environment was tested.

The application title retains “v0.83 直接執行修復候選（0928）” candidate wording for the fixed product source. [Validation](docs/validation/083_DIRECT.md) separates the maintainer-reported manual acceptance from developer checks and unverified environments. Static review also identified two unconfirmed risks: a never-returning submission may keep native Run waiting, and a failed earlier image job may cause a later click to select an already queued image. These are recorded for a future version. A public screenshot for this version is not yet available. Models, Python/Qt and private data are not bundled.

## Contribute and license

Report reproducible issues with versions and steps at [Issues](https://github.com/gghjkiof36-wq/Prompt-Culculus-Studio/issues). Remove tokens, private images and local paths first. Read [CONTRIBUTING](CONTRIBUTING.md) and [SECURITY](SECURITY.md).

Original project code and documentation use [AGPL-3.0-only](LICENSE); third-party components retain their own licenses. See [licensing](docs/LICENSING.md).
