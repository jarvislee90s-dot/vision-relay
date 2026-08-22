# vision-relay

**English** · [中文](README.zh.md)

A **transparent HTTP proxy at the agent-harness boundary** that gives **text-only models vision**.
It sits in front of your harness base_url, intercepts images in Anthropic / Responses / Chat requests,
transcribes them via a vision-language model (VLM), and **relays the text** to the real upstream text model.
The upstream only ever sees text — so a text-only model can "read" images, without any skill, plugin, or tool. 

This is the standalone project extracted from [Qwen-MM-Plugins](https://github.com/QwenLM/Qwen-MM-Plugins)
(its `proxy` capability), running as a **resident HTTP service**, not a Skill + MCP server.

## Why a proxy, not a Skill?

There are two common ways to give a text-only model vision, and they look alike but are architecturally different:

| | Skill / Tool (model-invoked) | Pure transparent proxy | **vision-relay** (this project) |
|---|---|---|---|
| How it works | Ship a skill/tool into the harness; the model must *remember* to call it | Intercept base_url, but only capture / translate protocol | Intercept **the whole request stream** at base_url AND rewrite its content |
| Who decides to use it | The model (it may forget) | Non-discriminating | **You, once, at config time** |
| Image handling | Model passes image to the tool | Only observes, does nothing | **Images are transcribed to text and injected** before forwarding |
| Transparency | Opaque to the model, requires prompts | Transparent, but no value added | **Fully transparent + value added** (model is unaware, images become text) |
| Upstream | Text model sees whatever the tool returns | Text model sees raw (possibly image-bearing) stream | **Text model only ever sees text** |

Existing open-source projects you might find (`visual-proxy`, `codex-vision-proxy`, `vision-bridge-mcp`, `cc-inspector`, `anthroproxy`) mostly fall into the **left** (Skill/tool) or **middle** (pure proxy) columns.
**vision-relay combines both: transparent interception AND image transcription** — the combination is rare in open source.

## How it works

```
   [ Agent harness ]      Claude Code / Codex / Qwen Code
        |  base_url -> 127.0.0.1:8787
        v
   [ vision-relay ]
      /        \
  images       text-only
    /            \
   v              v
  [ VLM ]    [ upstream text model ]     relay: chat / responses / anthropic
  (transcribe)
```

## Install

```bash
pip install vision-relay
```

Or from a checkout:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m pip install pytest httpx
```

**Windows (PowerShell)** when installing from a checkout: `source .venv/bin/activate` is Unix syntax and will error in PowerShell. Use the commands below instead (use `py` or `python`, not `python3`; the activation script lives under `Scripts\`):

```powershell
py -3 -m venv .venv
.venv\Scripts\Activate.ps1   # if "running scripts is disabled", first run:  Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
python -m pip install -e .
python -m pip install pytest httpx
```

## Quick start

### What vision-relay supports

- **Inbound node types**: `responses`, `chat`, and Anthropic (`/v1/messages`). Detection is fully automatic — no config. The proxy matches the **path** first (`/v1/messages` -> Anthropic, `/v1/responses` -> Responses, `/v1/chat/completions` -> Chat) and falls back to the **body structure** (`input` -> Responses, `messages` -> Anthropic, otherwise Chat). Requests matching neither are rejected with a 400.
- **Model types**: both vision-capable VLM models (images pass through untouched) and text-only models (images are transcribed) are supported, decided by the `model_capabilities` map in config.
- **Model identification**: on every `start` the proxy scans the harness config files (Claude Code / Codex / Qwen Code), groups discovered models by harness, and interactively asks you to confirm only unseen models (default: text-only, the safe choice). Reuse silently afterwards; run `vision-relay models` to review/edit.

### What you need to prepare

1. an API key for a vision-capable VLM (mimo / qwen-vl / Doubao / ...) — `vlm.api_key`;
2. upstream endpoints for **both** the text model (`relays[].base_url`) and the VLM (`vlm.base_url`): either side can be OpenAI-compatible (chat) or Anthropic-format, and the text side additionally supports Responses. Set via `relays[].protocol` / `vlm.format`; Volcengine / DeepSeek etc. all work.

### Three steps

1. Edit `~/.vision-relay/proxy.json` (create if missing) with the template below (put in your own keys):

```json
{
  "server": { "bind_port": 8787 },
  "relays": [
    { "name": "my-text", "protocol": "chat",
      "base_url": "https://<your-upstream>", "api_key": "<YOUR_UPSTREAM_KEY>", "models": ["*"] }
  ],
  "vlm": {
    "model": "mimo-v2.5",
    "base_url": "https://<your-vlm-endpoint>", "api_key": "<YOUR_VLM_KEY>", "format": "chat"
  },
  "model_capabilities": { "global": { "minimax-m3": "vision", "doubao-seed-2.1-turbo": "vision" } }
}
```

> The `relays` above is a **single-hop (direct-to-upstream)** example. If you run a local routing tool like **CC Switch / Codex++** and want a two-hop chain (`harness → vision-relay(8787) → tool(15721/57321) → real upstream`), point `relays[].base_url` at the tool's local port and add a `via` field (descriptive only — it does not affect URL joining), as in the two-hop templates below.

**Two-hop · via Codex++ (Codex models, responses protocol)**:

```json
{ "name": "codex", "protocol": "responses",
  "base_url": "http://127.0.0.1:57321/v1", "via": "codex-plus", "models": ["*"] }
```

**Two-hop · via CC Switch (Codex models, chat protocol)**:

```json
{ "name": "cc-codex", "protocol": "chat",
  "base_url": "http://127.0.0.1:15721", "via": "cc-switch", "models": ["*"] }
```

**Two-hop · via CC Switch (Claude models, anthropic protocol)**:

```json
{ "name": "cc-claude", "protocol": "anthropic",
  "base_url": "http://127.0.0.1:15721", "via": "cc-switch", "models": ["*"] }
```

> Note: if `relays` is left empty (`[]`), the proxy has nowhere to forward after transcribing images, and requests fail with `UnsupportedProtocol("Request URL is missing an 'http://' or 'https://' protocol.")` — make sure you add a relay for every harness you actually use.

2. Start: `vision-relay start` (first run interactively asks which models support images; afterwards start/stop auto-wire and restore without prompting).

   **Windows one-click scripts** from the source dir: run `.\start.ps1` to start and `.\stop.ps1` to stop — the script creates the venv, installs deps, then invokes `vision-relay start`/`stop` (run `start.ps1` in an interactive terminal on first use, since it asks you to confirm model vision capability):

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\start.ps1   # one-click start (foreground, Ctrl+C to stop)
   powershell -ExecutionPolicy Bypass -File .\stop.ps1    # one-click stop (restores wiring)
   ```

3. Verify: paste an image in Claude Code / Codex / Qwen Code and ask "what is this", then `vision-relay logs` shows `injected:1` on success.

Config rewrites happen only on `start` / `stop` (backup + rewrite the three harness base_urls, restore on stop). While running it never watches or rewrites any config file; edits take effect on the next `start`.

**Commands**:

| Command | Purpose |
|---|---|
| `start` | Start the service and wire the three harnesses (backup + rewrite base_urls). |
| `start --detach` | Start as a detached background process (for GUI / auto-restart). |
| `stop` | Stop the service and restore the original harness base_urls. |
| `status` | Show service / wiring / intent status. |
| `logs` | Tail the proxy log. |
| `check` | Self-check config and upstreams. |
| `models` | Interactively review / edit model capabilities. |
| `models-scan` | Non-interactively print the model capability draft. |
| `test-image` | Test the VLM transcription path with one image. |
| `refresh` | Manual reconcile: reclaim hijacked wiring, absorb vendor changes, auto-repair zombie wiring (backend of the refresh button). |
| `diagnose` | Read-only diagnostic report: observations + auto-fixes applied + what still needs you. |
| `tools` | Probe routing-tool ports and show the active provider (read-only). |
| `probe` | Modality probe for one model: `--harness` / `--provider` / `--model`, or `--all-untested`. |
| `events` | Tail the event log. |
| `visionlog` | Query the vision call records. |

All management verbs accept `--json` for machine-readable output shaped like `{"contract_version": 1, "ok": ..., "data": ...}` (the GUI contract):

```bash
vision-relay status --json
```

## Configuration

Shared config lives in `~/.vision-relay/config` (fallback for env vars); proxy settings in `~/.vision-relay/proxy.json`. Env overrides: `VISION_RELAY_BIND_PORT`, `VISION_RELAY_VLM_MODEL`, `VISION_RELAY_VLM_BASE_URL`, `VISION_RELAY_VLM_API_KEY`, `VISION_RELAY_VLM_FORMAT` (config dir: `VISION_RELAY_CONFIG_DIR`).

### Upgrading from qwen-mm-plugins-proxy

vision-relay is the standalone successor of the `qwen-mm-plugins-proxy` capability. On first start it reads an existing `~/.qwen-mm-plugins/proxy.json` automatically and migrates it to `~/.vision-relay/` on next save; legacy `QWEN_MM_PROXY_*` env vars and `.qwen-mm-proxy.bak` wiring backups are still recognized (with a deprecation warning). If you still export the legacy `QWEN_MM_CONFIG_DIR`, it is honored as the active state directory for both reads and writes (no split-brain); the automatic migration applies to the default paths.

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m pip install pytest httpx
python -m pytest -q
ruff check .
```

Windows (PowerShell):

```powershell
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e .
python -m pip install pytest httpx
python -m pytest -q
ruff check .
```

See [CONTRIBUTING.md](CONTRIBUTING.md) and the design spec under `docs/superpowers/specs/`.

## License

Apache-2.0 — see [LICENSE](LICENSE).