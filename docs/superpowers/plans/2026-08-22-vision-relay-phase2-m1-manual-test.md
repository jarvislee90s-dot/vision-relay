# vision-relay 二期 M1 手动测试手册（2026-08-22）

> 前置：本机已装 CC Switch 或 Codex++（任一即可）；有一个可用的 OpenAI 兼容 VLM key。
> 全程在项目根目录，用 `.venv\Scripts\python -m vision_relay …`（或装好后的 `vision-relay …`）。
> 每条都写「预期」，不符即 FAIL 并记录到 issues。

### A1. 刷新优先做（核心诉求）
1. `vision-relay start` → 服务起来，三 harness 接线，`proxy.json` 出现自动 relay（工具在线时）。
   预期：输出含 `[wire] claude: base_url -> http://127.0.0.1:8787 (ok)`；`vision-relay tools` 显示在线工具与激活供应商。
2. 保持服务运行，手动把 `~/.codex/config.toml` 的 `base_url` 改成 `http://127.0.0.1:57321/v1`（模拟工具抢线）。
3. `vision-relay refresh`。
   预期：输出 `[reconcile] {'type': 'reclaim', 'harness': 'codex', ...}`；重新查看 config.toml 已回到 `:8787`；`vision-relay events` 最后一条是 `reclaim`。

### A2. 换供应商吸收
1. 服务运行中，把 `~/.claude/settings.json` 的 `env.ANTHROPIC_BASE_URL` 改成一个陌生地址（如 `https://newvendor.example/api`）。
2. `vision-relay refresh`。
   预期：`absorb` 事件；base_url 被接管回 `:8787`；`proxy.json` 出现 `direct-claude` relay 指向新地址；`~/.vision-relay/snapshots.json` 的 claude 快照 base_url 已更新；`diagnose` 提示需要补 key。

### A3. 僵尸接线自动修复（两种意图）
1. 路由开着：`vision-relay start`，然后直接 `taskkill /F /PID <pid>`（或 `kill -9`）。
   预期：pid 文件残留。运行 `vision-relay diagnose`：`routing_on=true` → 自动 `restart`，数秒后 `vision-relay status --json` 显示 `service_alive: true`，接线保持 `:8787`（事件里 `auto_fix restart`）。
2. 路由关着（模拟关路由时崩溃）：`vision-relay stop` 后手动把 claude 的 base_url 改回 `:8787` 再 `vision-relay diagnose`。
   预期：`routing_on=false` → `auto_fix restore`，base_url 恢复为快照里的原值。

### A4. 模态探针（真供应商）
1. `vision-relay probe --harness claude --provider bigmodel --model <你的文本模型>`。
   预期：文本模型返回 `text_only`（大概率报错判定）；再对视觉模型（如 qwen-vl-max，经 dashscope 直连 relay）探测，预期 `image`。
2. `vision-relay models-scan --json`。
   预期：三元组行含 `value/source/probe_cached`；未标注模型 `value: null`。

### A5. 识图留痕三段
1. 配好 VLM key，`vision-relay start`。
2. 用 curl 向代理发一条带 data URL 小图的 chat 请求（示例图任意 <100KB png base64）：
   `curl http://127.0.0.1:8787/v1/chat/completions -d '{"model":"<文本模型>","messages":[{"role":"user","content":[{"type":"text","text":"这是什么"},{"type":"image_url","image_url":{"url":"data:image/png;base64,<...>"}}]}]}'`
3. `vision-relay visionlog`。
   预期：一条记录，含 harness、tier、prompt（Tier2 带你的问题）、raw、injected（`[图片描述]` 开头）。

### A6. --json 契约抽查
1. `vision-relay status --json`、`refresh --json`、`diagnose --json`、`tools --json`、`events --json`。
   预期：每条输出都是合法 JSON 且含 `"contract_version": 1`；`config get --json`（若实现）不含明文 key（`●●●●`）。

### A7. 留存与开关
1. `proxy.json` 设 `"vision_log": {"enabled": false}` → 重启 → 发图请求 → `vision-relay visionlog` 无新记录。
2. 恢复 enabled=true，把 `retention_days` 设 0 → 重启（触发 cleanup）→ 昨日及更早的 visionlog 文件被清理。
