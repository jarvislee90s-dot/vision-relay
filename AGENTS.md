# AGENTS.md — working rules for AI coding agents in this repo

These rules keep agent-driven changes consistent with the design in
`docs/superpowers/specs/`. Read them before touching code.

## Project identity (naming alignment)

| Layer | Name |
|---|---|
| PyPI package / repo | `vision-relay` |
| Python package (import) | `vision_relay` |
| Console command | `vision-relay` |
| Config directory | `~/.vision-relay/` (config: `proxy.json`, logs: `logs/proxy.log`) |
| Env vars | `VISION_RELAY_*` (`VISION_RELAY_CONFIG_DIR`, `VISION_RELAY_VLM_API_KEY`, ...) |

Legacy `QWEN_MM_*` env names and `~/.qwen-mm-plugins/proxy.json` are read-only
compatibility fallbacks — never write to them, never use them in new code.

`qwen-code` / `claude` / `codex` inside `HARNESSES`, `HARNESS_CFG`, and
`_HARNESS_BY_PROTO` are *harness adapters* (they point at real config files
like `~/.qwen/settings.json`), not project naming. Do not "de-Qwen" them.
Default VLM model names (`qwen-vl-max`, DashScope base URL) are real service
defaults, not project naming either.

## Architecture invariants

- Protocol parsing/serialization lives only in `ir.py` (IR + parse_*/serialize_*).
  All three protocols are parse/serialize shells around the IR; image handling
  is written once against the IR (`pipeline.py`), never per-protocol.
- fail-open is a hard invariant: any proxy-internal failure (VLM down, parse
  error, overflow) must degrade to a text injection, never a 4xx/deadlock.
- Vision models pass through untouched; text-only models go through the
  pipeline; unknown models default to text-only (safe side).
- API keys live only in `~/.vision-relay/proxy.json` (0600) or env; logs must
  never contain keys (`log_json` strips them).
- The proxy must remain the first hop: wiring rewrites harness base_url to
  `http://127.0.0.1:<bind_port>` with backup + guarded restore.

## Workflow rules

- Tests first for behavior changes: `python -m pytest -q` must stay green;
  add tests for new behavior in the matching `tests/test_proxy_*.py`.
- Before opening a PR: `ruff format --check .`, `ruff check .`, full pytest.
- Behavior changes update the matching spec under `docs/superpowers/specs/`
  in the same PR.
- Docs follow the superpowers convention: `docs/superpowers/specs/` (design),
  `docs/superpowers/plans/` (implementation), `research/` (surveys),
  `docs/history/` (as-written records).
- Never commit API keys, private test media, or machine-specific paths.
