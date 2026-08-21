# TG-06 — Telegram Mini App Capabilities

TG-06 adds a client-only capability layer. When Telegram exposes safe-area or theme fields, the bridge maps them to CSS variables; when it does not, the standalone web experience remains unchanged. The bridge invokes `ready` and `expand` as before and may attach a BackButton callback when that API exists.

No initData validation, Core command, financial record, secret, DeviceStorage, payment, Copy Trading, or live execution behavior is changed. The UI status chip respects `--tma-safe-bottom` so it does not overlap Telegram navigation chrome.

## Verification

- TMA capability unit tests pass.
- TypeScript project verification is delegated to CI with the repository's locked frontend dependency set. The isolated worktree reused the local Web project's dependencies and lacks the repository's `pg` package, producing an unrelated server dependency-resolution error.
