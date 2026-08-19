# Contributing

Thanks for contributing to vision-relay. Keep changes focused on a concrete problem.

## Development setup

vision-relay supports Python 3.10 and newer. From a checkout, install the runtime and test deps:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m pip install pytest httpx
python -m pytest -q
```

## Making changes

- Keep proxy protocol logic in vision_relay/; each module has one clear job (see AGENTS.md for the architecture invariants).
- Preserve existing CLI flags, protocol handling, and config keys unless the change is required to fix functionality. Explain any interface change in the PR.
- Import optional dependencies lazily.
- Do not commit API keys, credentials, private test media, generated artifacts, or machine-specific configuration.
- Add or update tests and documentation when behavior changes.

## Verification

Run the full offline test suite and lint before opening a PR:

```bash
python3 -m pytest -q
ruff format --check .
ruff check .
```

If a test needs a live VLM/upstream or hardware not available to you, state what was not run in the PR.

## Pull requests

Describe the problem and the reason for the chosen fix, link related issues, and include the commands and results used for verification. Keep unrelated changes in separate PRs.

Report security issues according to SECURITY.md, not through a public issue. Contributions are licensed under the repository's Apache-2.0 license.
