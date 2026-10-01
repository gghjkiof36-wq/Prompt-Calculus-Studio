# Prompt Calculus Studio (PCS)

[Version naming and legacy mapping](docs/VERSIONING.md)

[繁體中文](README.md)

PCS is a local-first Windows tool for composing reusable prompts and connecting them to ComfyUI. Organize characters, styles and scenes as modules, arrange them in a list or Canvas, and keep manual text and saved workspaces.

## What it does

- Compose prompts with ordering, weights and tag exclusions.
- Arrange Canvas modules, text destinations and image connections.
- Apply the text connected to CLIP to its explicitly bound field, preserving unbound browser fields.
- Use Stage nodes for generation, chain linear workflows, and route current-run images through data or Stage pre-scheduling.
- Organize local models and images, and inspect available PNG metadata and module snapshots.

## Download and start

These instructions target **0.8.4 Alpha 1**, a prerelease. Get `PCS-v0.8.4-Alpha1-source.zip`, the matching `PCS-v0.8.4-Alpha1-ComfyUI.zip`, and `SHA256SUMS.txt` from the [release page](https://github.com/gghjkiof36-wq/Prompt-Culculus-Studio/releases/tag/v0.8.4-alpha.1). Availability is determined by the actual published assets. **This release does not provide a Windows executable download.** Distribution materials for its runtime still need to be completed; use the source instructions below. The earlier [0.8.2 Repair 5 release](https://github.com/gghjkiof36-wq/Prompt-Culculus-Studio/releases/tag/v0.8.2-alpha.1.repair.5) and [0.8.1 release](https://github.com/gghjkiof36-wq/Prompt-Culculus-Studio/releases/tag/v0.8.1-alpha.2.ui-repair.1) remain available with their own instructions.

For the source edition, use Windows 64-bit and Python 3.12. Extract the source ZIP and run these commands beside `run.py`:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py --v0.8.4-alpha
```

Choose the list or Canvas interface, select a module and an item, adjust the resulting prompt, then copy the text. For generation, install the matching extension, restart ComfyUI and refresh its browser page; connect the desktop library and bind the intended workflow/CLIP field. See [installation](docs/GETTING_STARTED.md) and the [ComfyUI guide](COMFYUI_GUIDE.md) (Chinese).

## Limits

Keep PCS, ComfyUI and its native browser page open. Stage is the generation entry point: connect CLIP or image-input control outputs to a Stage. Run applies the bound inputs and generates the Stage's selected workflow; without a valid Stage connection it only applies inputs. Editing PCS text alone does not overwrite browser fields.

A single Stage accepts additional clicks while generating. Multiple Stages follow control connections and result dependencies; count means complete workflow rounds. Linear A→B→C and A→B→A with separate Stage instances are supported. Downstream inputs can select the current Stage's image node and batch images rather than reusing old results.

Data pre-scheduling saves selected text or images. Stage pre-scheduling saves a Stage or group, with saved/live choices for each input. Image lists supply up to ten unfinished items and refill after completion. Cancellation only targets confirmed PCS jobs.

A native workflow loader that never returns still requires refreshing ComfyUI. Third-party Run wrappers that cannot be safely isolated also require a refresh. Unknown submissions are not automatically resent. Browser-free execution, AI interpretation, Manager integration, conditional branches and loops are outside this release.

The application title is “v0.8.4 Alpha 1”. [Validation](docs/validation/084_STAGE.md) separates the maintainer-reported manual acceptance from developer checks and unverified environments. User acceptance and local offscreen EXE startup checks are reported separately; neither implies full clean-machine or EXE GPU coverage or public EXE availability. A public screenshot for this version is not yet available. Source and extension archives do not bundle Python/Qt, models or private data.

## Contribute and license

Report reproducible issues with versions and steps at [Issues](https://github.com/gghjkiof36-wq/Prompt-Culculus-Studio/issues). Remove tokens, private images and local paths first. Read [CONTRIBUTING](CONTRIBUTING.md) and [SECURITY](SECURITY.md).

Original project code and documentation use [AGPL-3.0-only](LICENSE); third-party components retain their own licenses. See [licensing](docs/LICENSING.md).
