# vision-proxy

**English** · [中文](README.zh.md)

A local HTTP protocol proxy that gives **text-only models vision**. It intercepts images in
Anthropic / Responses / Chat requests, transcribes them via a vision-language model (VLM), and
forwards the **text** to the real upstream text model. The upstream only ever sees text, so a
text-only model can "read" images.

This is the standalone, single-capability project extracted from [Qwen-MM-Plugins](https://github.com/QwenLM/Qwen-MM-Plugins)
(its `proxy` capability). It runs as a resident service — not a Skill + MCP server.

## How it works

```
Agent harness (Claude Code / Codex / Qwen Code)
        |
        | base_url -> 127.0.0.1:8787
        v
   [ vision-proxy ]
      /          \
  images        text only
    /                \
   v                  v
 [ VLM ]        [ upstream text model ]
  (description)   (chat/responses/anthropic)
```

## Install

```bash
pip install vision-proxy
```

Or from a checkout:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[proxy]'
```

## Quick start

**What the proxy supports**

- **Inbound node types**: `responses`, `chat`, and Anthropic (`/v1/messages`). Detection is
  fully automatic — there is no config for it. The proxy matches the request **path** first
  (`/v1/messages` → Anthropic, `/v1/responses` → Responses, `/v1/chat/completions` → Chat)
  and falls back to the **body structure** when the path is not recognized (an `input` field →
  Responses, a `messages` list → Anthropic, otherwise Chat). A request that matches neither is
  rejected with a 400.

- **Model types**: both **vision-capable VLM models** (images pass through untouched) and
  **text-only models** (images are transcribed) are supported. The `model_capabilities` map in
  the config decides which is which.

- **How the upstream model is identified**: the model-name → vision/text mapping lives in the
  proxy config. On every `start` the proxy scans the harness config files (Claude Code / Codex /
  Qwen Code), groups the discovered models by harness, and interactively asks you to confirm only
  models it has not seen before (default: text-only, the safe choice). Already-confirmed models
  are reused silently; run `qwen-mm-plugins-proxy models` to review or edit the map.

**You need to prepare**

1. an API key for a vision-capable VLM (mimo / qwen-vl / Doubao / ...), used in `vlm.api_key`;

2. the upstream endpoints for **both** the text model (`relays[].base_url`) and the VLM
   (`vlm.base_url`): either side can be an OpenAI-compatible (chat) or Anthropic-format endpoint,
   and the text side additionally supports the Responses format. Which one is in use is set by
   `relays[].protocol` / `vlm.format`; Volcengine / DeepSeek etc. all work as long as the
   endpoint speaks one of those formats.

**Three steps**

1. Edit `~/.qwen-mm-plugins/proxy.json` (create it if missing) with the template below (put in your own keys):

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

2. Start: `qwen-mm-plugins-proxy start` (the first run interactively asks you to confirm which
   models support images; afterwards start/stop auto-wire and restore without prompting).

3. Verify: paste an image in Claude Code / Codex / Qwen Code and ask "what is this", then
   `qwen-mm-plugins-proxy logs` shows `injected:1` on success.

**Config rewrites happen only on `start` / `stop`**: the proxy backs up and rewrites the three
harness base_urls when it starts, and restores them when it stops. While it is running it never
watches or rewrites any config file. Your changes take effect on the next `start` (or a restart).

**Commands**: `start` / `stop` / `status` / `logs` / `check` / `models` (edit model capability) /
`models-scan` / `test-image`.

## Configuration

Shared configuration lives in `~/.qwen-mm-plugins/config` (fallback for env vars) and the proxy
settings in `~/.qwen-mm-plugins/proxy.json`. Env overrides: `QWEN_MM_PROXY_BIND_PORT`,
`QWEN_MM_PROXY_VLM_MODEL`, `QWEN_MM_PROXY_VLM_BASE_URL`, `QWEN_MM_PROXY_VLM_API_KEY`,
`QWEN_MM_PROXY_VLM_FORMAT`.

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[proxy]'
python -m pip install pytest httpx
python -m pytest -q
ruff check .
```

See [CONTRIBUTING.md](CONTRIBUTING.md) and the design spec under `docs/superpowers/specs/`.

## License

Apache-2.0 — see [LICENSE](LICENSE).