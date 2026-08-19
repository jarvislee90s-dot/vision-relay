# PR：vision-proxy（视觉代理）—— 给纯文本模型看图

> **存档说明（2026-08-19）**：本文是提交给 QwenLM/Qwen-MM-Plugins 的 PR #40 正文原文（视觉代理 vision-proxy），按原貌存档、不作改写。文中 `docs/superpowers/...` 路径指上游仓库，对应内容已移植至本仓库 `docs/superpowers/specs|plans/`；文中 `qwen_mm_plugins_proxy` 命名在独立化后改为 `vision_relay`。

> 分支 vision-proxy（11 个 commit：3 个设计提交 + 6 个功能组 + 2 个文档），已 rebase 到最新 main（b40783c），落后 0 / 领先 11。
> 跟踪文档：docs/superpowers/specs/2026-08-13-qwen-mm-proxy-design.md（一阶段设计规范）、docs/superpowers/plans/2026-08-16-proxy-phase1-manual-test.md（手工测试）。
> 关联 issue：[Qwen-MM-Plugins#39 新能力提案](https://github.com/QwenLM/Qwen-MM-Plugins/issues/39)。
> PR：[#40](https://github.com/QwenLM/Qwen-MM-Plugins/pull/40)。

---

## What changed

### 1. 背景：视觉能力的维度与 L0–L6 分级

让模型「会看图」有非常多的做法，差异可以归纳为几个维度：作用层（方案插在链路哪一层）、是否自动（要不要人/模型主动触发）、是否依赖模型主动调用工具、跨 harness 通用性。

按作用层把社区方案分为 L0–L6（本地生态调研结论）：

| 级别 | 作用层 | 实现范式 | 是否自动 | 依赖模型调用工具 | 代表项目 |
|---|---|---|---|---|---|
| L0 | 外部进程/本地模型 | 本地 VLM / 浏览器 CDP / macOS Vision | 视宿主 | 部分 | [free-vision-skill](https://github.com/niyongsheng/free-vision-skill)、[dsh-tool-vision](https://github.com/gloryxpnv/dsh-tool-vision) |
| L1 | Web GUI 客户端 | 发送接管 | 自动 | 否 | [dsh-plugin-image-input](https://github.com/Elohia/dsh-plugin-image-input)、[dsh-subagent-vision](https://github.com/niuniuaba/dsh-subagent-vision) |
| L2 | 附件准入层 | 准入声明 + sidecar | 自动 | 否 | [dsh-plugin-multimodal](https://github.com/shinjiyu/dsh-plugin-multimodal)、[dsh-vision-bridge](https://github.com/ximengxiaolan/dsh-vision-bridge) |
| L3 | LLM 适配器层（进程内代理） | 适配器包装 / provider 路由 | 自动 | 否 | [dsh-vision-proxy](https://github.com/Flyvhidbwo/dsh-vision-proxy)、[dsh-visual-plugin](https://github.com/jyh20030112/dsh-visual-plugin)、[dsh-deepseek-vision](https://github.com/siegfly/dsh-deepseek-vision) 等 10+ |
| L4 | 工具层 | 模型主动调用 vision 工具 | 模型触发 | 是 | [modlens](https://github.com/liustack/modlens)、[dsh-vision-router](https://github.com/ysr666/dsh-vision-router)、[dsh-vision-toolkit](https://github.com/Anionex/dsh-vision-toolkit) |
| L5 | 子代理层 | 委派视觉子代理 | 模型触发/自动 | 混合 | [dsh-vision-subagent](https://github.com/ruby1304/dsh-vision-subagent)、[dsh-subagent-vision](https://github.com/niuniuaba/dsh-subagent-vision) |
| L6 | 外部协议桥 | MCP 桥接 | 模型触发 | 是 | [Gemini-Eyes](https://github.com/ConsoleSun/Gemini-Eyes)、[deepseek-vision](https://github.com/GOU-GEE/deepseek-vision)（MCP） |

**总体优缺点**：
- L1/L2/L3（自动通道）：图片在进模型前就被处理，不依赖模型「想起来」调工具，最稳健；缺点是绑定实现位置（L3 需 harness 进程内 seam，L1/L2 需客户端/准入配合），跨 harness 复用成本高。
- L4/L5/L6（工具/委派/桥接派）：实现简单、复用现有工具栈；但依赖模型主动调用——纯文本模型忘了调，图就白发，稳健性差（社区已退居二线）。
- 共同痛点：多数方案绑定单一 harness（如 DSH）或单一接入点，换 Claude Code / Codex / Qwen Code 要各写一套。

### 2. 本代理的独特优点

本 PR 是「L3 思路的外部化实现」：不在 harness 进程内打补丁，而起一个**常驻 HTTP 代理**作为 harness base_url 的第一跳，自动拦截图片 → VLM 转文字 → 替换后转发给上游文本模型。

| 对比项 | 传统 Skill | MCP 工具 | 主动调用工具插件 | 本代理（vision-proxy） | 本代理的具体优点 |
|---|---|---|---|---|---|
| 是否需模型主动调用 | 是 | 是 | 是 | **否（请求一到即自动）** | 纯文本模型「忘了调工具」也不会漏图：图片只要进请求就被处理，稳健性不依赖模型自觉 |
| 是否需 harness 进程内补丁 | 否 | 否 | 否 | **否（纯外部 HTTP 代理）** | 不动 harness 内核与安装目录，升级 harness 不失效；安装/卸载只改 base_url（带备份还原） |
| 跨 harness 通用 | 各写一套 | 各配一套 | 各配一套 | **一套代码覆盖 Claude Code / Codex / Qwen Code（三协议归一化）** | 图片安全网只写一次；以后接新工具只加一层 parse/serialize 外壳，不重写图片处理 |
| 对主模型透明 | 否 | 否 | 否 | **是（模型只看到文字，表现像原生看图）** | 主模型零改动、零学习成本，对话体验无缝，用户不用记任何新工具名 |
| 失败兜底 | 无 | 报错 | 报错 | **fail-open：VLM 失败注入说明，绝不 400/死锁** | 最坏情况是可读的说明提示而非崩溃/死锁，用户不用查日志、不用改配置 |
| 成本 | 高 | 高 | 高 | **两层缓存 + 上下文预算 + 黄金窗口，降本** | 同图多次请求不重复调 VLM；大图/长文不会撑爆上下文预算（CONTEXT_FULL） |
| 兼容中继工具 | 无 | 无 | 无 | **与 CC Switch / Codex++ 共存（本代理第一跳、工具为下游）** | 用户已有的中继工具、模型切换习惯原样保留，无需迁移 |

**代价（缺点，为什么不是人人都该用）**：
- **要常驻一个进程**：端口占用、需要守护；与其它改写 base_url 的工具并存时需 check 检测冲突（本代理必须是第一跳）。
- **协议归一化是跨 harness 的必须成本**：三种入站 × 三种上游的转换无法外包，只能自己实现。
- **只处理「图片字节已进请求」的图**：协议原生图片块与字符串内嵌 data URL 都会被拦截转写——包括 harness 的文件工具把路径读进来后产生的图片块（你在 Codex / Qwen Code 里贴文件地址能识图，就是走了这条路）。代理不读文件系统：若请求里只有路径字符串、没有图片字节（如工具只返回存盘路径），Phase 1 不处理，路径图兜底交给 MCP 工具派 / Skill。
- **接线只在 start/stop 发生**：运行期间手动改 harness 配置或 proxy.json，代理不会自动跟随，需下次 start（或重启）才生效。
- **图→文字是有损的**：密集 UI 截图的小字可能丢失（所有「图转文字」方案的共同天花板）。

**与 L3 进程内适配器派（DSH 生态的主流）的区别**：同样是「自动、无痕、不依赖模型调工具」的 L3，本代理与 DSH 主流是**同一目标的两种挂载位置**。DSH 的 L3 在 harness 进程内、LLM adapter seam 上注册 provider 路由，在 stream() 里改图片块：免协议转换、与 relay 天然共存，但绑定 DSH 一个 harness，还必须骗过 inputModalities 准入闸门（声明图片输入），否则图片根本进不了会话。本代理把同一件事搬到 harness 之外的常驻 HTTP 代理：改写 base_url 做第一跳，一套代码覆盖 Claude Code / Codex / Qwen Code（这三个都没有 DSH 的准入闸门，改了 base_url 就能 100% 拦到），代价是自己实现 Anthropic / Responses / Chat 三协议归一化、进程守护与「第一跳」冲突检测。

一句话：把「给纯文本模型看图」从「让模型想起来调工具」变成「链路自动完成」，且不绑定任何 harness 内部 API。

---

### 3. 设计动因（为什么这样设计）

每个设计都是被需求逼出来的。下面按需求→设计的链路说明。

### 3.1 协议归一化：为什么
- **需求**：要同时兼容 Claude Code（Anthropic）、Codex（Responses）、Qwen Code（Chat）三种协议，且以后接新工具时不想重写图片处理。
- **若不归一化**：每种协议写一套「抽图→转写→回填」→ 重复代码、难维护、易漏。
- **设计**：统一中间表示 IR（IRRequest/Message/ContentBlock，见 B2a），图片安全网只实现一次，协议只是 parse/serialize 的外壳。

### 3.2 图片处理管线：为什么
- **需求**：主模型是纯文本，图必须变成它能「懂」的文字；而且出错/剥离时，用户不能收到 400/死锁，最好连日志都不用看。
- **设计**：扫描→抽取→VLM 转写→注入/fail-open。**fail-open 用注入文本「看不到图：视觉模型调用失败…」让模型替我们把情况讲给用户**，用户在界面上就能看懂，无需改代码或查日志。提示词注入考虑了多种场景：Tier1 全面描述 / Tier2 按问题聚焦、多图 [[图片K]] 前缀、追问检测、上下文预算（字符串 data URL 不计入）等（见 B2b）。

### 3.3 能力判定：为什么
- **需求**：有的模型本身能看图（minimax、doubao），有的不能（deepseek、glm）。全部转写浪费钱还丢细节；全部透传纯文本模型会崩。
- **设计**：model_capabilities 按 (harness, model) 判定；未知模型默认 text_only（最安全：拦下转写，最多多花一次 VLM，绝不把图漏给纯文本模型）（见 B2b）。

### 3.4 relay 路由与中继工具共存：为什么
- **需求**：用户常开 CC Switch / Codex++ 管模型；代理必须插进链路而不破坏工具，也不让用户改工具配置。
- **设计**：本代理做第一跳（harness base_url→8787），工具作代理的下游 relay；relay.via 标签表达「两层经工具」拓扑，check 提示，URL 拼接对齐 Codex++ 的 build_versioned_url（见 B4）。

### 3.5 生命周期自动接线：为什么
- **需求**：零背景用户不想手工改三处 harness 配置，也不想每次贴命令。
- **设计**：start 自动备份并改写三处 base_url→8787、stop 自动还原；首次启用**显式**交互确认各模型看图能力（弹到脸上），后续只对**新模型**增量询问（见 B5）。

### 3.6 缓存与预算：为什么
- **需求**：VLM 调用贵且慢；大图/长文会把上下文预算塞满导致 CONTEXT_FULL。
- **设计**：两层缓存（Tier1 图 / Tier2 图+问题）+ TTL；上下文预算中字符串 data URL 不计入；黄金窗口配额限制历史图转写（见 B2b）。

---

## Implementation（实现，按功能组 · 含文件/关键函数/行号）

说明：本 PR 的 8 个功能组对应整理后的 commit。**功能组之前**还有三个设计提交（ac3978f 添加一阶段设计、e241d32 对齐 Codex++ 评审意见、3ab1613 调研笔记）——它们是本 PR 的设计基础，未在功能组中重复。实现细节见跟踪文档：设计规范与手工测试。

> 阅读约定：下面每个关键函数都按「是什么 → 干什么」介绍，变量/函数名均为实际代码中的名字（文件:行号）。

### B1 · docs：设计规范
- 内容：一阶段设计规范（跟踪文档，`docs/superpowers/specs/2026-08-13-qwen-mm-proxy-design.md`），是本 PR 的设计基础与验收依据。

### B2a · IR 与三协议（协议层）
- 文件：`qwen_mm_plugins_proxy/ir.py`、`stream.py`。
- 核心概念——**IR（统一中间表示，Intermediate Representation）**：把三种入站协议归一成同一种内部结构，图片安全网只对着这一种结构写一次，协议只是外面的 parse/serialize 外壳。三个核心类型：**IRRequest**（一次完整请求：协议、模型名、消息列表、采样参数等）、**Message**（一条消息）、**ContentBlock**（消息里的内容块：文本块 / 图片块 / tool_result 等）。
- 关键函数（是什么 → 干什么）：
  - `detect_protocol` (ir.py:51)：**入站协议识别器**。先按请求路径判定（`/v1/messages`→Anthropic、`/v1/responses`→Responses、`/v1/chat/completions`→Chat），路径认不出再按请求体结构兜底（有 `input` 字段→Responses、有 `messages` 列表→Anthropic、否则 Chat）。它决定了后面走哪套 parse/serialize。
  - `parse_anthropic / parse_responses / parse_chat` (ir.py:166/178/229)：**三种协议的请求解析器**，把各自协议的请求 JSON 转成上面的 IR——这样图片安全网不用关心请求是从哪个 harness 来的。
  - `serialize_*` (ir.py:471/476/481)：**三种协议的响应序列化器**，把统一 IR（即上游回包）转回对应协议格式还给 harness。
  - `_attach_proto_fields` (ir.py:456)：**协议字段透传器**。序列化时把 `max_tokens` / `temperature` 等协议专属字段原样透传，避免上游回包丢字段。
  - `_text_with_images` (ir.py:87)：**内嵌图片剥离器**。把文本块里以字符串形式内嵌的 data URL 图片提前剥离成 [图片] 占位，base64 只走图片块——防止图混在文本里漏给纯文本模型。
  - `_message` (ir.py:157)：**消息解析的健壮性兜底**——assistant 消息 content 为 null 但带 tool_calls 时不再崩溃（见 B3）。
- 链路：多工具兼容 → 统一 IR → 图片安全网只写一次 → 三协议只是外壳。

### B2b · VLM / 缓存 / 能力判定（视觉层）
- 文件：`vlm.py`、`cache.py`、`capability.py`。
- 关键函数（是什么 → 干什么）：
  - `VLMClient.describe` (vlm.py:79)：**视觉模型调用器**，把图片转成文字描述。两种提示词模式：Tier1 全面描述、Tier2 按用户问题聚焦（更省 token、更准）。
  - `_is_retryable` (vlm.py:34)：**失败重试判定器**。判断某次 VLM 调用失败是否值得重试：TRANSPORT / TIMEOUT / RATE_LIMIT / HTTP 错误重试（指数退避+抖动）；AUTH / PARSE 不重试（重试也没用）。
  - `DescriptionCache`（cache.py）：**转写结果缓存**。Tier1 按图缓存、Tier2 按「图+问题」缓存、带 TTL——同一张图多次请求不重复调 VLM，省钱省时。
  - `CapabilityTable.judge(model, cfg, harness)` (capability.py:27)：**模型能力判定器**。查某个模型能不能看图：先按 (harness, model) 分组查 model_capabilities → 查不到再查内置名单 → 最后落到 unknown_default（默认 text_only 最安全：拦下转写，绝不把图漏给纯文本模型）。
- 链路：VLM 贵/慢 → 缓存+重试；哪些模型要转写 → (harness, model) 判定表。

### B2c · server / 生命周期 CLI / install.sh 接线（服务层）
- 文件：`server.py`、`cli.py`、`logging_util.py`。
- 关键函数（是什么 → 干什么）：
  - `ProxyHandler.do_POST` (server.py:123)：**HTTP 请求入口**。每个请求都走它：读入请求体 → 判协议 → 图片管线（抽图/转写/注入）→ 选 relay → 序列化 → 转发上游 → 记日志。
  - `_upstream_url` (server.py:47)：**上游 URL 拼接器**。把 relay 的 `base_url` 按协议规则拼成最终调用地址（对齐 Codex++ 的 build_versioned_url：纯 origin 补 /v1、v<数字> 段直拼、/v1/v1 去重）。
  - `_select_relay` (server.py:34)：**转发目标选择器**。按 (model, protocol) 选 relay：先精确/通配匹配 models，再按协议匹配，最后落默认。
  - `_HARNESS_BY_PROTO`（server.py 模块常量）：**入站协议 → harness 名映射**（anthropic→claude、responses→codex、chat→qwen-code），用于能力判定分组与日志归属。
  - 生命周期命令：`cmd_start` (cli.py:47) 启动+自动接线；`cmd_stop` (cli.py:82) 停止+还原接线；`cmd_check` (cli.py:234) 巡检（拓扑/接线/冲突告警）；`cmd_models` (cli.py:217) 查看/编辑模型能力映射。Windows 下 `_pid_running` / `_terminate` (cli.py:143/121) 用 OpenProcess/TerminateProcess 兜底进程管理。
- 链路：三协议请求 → 数据面 → 图片安全网 → 转发上游；CLI 起停/接线/巡检。

### B3 · 手工测试暴露的健壮性修复
- 每个修复 = 什么问题 → 怎么修：
  - assistant content:null + tool_calls 解析崩溃（`_message` ir.py:157）：Claude 有些消息只有工具调用、没有正文，旧代码按正文解析会崩 → 判空兜底。
  - content-length 按 UTF-8 字节计算（server.py）：中文回复按字符数算长度会截断 → 按字节数算。
  - env 空串清 VLM key（`_apply_env` config.py:143）：环境变量显式把 key 置空应生效 → 空字符串也参与覆盖。
  - Windows stop/status 的 os.kill WinError 87（cli.py:121/143）：Windows 上 os.kill 不可靠 → OpenProcess/TerminateProcess 兜底。
  - test-image 输出 reason + stdout errors=replace（cli.py:188/281）：GBK 终端打印 UTF-8 内容会崩 → 编码错误替换。
- 测试：tests/test_proxy_*.py 全绿。

### B4 · 单/双层路由 + 真实 harness 用例
- 内容：
  - `relay.via`：relay 上的**纯描述性标签**（∈ cc-switch | codex-plus），标明这条 relay 是直连（一层）还是经中继工具（两层）。不参与 URL 拼接，只用于 check 展示拓扑，并在 via 与端口不一致时告警（如 via=codex-plus 但端口不是 57321）。
  - `cmd_check` (cli.py:234) 拓扑提示；Codex++(57321) / CC Switch(15721) base_url 拼接测试；5.13 三终端真实贴图用例（见手工测试）。
- 链路：CC Switch/Codex++ 共存 → 本代理第一跳、工具作下游 → via/check 让拓扑可见。

### B5 · start/stop 自动接线 + model_capabilities 分组/增量确认 + models 入口
- 文件：`wiring.py`、`onboarding.py`、`capability.py`、`config.py`。
- 关键函数（是什么 → 干什么）：
  - `wiring_backup_and_rewrite` (wiring.py:106)：**接线器**。start 时把三个 harness 的 base_url 备份成 *.qwen-mm-proxy.bak 并改写为指向本代理。
  - `wiring_restore` (wiring.py:131)：**还原器**。stop 时仅当当前 base_url 仍指向本代理才还原（避免覆盖用户后来手改的配置）。
  - `relays_activate` / `relays_restore` (wiring.py:157/177)：**relay_templates 的合入/移除**——start 激活模板、stop 移除。
  - `scan_model_groups` (onboarding.py:54)：**模型扫描器**。扫描三处 harness 配置，按 harness 分组列出发现的模型（含原变量名/来源 URL；base_url 不作身份 key，防换端点误判）。
  - `confirm_models` (onboarding.py:147)：**交互确认器**。逐个问用户模型是 vision 还是 text_only。
  - `run_onboarding` (onboarding.py:226)：**编排器**。首次全量确认，之后只对新增模型增量询问（已确认的静默复用）。
  - `edit_all` (onboarding.py:260)：**models 命令的编辑入口**，改 model_capabilities。
- 链路：零配置接线 → 备份/还原 → 首次显式确认（弹脸上）→ 增量扫描新模型 → 运行时 (harness, model) 判定。

### B6 · chore：文档跟踪收敛 + README 双语
- 只跟踪一阶段设计规范与手工测试，其余本地文档保留在 .gitignore（不上传）；README.md（英文）与 README.zh.md（中文）新增「给纯文本模型看图」零基础快速开始（密钥打码、说明节点/模型/上游识别与扫描、运行期配置改写边界）。

---

## Verification（测试与质量）
- **proxy 单元/集成套件**（tests/test_proxy_*.py，11 个文件、不含需 bash 的 test_proxy_install，PYTHONUTF8=1）：**144 passed**。
- **scripts/check_manifests.py**：**OK**（10 capabilities 在 marketplace.json / plugin manifests / pyproject.toml 中一致）。
- **ruff**（uvx ruff 最新版）：本次改动 44 个文件 **0 命中**；仓库全量存在既有未格式化/告警（edu-agent / video-edit / blender / scripts / test_tools，均非本 PR 改动），不在本 PR 范围处理。
- 真实三终端贴图验证：日志 injected:1, upstream_status:200（详见手工测试）。
- **未运行项（环境限制，按 CONTRIBUTING 声明）**：
  - test_install_sh / test_proxy_install（55 项）：需要 bash（Git Bash），本机未安装 → 未运行；
  - test_api_omni / test_api_tools / test_api_clients（37 项）：需要真实 API 凭据 → 未运行；
  - test_tools（10 项）：需要 ffprobe（本机 npm 版 ffmpeg 不含）→ 未运行。
  - 以上失败均为环境原因，与本 PR 改动无关（proxy 相关套件全绿）。

---

## Compatibility

- [x] 现有 MCP 工具名、输入、输出未改变（vision-proxy 是非 MCP 的独立能力，不带 `.mcp.json`）。
- [x] 行为变化处已更新测试与文档。
- [x] 未包含凭据、私有媒体、生成产物或机器特定配置。
