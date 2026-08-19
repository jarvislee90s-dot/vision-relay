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

2. Start: `vision-relay start` (first run interactively asks which models support images; afterwards start/stop auto-wire and restore without prompting).

3. Verify: paste an image in Claude Code / Codex / Qwen Code and ask "what is this", then `vision-relay logs` shows `injected:1` on success.

Config rewrites happen only on `start` / `stop` (backup + rewrite the three harness base_urls, restore on stop). While running it never watches or rewrites any config file; edits take effect on the next `start`.

**Commands**: `start` / `stop` / `status` / `logs` / `check` / `models` (edit model capability) / `models-scan` / `test-image`.

## Configuration

Shared config lives in `~/.vision-relay/config` (fallback for env vars); proxy settings in `~/.vision-relay/proxy.json`. Env overrides: `VISION_RELAY_BIND_PORT`, `VISION_RELAY_VLM_MODEL`, `VISION_RELAY_VLM_BASE_URL`, `VISION_RELAY_VLM_API_KEY`, `VISION_RELAY_VLM_FORMAT`.

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m pip install pytest httpx
python -m pytest -q
ruff check .
```

See [CONTRIBUTING.md](CONTRIBUTING.md) and the design spec under `docs/superpowers/specs/`.

## License

Apache-2.0 — see [LICENSE](LICENSE).