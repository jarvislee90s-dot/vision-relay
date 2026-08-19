# vision-relay

[English](README.md) · **中文**

一个架设在 **agent harness 边界的透明 HTTP 代理**,让**纯文本模型**拥有视觉能力。它在你的 harness base_url 前面,拦截 Anthropic / Responses / Chat 三类请求里的图片,交给视觉语言模型(VLM)转述为文字,再**把文本转发(relay)给真正的上游文本模型**。

上游永远只看到文本——所以无需任何 skill / 插件 / 工具,纯文本模型就能"看图"。它是从 [Qwen-MM-Plugins](https://github.com/QwenLM/Qwen-MM-Plugins) 的 `proxy` 能力抽取出来的独立项目,以**驻留式 HTTP 服务**形式运行(不是 Skill + MCP server)。

## 为什么用"代理",而不是 "Skill"?

给纯文本模型加视觉,常见有两条路线,表面相似实则架构完全不同。本项目属于第三条:

| | Skill / 工具型(模型主动调用) | 纯透明代理 | **vision-relay(本项目)** |
|---|---|---|---|
| 做法 | 给 harness 塞一个 skill/tool,靠模型*记得*去调用 | 拦截 base_url,但只抓包 / 转协议 | 在 base_url 拦截**整个请求流**,并**改写请求内容** |
| 谁决定用 | 模型(它可能忘) | 无差别拦截 | **你在配置时定一次** |
| 图片处理 | 模型把图传给工具 | 只观察,不处理 | **图片被转述成文本并注入**,再转发 |
| 透明性 | 对模型不透明,需提示 | 透明,但不增值 | **完全透明 + 有增值**(模型无感,图片变文本) |
| 上游所见 | 文本模型看到工具返回的任意结果 | 文本模型看到原始(可能含图)流 | **文本模型永远只见文本** |

开源里你能搜到的同类(`visual-proxy`、`codex-vision-proxy`、`vision-bridge-mcp`、`cc-inspector`、`anthroproxy`)大多落在最**左**(Skill/工具)或最**中**(纯代理)两列。
**vision-relay 把"透明拦截 + 图片转述"两者结合**——这种组合在开源里很罕见。

## 工作原理

```
   [ Agent harness ]      Claude Code / Codex / Qwen Code
        |  base_url -> 127.0.0.1:8787
        v
   [ vision-relay ]
      /        \
 图片       纯文本
    /            \
   v              v
  [ VLM ]    [ 上游文本模型 ]       relay: chat / responses / anthropic
  (转述)
```

## 安装

```bash
pip install vision-relay
```

或从源码 checkout:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m pip install pytest httpx
```

## 快速开始

### 支持什么

- **入站节点类型**: `responses`、`chat`、以及 Anthropic(`/v1/messages`)。自动识别,无需配置。先按**路径**匹配(`/v1/messages`→Anthropic,`/v1/responses`→Responses,`/v1/chat/completions`→Chat),路径不识别时按**请求体结构**兜底(`input`→Responses,`messages`→Anthropic,否则 Chat)。两者都不匹配则 400 拒绝。
- **模型类型**: 既能透传视觉 VLM(图片原样放行),也能转述纯文本模型(图片→描述)。由配置里的 `model_capabilities` 表决定哪种。
- **模型识别**: 每次 `start` 时扫描 harness 配置文件(Claude Code / Codex / Qwen Code),按 harness 分组发现的模型,只交互确认未见过的模型(默认纯文本,安全选择),已确认的静默复用。用 `vision-relay models` 查看/修改。

### 你需要准备

1. 视觉 VLM 的 API key(mimo / qwen-vl / Doubao 等)——`vlm.api_key`;
2. 文本模型(`relays[].base_url`)与 VLM(`vlm.base_url`)两端的上游端点:任一侧都支持 OpenAI 兼容(chat)或 Anthropic 格式,文本侧还多支持 Responses 格式。用 `relays[].protocol` / `vlm.format` 指定;Volcengine / DeepSeek 等只要端点能说其中一种格式即可。

### 三步开工

1. 编辑 `~/.vision-relay/proxy.json`(不存在则创建),按下方模板填入你自己的 key:

```json
{
  "server": { "bind_port": 8787 },
  "relays": [
    { "name": "my-text", "protocol": "chat",
      "base_url": "https://<你的上游>", "api_key": "<你的上游KEY>", "models": ["*"] }
  ],
  "vlm": {
    "model": "mimo-v2.5",
    "base_url": "https://<你的VLM端点>", "api_key": "<你的VLM_KEY>", "format": "chat"
  },
  "model_capabilities": { "global": { "minimax-m3": "vision", "doubao-seed-2.1-turbo": "vision" } }
}
```

2. 启动:`vision-relay start`(首次会交互确认哪些模型支持图片;之后 start/stop 自动接线并恢复,不再提示)。

3. 验证:在 Claude Code / Codex / Qwen Code 里粘贴一张图并问"这是什么",然后 `vision-relay logs` 显示 `injected:1` 即成功。

配置改写只在 `start` / `stop` 时发生(备份并改写三个 harness base_url,stop 时恢复)。运行时它从不监视或改写任何配置文件;改动下次 `start` 生效。

**命令**: `start` / `stop` / `status` / `logs` / `check` / `models`(编辑模型能力) / `models-scan` / `test-image`。

## 配置

共享配置在 `~/.vision-relay/config`(作为环境变量的回退);代理设置在 `~/.vision-relay/proxy.json`。环境变量覆盖:`VISION_RELAY_BIND_PORT`、`VISION_RELAY_VLM_MODEL`、`VISION_RELAY_VLM_BASE_URL`、`VISION_RELAY_VLM_API_KEY`、`VISION_RELAY_VLM_FORMAT`(配置目录:`VISION_RELAY_CONFIG_DIR`)。

### 从 qwen-mm-plugins-proxy 升级

vision-relay 是 `qwen-mm-plugins-proxy` 能力的独立继任者。首次启动会自动读取已有的 `~/.qwen-mm-plugins/proxy.json`,并在下次保存时迁移到 `~/.vision-relay/`;旧 `QWEN_MM_PROXY_*` 环境变量与 `.qwen-mm-proxy.bak` 接线备份仍被识别(带 deprecation 提示)。

## 开发

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m pip install pytest httpx
python -m pytest -q
ruff check .
```

参见 [CONTRIBUTING.md](CONTRIBUTING.md) 与 `docs/superpowers/specs/` 下的设计规格。

## 许可证

Apache-2.0 — 见 [LICENSE](LICENSE)。