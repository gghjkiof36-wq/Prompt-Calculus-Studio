# Asset and credential changes

- `Catalog.update_identified_models` merges source metadata after checking current path/size/mtime/hash. Preserve the user's latest name, Notes, Trigger and preview overrides, including edits made while a background request was running.
- Type, Base Model and personal category are separate fields. Missing remote metadata stays missing; do not invent values or use one category as another.
- Download replacement, cancellation, hash status and failed registration retain their recovery behavior. Distinguish official-hash match, no official hash and failed verification; do not overwrite a same-name file silently.
- Network/Token epochs and selection identity invalidate stale responses. Preserve credential restrictions across redirects and sanitized errors/receipts. Use synthetic secrets to verify these boundaries, never print a real Token.
- Windows DPAPI in `prompt_studio/credentials.py` is account-bound storage, not portable credential recovery. ZIP backups exclude credentials and external models/images. For changes to export or recovery read [data/privacy](../DATA_AND_PRIVACY.md); verify affected `test_civitai_repairs`, `test_civitai_reinstall_security` and `test_api_redirect` cases in the target version.
