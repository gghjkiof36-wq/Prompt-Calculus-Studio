# Prompt Calculus Studio (PCS)

Keep notes, prompts, conversations, and project resources on your computer. Chat with your own models, use documents and Skills as references, and bring text and materials into Canvas workflows for ComfyUI.

[繁體中文](README.md) · [Getting started](docs/guide/workspace-0810.md) · [Documentation](docs/README.md) · [Pre-alpha3 changes](docs/releases/0.8.10/pre-alpha.3.md)

## What you can do

- **Write and read notes:** View Markdown source and rendered content side by side, with math, tables, images, outlines, search, links, and revision history. Rewrite a selection or draft with a local model, then review and keep the result.
- **Save and organize conversations:** Save selected ChatGPT messages with the Chrome extension. Import ChatGPT ZIP/JSON, Codex JSONL, or PCS conversations and export individual Markdown documents with their sources and available attachments.
- **Manage local models and chats:** Register GGUF files, configure context, thinking, and GPU settings, and save attachments, message versions, branches, and timing in `.pcs-chat` documents.
- **Use Skills and Agents by project:** Add files and Skills under Work Resources, set project rules and Agent roles, and use them in chat to search and read configured sources. Inspect the reading record, or submit a task through the Windows command-line tool and retrieve its draft.
- **Reuse prompts and run workflows:** Organize prompt variants, compositions, and flows, then connect CLIP and Stage modules on a Canvas to your own ComfyUI models and workflows.

## This release

Pre-alpha3 improves Canvas scheduling, result retention, and large image previews. Add another batch while generation is running, and reconnect to check and continue existing work after an interruption. Install the matching ComfyUI extension and migrate a copy of your data when updating. [Update and operation guide](docs/guide/canvas-prealpha3.md).

## Download and start

Download **[0.8.10 Pre-alpha3](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/releases/tag/v0.8.10-pre-alpha.3)** (preview).

| File | Purpose |
|---|---|
| `PCS-v0.8.10-Pre-alpha3-source.zip` | Application source, lessons, materials, templates, and matching ComfyUI packages |
| `PCS-v0.8.10-Pre-alpha3-Chrome-Connector.zip` | Chrome 0.2.7 extension and Windows connection tools |
| `PCS-v0.8.10-Pre-alpha3-ComfyUI.zip` | General ComfyUI package |
| `PCS-v0.8.10-Pre-alpha3-ComfyUI-legacy-0.39.2.zip`, `PCS-v0.8.10-Pre-alpha3-ComfyUI-modern-0.40.0.zip` | Compatibility profiles; choose one package |
| `SHA256SUMS.txt` | Download integrity checksums |

The application is distributed as source. Install 64-bit Python 3.12 for Windows, extract the full source package, and open PowerShell beside `run.py`:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Then double-click **`Start.vbs`**. The initial setup downloads dependencies; startup logs are saved under `logs`. For a terminal session, run `.\.venv\Scripts\python.exe run.py --v0.8.10-pre-alpha.3`.

For local AI, provide a GGUF model and a supported `llama-server` using the [model setup guide](docs/guide/local-ai.md). Follow the [Chrome connection steps](docs/guide/chrome-connector.md) to save web conversations, or the [ComfyUI guide](docs/guide/comfyui-compatibility.md) to connect image-generation workflows.

## First use and updates

Start with `歡迎使用.md` for editable notes, materials, and Canvas lessons. Documents default to `files`; settings and internal records go in `data`. Added folders are edited at their original locations. Keep your previous program and data, try the new version with copies, and use one version at a time for shared data. [Startup and existing data](docs/guide/workspace-0810.md).

## Before using

- Some large operations still pause the interface. Keep your previous data copy for use with older versions.

- Agent analysis and citations can contain errors, especially in long tasks. Review the sources. Skills currently supply reading material and guidance.
- Local AI handles one task at a time. Use Windows commands for external delegation, or approve a local child task from an Agent chat.
- Chrome saves messages already loaded on the page. Charts and attachments are retained according to the content available.
- Conversations, reading records, attachments, and backups can contain private information. [Check before sharing](docs/guide/data-backup-privacy.md).

[Validation notes](docs/releases/0.8.10/validation/pre-alpha.3.md) · [Release history](docs/releases/README.md) · [Issues](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/issues) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)

## License

[AGPL-3.0-only](LICENSE)
