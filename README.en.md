# Prompt Calculus Studio (PCS)

Keep Markdown notes, reusable prompts and Canvas workflows in a local workspace. Save text variants, combinations and flows, then send text and images to your own ComfyUI setup. Notes and prompt editing also work without a generation service.

[繁體中文](README.md) · [Setup](docs/guide/workspace-089.md) · [Documentation](docs/README.md) · [0.8.9 release notes](docs/releases/0.8.9/alpha.1.md) (Chinese)

## What it does

- Open notes, prompt categories and Canvas workflows in tabs, including side-by-side views.
- Write Markdown or inspect its source and reading view. Keep tables, formulas and Mermaid diagrams editable.
- Organize reusable text variants, ordered combinations and flows with repeat settings.
- Prepare batches, bind text fields, choose workflows and queue or retry tasks with their saved inputs.
- Find local notes through outlines, search, tags, links, bookmarks and version history.

Generation requires your own ComfyUI installation, models and workflows.

## Screenshots

### Notes beside prompt materials

Record the scene on the left and inspect reusable prompt text on the right.

![PCS 0.8.9 notes and prompt materials](docs/images/workspace-split-0.8.9.png)

### Markdown source beside its reading view

Tables, diagrams and formulas retain their editable source in the same document.

![PCS 0.8.9 Markdown source and reading](docs/images/markdown-source-reading-0.8.9.png)

### Outlines and a sidebar that appears when needed

Navigate by heading. When the content sidebar is collapsed, move near the left edge to reveal it; opening and closing delays can be changed in settings.

![PCS 0.8.9 Markdown outline](docs/images/markdown-outline-0.8.9.png)

![PCS 0.8.9 temporary sidebar](docs/images/sidebar-peek-0.8.9.png)

## Download and start

Current version: **[0.8.9 Alpha 1](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/releases/tag/v0.8.9-alpha.1)**.

- `PCS-v0.8.9-Alpha1-source.zip`: source application, bundled extension profiles and installer.
- `PCS-v0.8.9-Alpha1-ComfyUI.zip`: universal extension for manual installation.
- `PCS-v0.8.9-Alpha1-ComfyUI-legacy-0.39.2.zip` or `PCS-v0.8.9-Alpha1-ComfyUI-modern-0.40.0.zip`: alternative compatibility profiles; install one copy only.
- `SHA256SUMS.txt`: download checksums.

No Windows executable is publicly distributed. The source edition needs 64-bit Python 3.12 on Windows. Extract the whole archive and open PowerShell beside `run.py`:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py --v0.8.9-alpha
```

The first installation downloads dependencies. After that, double-click `Start.cmd`. Update the ComfyUI extension as well, restart the backend and refresh the workflow browser page. See [setup](docs/guide/workspace-089.md) and [compatibility](docs/guide/comfyui-compatibility.md) (Chinese).

## First use

Create a Markdown note, prompt category or Canvas from the content sidebar. Write a note, save a text material, then add a separate copy of the material to Canvas. New documents default to `files` beside the program; settings use `data`. Adding a local folder opens its actual files, so edits are saved to their original locations.

## Before using it

- Back up the old program, extension and original data. Try an independent data folder and copies of your content when upgrading. Compatibility with old databases still needs confirmation; do not let old and new applications write the same data at once.
- Markdown files near 8 MiB with many short paragraphs can pause when first switching to the reading view. Complex content may take several seconds to finish rendering.
- Input methods, display scaling, multiple monitors, media formats and ComfyUI/GPU setups have not all been checked. See [known limitations](docs/releases/0.8.9/validation/alpha.1.md) (Chinese).
- Generation images retain necessary prompts and restoration references; full backups contain the private library. [Inspect files before sharing](docs/guide/data-backup-privacy.md#分享前) (Chinese).

[Release history](docs/releases/README.md) · [Issues](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/issues) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)

## License

[AGPL-3.0-only](LICENSE)
