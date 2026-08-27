# vision-relay

**English** · [中文](README.zh.md)

A **transparent HTTP proxy at the agent-harness boundary** that gives **text-only models vision**.
It sits in front of your harness base_url, intercepts images in Anthropic / Responses / Chat requests,
transcribes them via a vision-language model (VLM), and **relays the text** to the real upstream text model.
The upstream only ever sees text — so a text-only model can "read" images, without any skill, plugin, or tool.
It runs as a **resident HTTP service**, not a Skill + MCP server. **One codebase covers four harnesses —
Claude Code, Codex, Qwen Code and zcode: install once, take over all of them.**

## Why vision-relay?

There are two common ways to give a text-only model vision, and they look alike but are architecturally different.
This project is a third:

| | Skill / Tool (model-invoked) | Pure transparent proxy | **vision-relay** (this project) |
|---|---|---|---|
| How it works | Ship a skill/tool into the harness; the model must *remember* to call it | Intercept base_url, but only capture / translate protocol | Intercept **the whole request stream** at base_url AND rewrite its content |
| Who decides to use it | The model (it may forget) | Non-discriminating | **You, once, at config time** |
| Image handling | Model passes image to the tool | Only observes, does nothing | **Images are transcribed to text and injected** before forwarding |
| Transparency | Opaque to the model, requires prompts | Transparent, but no value added | **Fully transparent + value added** (model is unaware, images become text) |
| Upstream | Text model sees whatever the tool returns | Text model sees raw (possibly image-bearing) stream | **Text model only ever sees text** |
| Cross-harness reuse | One implementation per harness | Generic but adds no value | **One codebase, four harnesses**: protocol normalization and the image safety net are written once; a new tool is just another shell |
| Failure mode | None | Does nothing | **fail-open**: on VLM failure a readable notice is injected — never a 400 / deadlock |
| Cost | Re-transcribes on every call | — | **Tier1 / Tier2 two-level cache + TTL + context budget**: the same image never pays twice, large images can't blow the budget |

Existing open-source projects you might find (`visual-proxy`, `codex-vision-proxy`, `vision-bridge-mcp`, `cc-inspector`, `anthroproxy`) mostly fall into the **left** (Skill/tool) or **middle** (pure proxy) columns.
**vision-relay combines both: transparent interception AND image transcription** — the combination is rare in open source.

## What it looks like

A desktop console that works out of the box — routing toggle with live per-harness topology, a probe-backed
capability matrix, three-part vision records, read-only diagnostics with auto-repair, five sheets, shipped
inside the desktop installer.

| ![Overview](docs/screenshots/overview.png) | ![Models](docs/screenshots/models.png) |
|:---:|:---:|
| **Overview** | **Model capabilities** |
| ![Records](docs/screenshots/records.png) | ![Settings](docs/screenshots/settings.png) |
| **Vision records** | **Settings** |

What each sheet does and shows — the [sheet-by-sheet manual](#desktop-console-gui) is below.

## How it works

```
   [ Agent harness ]      Claude Code / Codex / Qwen Code / zcode
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

Already using a local routing tool like CC Switch or Codex++? Keep your habits — vision-relay takes the
first hop and the tool becomes the downstream relay (two-hop routing; templates in
[Configuration & advanced](#configuration--advanced)).

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

Three things happen automatically — no config needed:

- **Inbound protocol auto-detection** — Anthropic / Responses / Chat, matched by path first, then by body structure;
- **Capability defaults to the safe side** — vision models pass through, text-only models get transcribed, per the capability table; unannotated models are treated as text-only (worst case one extra VLM call, never leaks an image to a text-only model);
- **start wires, stop restores** — harness base_urls are backed up and rewritten automatically; while running it never touches any config file; coexists with CC Switch / Codex++ (first hop, tools downstream).

**Three steps**:

1. Install and open the desktop app (see above);
2. First-launch wizard: fill in one "vision" model key (the only required setting) → review model capabilities → flip the routing switch on Overview;
3. Paste an image into Claude Code / Codex / Qwen Code / zcode and ask "what is this" — a fluent text description means success 🎉

> **Headless / scripted**: `pip install vision-relay` then `vision-relay start` (first run interactively confirms model capabilities, then stays silent; success check: `vision-relay logs` shows `injected:1`). Hand-written `proxy.json`, two-hop templates and all commands live in [Configuration & advanced](#configuration--advanced); on Windows from a checkout use `.\start.ps1` / `.\stop.ps1`.

## Desktop console (GUI)

### Launching

- **Installed app (recommended)**: launch it like any regular app — **Launchpad / Applications** on macOS, the **Start menu** on Windows, the app list on Linux. First launch walks you through a two-step wizard: ① fill in the VLM (the only required setting), ② review model capabilities — then flip the routing switch on the Overview page.
- **Development mode (from a checkout)**: needs the core on `PATH` (e.g. `pip install -e .` first), then:

```bash
pnpm -C gui install
pnpm -C gui tauri dev
```

> Closing the window ≠ stopping the service: on close it asks whether to "close the UI only (service keeps running in the tray)" or "stop the service too"; your choice can be remembered. While resident, the tray icon reopens the window or runs diagnostics.

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

## Configuration & advanced

### Hand-written proxy.json (headless / CLI)

Edit `~/.vision-relay/proxy.json` (create if missing) with the template below (put in your own keys):

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

Both `relays` / `vlm` sides accept OpenAI-compatible (chat) or Anthropic format, and the text side additionally supports Responses; mainstream upstreams such as Volcengine / DeepSeek work as long as the endpoint speaks one of them.

> Note: if `relays` is left empty (`[]`), the proxy has nowhere to forward after transcribing images, and requests fail with `UnsupportedProtocol("Request URL is missing an 'http://' or 'https://' protocol.")` — make sure you add a relay for every harness you actually use.

### Two-hop routing (CC Switch / Codex++)

The `relays` above is a **single-hop (direct-to-upstream)** example. If you run a local routing tool and want a two-hop chain (`harness → vision-relay(8787) → tool(15721/57321) → real upstream`), point `relays[].base_url` at the tool's local port and add a `via` field (descriptive only — it does not affect URL joining).

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

### Commands

| Command | Purpose |
|---|---|
| `start` | Start the service and wire the four harnesses (backup + rewrite base_urls). |
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

### Environment variables

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
