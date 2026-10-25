# Prompt Calculus Studio (PCS)

Keep Markdown notes, reusable prompts and Canvas workflows locally. Save ChatGPT conversations as editable documents, then arrange text, images and parameters for your own ComfyUI workflows.

[繁體中文](README.md) · [Getting started](docs/guide/workspace-089.md) · [Documentation](docs/README.md) · [Beta3 release notes](docs/releases/0.8.9/beta.3.md) (guides in Chinese)

## What it does

- Save selected ChatGPT messages through the Chrome extension, send them to PCS or download JSON/Markdown, and find saved files in download history.
- Edit Markdown with source and reading views side by side, formulas, tables, images, outlines, search, links and version history. Ctrl+V retains available formatting; Ctrl+Shift+V pastes plain text.
- Import selected conversations from local ChatGPT ZIP/JSON files with branches and identifiable attachments. Check the original questions and answers, then export a folder or ZIP.
- Reuse prompt variants, combinations and flows; Canvas copies remain separate from library originals.
- Bind CLIP fields and Stage workflow parameters, run Canvas documents in order, and retain task records and selection progress locally.

Notes, prompt editing and copying work without ComfyUI. Image generation requires your own backend, models and workflows. Keep PCS, ComfyUI and the workflow browser page open.

## This release

Beta3 adds Chrome conversation saving, citation cards and portable document exports. It improves formulas, tables, recovery for missing files and saving under a new name. Tutorials include 59 editable prompt materials, eight note templates, and formula and Markdown references. Optional cherry blossom, rain and snow effects are available in settings. See the [full release notes](docs/releases/0.8.9/beta.3.md).

## Download and start

Use **[0.8.9 Beta3](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/releases/tag/v0.8.9-beta.3)**, a prerelease.

- `PCS-v0.8.9-Beta3-source.zip`: source application, welcome page, tutorials, materials, templates and matching ComfyUI extensions.
- `PCS-v0.8.9-Beta3-Chrome-Connector.zip`: Chrome extension and Windows connection tools for saving to PCS.
- `PCS-v0.8.9-Beta3-ComfyUI.zip`: universal ComfyUI extension.
- `PCS-v0.8.9-Beta3-ComfyUI-legacy-0.39.2.zip` or `PCS-v0.8.9-Beta3-ComfyUI-modern-0.40.0.zip`: alternative compatibility profiles; install one copy only.
- `SHA256SUMS.txt`: download checksums.

The PCS application is distributed as source; **no PCS Windows executable is publicly provided**. Install 64-bit Python 3.12 on Windows, extract the source archive and open PowerShell beside `run.py`:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py --v0.8.9-beta.3
```

The first installation downloads dependencies. Afterwards, double-click `Start.cmd`. Follow the [Chrome setup](docs/guide/chrome-connector.md) to install its connection tools and load the extension. For image generation, install one matching ComfyUI extension, restart the backend and refresh the workflow browser page.

## First use and upgrades

The welcome document links to tutorials for notes, prompt materials, combinations, flows and Canvas. Closing it leaves it closed after restart; saved tabs are restored. Copy tutorials first if you want to keep their initial content.

New documents live in `files`; settings, recovery data and task records live in `data`. Adding a local folder edits files in their original locations. Keep the old application and both data folders when upgrading, try copies first, and avoid running both versions against the same data. See [startup and existing data](docs/guide/workspace-089.md) and [saving and exports](docs/guide/markdown-retention.md).

## Before using

- Chrome captures messages already loaded on the page; selecting all does not include history the site has not loaded.
- Complex charts and widgets may retain only static images, text or source links. Formula characters lost in older data cannot be recovered through display changes.
- Not all Obsidian plugins are supported. This release does not run an LLM automatically; ChatGPT editing uses manual copying and pasting.
- Imported records, attachments, PNG metadata, recovery data and backups can contain private content. [Inspect before sharing](docs/guide/data-backup-privacy.md).

[Current limits](docs/releases/0.8.9/validation/beta.3.md) · [Release history](docs/releases/0.8.9/README.md) · [Issues](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/issues) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)

## License

[AGPL-3.0-only](LICENSE)
