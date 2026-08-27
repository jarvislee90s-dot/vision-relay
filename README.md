# vision-relay

**English** · [中文](README.zh.md)

A **transparent HTTP proxy at the agent-harness boundary** that gives **text-only models vision**.
It sits in front of your harness base_url, intercepts images in Anthropic / Responses / Chat requests,
transcribes them via a vision-language model (VLM), and **relays the text** to the real upstream text model.
The upstream only ever sees text — so a text-only model can "read" images, without any skill, plugin, or tool.
It runs as a **resident HTTP service**, not a Skill + MCP server.

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

**Desktop app (recommended)** — grab the installer from [Releases](https://github.com/jarvislee90s-dot/vision-relay/releases):

| Platform | Artifact |
|---|---|
| Windows x64 | `vision-relay-<version>-win-x64-setup.exe` |
| macOS (Apple Silicon) | `vision-relay-<version>-macos-arm64.dmg` |
| Linux x64 | `vision-relay-<version>-linux-x64.AppImage` / `.deb` |

Zero Python required — the core ships frozen inside the app. Install, open, done.

**pip (advanced / headless)**:

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

## Desktop console (GUI)

A Tauri 2 desktop console manages everything visually, for Claude Code, Codex, Qwen Code and zcode alike: routing toggle with live per-harness topology, model modality matrix backed by real probes, vision call records (prompt / raw VLM reply / injected text), read-only diagnostics with auto-repair, per-harness VLM settings, and local-only vision logs with retention. It ships inside the desktop installer above — zero Python needed.

### Launching

- **Installed app (recommended)**: launch it like any regular app — **Launchpad / Applications** on macOS, the **Start menu** on Windows, the app list on Linux. First launch walks you through a two-step wizard: ① fill in the VLM (the only required setting), ② review model capabilities — then routing is on.
- **Development mode (from a checkout)**: needs the core on `PATH` (e.g. `pip install -e .` first), then:

```bash
pnpm -C gui install
pnpm -C gui tauri dev
```

> Closing the window ≠ stopping the service: on close it asks whether to "close the UI only (service keeps running in the tray)" or "stop the service too"; your choice can be remembered. While resident, the tray icon reopens the window or runs diagnostics.

### A quick look

| ![Overview](docs/screenshots/overview.png) | ![Models](docs/screenshots/models.png) |
|:---:|:---:|
| **Overview** | **Model capabilities** |
| ![Records](docs/screenshots/records.png) | ![Settings](docs/screenshots/settings.png) |
| **Vision records** | **Settings** |

### Sheet-by-sheet guide

**1) Overview — see at a glance whether the service is alive and who is wired through**

- Shows: service status banner (running / stopped · `127.0.0.1:<port>` · auto-reconciling); one card per harness with takeover status (✓ taken over) and the live wiring chain (base_url → vision-relay → relay → real upstream, bypassed hops clearly marked); below, two lists — "handled automatically" (reclaimed drift, absorbed upstream changes, auto-fixes, auto-annotations) and "⚠ needs you" (e.g. a direct upstream missing an API key).
- Interact: the **routing toggle**; 🔄 **refresh** (manual reconcile: reclaim hijacked wiring, absorb vendor changes); 📋 **diagnostics** (read-only report: service / port / routing-tool status + what was auto-fixed + what still needs you); expand a card's **details** to open the harness config file, **disable / re-enable a relay**, or **fill in a missing API key**; when a zcode rewrite is pending a restart banner appears with a one-click restart.

**2) Model capabilities — "reads images" only counts once probed**

