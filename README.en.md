# Prompt Calculus Studio (PCS)

[繁體中文](README.md)

PCS is a local-first Windows tool for composing reusable prompts and connecting them to ComfyUI. Organize characters, styles and scenes as modules, arrange them in a list or Canvas, and keep manual text and saved workspaces.

## What it does

- Compose prompts with ordering, weights and tag exclusions.
- Arrange Canvas modules, text destinations and image connections.
- Choose PCS text or ComfyUI manual text for an explicitly bound CLIP field.
- Submit one open ComfyUI workflow once, view a complete output batch, and select one image for a bound LoadImage input.
- Organize local models and images, and inspect available PNG metadata and module snapshots.

## Download and start

These instructions target **0.82 Alpha 1 Repair 5**, a prerelease. Get `PCS-v0.82-Alpha1-Repair5-source.zip`, the matching `PCS-v0.82-Alpha1-Repair5-ComfyUI.zip`, and `SHA256SUMS.txt` from the [release page](https://github.com/gghjkiof36-wq/Prompt-Culculus-Studio/releases/tag/v0.82-alpha.1-repair.5). Availability is determined by the actual published assets. No Windows EXE is included. The earlier [0.81 release](https://github.com/gghjkiof36-wq/Prompt-Culculus-Studio/releases/tag/v0.81-alpha.2-ui-repair.1) remains available with its own instructions.

Use Windows 64-bit and Python 3.12. Extract the source ZIP and run these commands beside `run.py`:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py --v082-alpha
```

Choose the list or Canvas interface, select a module and an item, adjust the resulting prompt, then copy the text. For generation, install the matching extension, restart ComfyUI and refresh its browser page; connect the desktop library and bind the intended workflow/CLIP field. See [installation](docs/GETTING_STARTED.md) and the [ComfyUI guide](COMFYUI_GUIDE.md) (Chinese).

## Limits

Keep the bound ComfyUI browser page open. Each PCS execution submits one workflow once; the workflow's own batch size can be greater than one. Cross-workflow execution, multiple rounds, browser-free background submission, image-only workflows without CLIP bindings, and sequential downstream processing of every image are not completed. Image input requires an explicit single-image choice. Unbound settings follow the current browser workflow.

The application title retains “0927-2” candidate wording for the fixed product source. [Validation](docs/validation/082_REPAIR5.md) separates the maintainer-reported manual acceptance from developer checks and unverified environments. A public screenshot for this version is not yet available. Models, Python/Qt and private data are not bundled.

## Contribute and license

Report reproducible issues with versions and steps at [Issues](https://github.com/gghjkiof36-wq/Prompt-Culculus-Studio/issues). Remove tokens, private images and local paths first. Read [CONTRIBUTING](CONTRIBUTING.md) and [SECURITY](SECURITY.md).

Original project code and documentation use [AGPL-3.0-only](LICENSE); third-party components retain their own licenses. See [licensing](docs/LICENSING.md).
