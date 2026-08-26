## What changed

<!-- One-paragraph summary; link related issues -->

## Why

<!-- The problem this solves and the reason for this approach -->

## Verification

<!-- Commands you ran and their results -->

- [ ] `python -m pytest -q` passes
- [ ] `ruff format --check .` and `ruff check .` pass
- [ ] `pnpm test` (in `gui/`) passes when frontend/GUI code changed
- [ ] Behavior changes update the matching spec under `docs/superpowers/specs/`

## Compatibility

- [ ] No breaking change to CLI commands/flags
- [ ] No breaking change to `proxy.json` config keys (or documented in CHANGELOG)
- [ ] Protocol handling (ir.py parse/serialize) unchanged, or the change is explained above
- [ ] fail-open semantics preserved (proxy failures never 4xx/deadlock a request)
- [ ] No API keys, credentials, private media, or machine-specific config in this PR
