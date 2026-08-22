# Changelog

All notable changes to vision-relay will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- Renamed the Python package `qwen_mm_plugins_proxy` to `vision_relay`; the console script is now `vision-relay` only (the `qwen-mm-plugins-proxy` alias is gone).
- Configuration moved from `~/.qwen-mm-plugins/` to `~/.vision-relay/`; an existing `~/.qwen-mm-plugins/proxy.json` is read automatically and migrates on next save.
- Environment variables renamed `QWEN_MM_PROXY_*` / `QWEN_MM_CONFIG*` to `VISION_RELAY_*` (legacy names still honored with a deprecation warning).
- Harness wiring backups now use the `.vision-relay.bak` suffix; older `.qwen-mm-proxy.bak` backups are still recognized by `vision-relay stop`.

### Removed
- Upstream plugin-marketplace packaging: `src/capabilities/proxy/` manifests, `install.sh`, `install-proxy.sh`, and their tests. Install with `pip install vision-relay` and run `vision-relay start`.

### Added
- `vision-relay --version` flag.
- Ported design docs from the Qwen-MM-Plugins-plus development repo: Phase-1 spec, Phase-2 roadmap, implementation plan, acceptance checklist, manual test guide, and ecosystem research; PR #40 body archived under `docs/history/`.
- Repository scaffolding: PR template, feature-request issue form, AGENTS.md, CHANGELOG, and a CI matrix for Python 3.10–3.13 on Linux/macOS/Windows plus a build-and-install smoke job.
- M1 control plane — `refresh` / `diagnose` verbs, reconcile engine with intent-based auto-repair, tool dossiers, modality probe (tri-state), takeover snapshots, file lock, vision call records, per-harness VLM, tri-state capability store (image terminology).
- M2（GUI）：Tauri 2 + React 控制台（5 页 + 两步向导 + 托盘与关闭确认），新增写动词 models-set/vlm-set/vlm-test/settings-set/relay-set/probe --json/models-fetch，status 总览增强
