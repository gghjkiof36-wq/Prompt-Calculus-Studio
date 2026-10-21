# Prompt Calculus Studio (PCS)

Keep Markdown notes, reusable prompts and Canvas workflows locally. Paste formatted text or turn conversation archives into editable notes, then arrange prompt materials and images for your own ComfyUI workflows.

[繁體中文](README.md) · [Documentation](docs/README.md)

## What it does

- Edit Markdown with source and reading views side by side, outlines, search, links and version history.
- Paste Markdown or HTML formatting with Ctrl+V; use Ctrl+Shift+V for plain text.
- Import selected conversations from local ChatGPT ZIP/JSON files, including all branches, the selected raw record and identifiable attachments.
- Reuse text variants, combinations and flows; Canvas copies remain separate from library originals.
- Bind workflow fields and parameters, run Canvas documents in order, and retain task records and selection progress locally.

Notes, prompt editing and copying work without ComfyUI. Generation requires your own backend, models and workflows. Keep PCS, ComfyUI and the workflow browser page open.

## This release

This release adds formatted paste and selective conversation import, and updates reading, source-column sizing, sidebar tab dragging and model details. Closing documents saves edits or retains a recovery copy where writable storage is available. Viewing a material no longer marks it as changed. Tabbed Canvas binding and task recovery are also updated. See the [release notes](docs/releases/0.8.9/beta.md) (Chinese).

## Download and start

Use **[0.8.9 Beta](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/releases/tag/v0.8.9-beta)**, a prerelease.

- `PCS-v0.8.9-Beta-source.zip`: source application, welcome page, tutorials, extension and installer.
- `PCS-v0.8.9-Beta-ComfyUI.zip`: universal extension.
- `PCS-v0.8.9-Beta-ComfyUI-legacy-0.39.2.zip` or `PCS-v0.8.9-Beta-ComfyUI-modern-0.40.0.zip`: alternative compatibility profiles; install one copy only.
- `SHA256SUMS.txt`: download checksums.

No Windows executable is publicly distributed in this release. Install 64-bit Python 3.12 on Windows, extract the source archive and open PowerShell beside `run.py`:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py --v0.8.9-beta
```

The first installation downloads dependencies. Afterwards, double-click `Start.cmd`. For generation, install the matching extension, restart the backend and refresh the workflow browser page yourself. The old preparation.7 extension supports formats 1–6; format 7 needs an updated extension. Check the loaded version after installation. See [setup](docs/guide/workspace-089.md) and [compatibility](docs/guide/comfyui-compatibility.md) (Chinese).

## First use

The welcome document links to tutorials in the “入口” folder: editable coastal notes, prompt materials, combinations and flows. New Canvas documents start with six connected modules. Closing the welcome page leaves it closed when you restart; saved tabs are restored. Copy tutorials first if you want to retain their initial content.

New documents live in `files`; settings, recovery data and task records live in `data`. Adding another local folder edits files in their original locations. Back up and use copies when upgrading. See [formatted paste and conversation import](docs/guide/markdown-import.md) (Chinese).

## Known limitations

- Older imports that copied the entire conversation source are not cleaned automatically. Re-import into a new copy, retain your edited notes and inspect attachments before sharing.
- After closing another view of the same file, a second large HTML paste may remain at the preparation stage. Existing text and disk content remain unchanged; typing, saving and closing still work. Close and reopen the affected page before pasting again.
- Large documents can still pause. The cause of occasional CivitAI exits remains unresolved. Real archives, complete native UI/media/IME/DPI coverage, old-database acceptance and live ComfyUI/GPU use have validation limits; see the [current summary](docs/releases/0.8.9/validation/beta.md).
- PNG metadata, imported raw records, attachments, recovery data and backups can contain private content. [Inspect before sharing](docs/guide/data-backup-privacy.md) (Chinese).

[Release history](docs/releases/0.8.9/README.md) · [Issues](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/issues) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)

## License

[AGPL-3.0-only](LICENSE)