- Shows: the capability matrix keyed by (harness · provider · model): current annotation (image / text-only / unannotated), source (default / manual / cached probe), and the measured column (✓ accepts images / ✗ rejects / untested); inactive providers are folded at the bottom (excluded from probing); a status line shows progress and conclusions.
- Interact: 🔍 **probe all untested** — sends a real request per row for models of the currently active provider without a cached verdict, with in-row spinner and live N/M progress, then a summary popup (image / text-only / inconclusive / unreachable counts plus the first unreachable reason); per-row **retest** (active provider only); click "toggle" to cycle an annotation (unannotated → text-only → image); edited rows highlight until "save changes" applies them together; "fetch model list from upstream" optionally assists annotation (for two-hop routing the list lives in the routing tool's own UI).

**3) Vision records — what exactly got written on every "look"**

- Shows: a harness → session tree on the left; a table on the right (time, tier Tier1/Tier2, cache hit, duration, VLM used). Clicking any row expands the "three-part detail": ① the prompt sent to the VLM, ② the VLM's raw reply, ③ the text actually injected into the conversation — the whole transcription is auditable.
- Interact: click a session or row to inspect. Records stay on this machine only; retention is configurable in Settings.

**4) Event log — every automatic action, on the record**

- Shows: a rolling ledger of automatic actions (time / harness / type / details): auto-reclaim, auto-absorb, auto-fix, auto-annotate, relay added; refreshes every 8 seconds.
- Interact: filter by type; "⬇ export" downloads the full event stream as JSONL.

**5) Settings — the only required setting plus every switch**

- **VLM (the only required setting)**: model name, base URL, API key (masked input; 👁 reveals the saved key temporarily; leave blank to keep the current key).
- **Routing scope**: pick which harnesses get routed; unchecking one immediately restores its original wiring (unchecking a running zcode pops three choices: restart now / keep it checked / restart later yourself).
- **Per-harness groups**: everything follows the global VLM by default; "configure separately" gives one tool its own endpoint / model / key.
- **Appearance**: UI language (system / 中文 / English); core path (auto-detected from `PATH`, settable by hand).
- **Service & advanced**: default handling for unannotated models (treat as text-only and transcribe = safe default / pass through = saves tokens); vision-record logging on/off and retention days (default 7, local only).
- 🧪 **VLM test**: four modes (Tier1 / Tier2 × default / custom prompt), optional custom test image (PNG / JPEG / WebP / GIF, ≤10 MiB), one click sends a real request and shows duration and transcription.
- All input edits take effect via the sticky "💾 save" bar; "discard" reverts.

**6) Advanced features**

The advanced capabilities scattered across the five sheets, collected:

- **Custom vision prompts** (Settings → vision prompts): both the Tier1 "describe fully" and Tier2 "answer the question" prompts are replaceable, with one-click restore to defaults.
- **Default for unannotated models** (Settings → service & advanced): safe side by default (transcribe as text-only); switch to pass-through to save tokens once you know every upstream model is vision-capable.
- **Per-harness VLM** (Settings): give each tool its own transcription endpoint — e.g. a cheap model just for codex.
- **Relay disable / re-enable / fill key** (Overview → details): temporarily disable a misbehaving forwarding leg, re-enable with one click; fill a direct upstream's missing key right in the UI.
- **Fetch model list from upstream** (Model capabilities): ask the upstream for its model IDs to assist annotation; under two-hop routing (CC Switch / Codex++) you're pointed to the tool's own UI.
- **Event stream export** (Event log): full JSONL for archiving and review.
- **Tray residency & remembered close behavior**: close the window while the service stays resident; the tray menu reopens Overview or pops the diagnostics report.
- **CLI parity**: every GUI action is backed by a `vision-relay` management verb (`status` / `diagnose` / `refresh` / `probe` / …, all with `--json`), so scripted use never needs the GUI.

## Configuration

Shared config lives in `~/.vision-relay/config` (fallback for env vars); proxy settings in `~/.vision-relay/proxy.json`. Env overrides: `VISION_RELAY_BIND_PORT`, `VISION_RELAY_VLM_MODEL`, `VISION_RELAY_VLM_BASE_URL`, `VISION_RELAY_VLM_API_KEY`, `VISION_RELAY_VLM_FORMAT` (config dir: `VISION_RELAY_CONFIG_DIR`).

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