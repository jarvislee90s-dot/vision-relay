# vision-relay - 独立身份重塑 / 文档移植 / 仓库脚手架 设计规格

**日期**: 2026-08-19  **状态**: Approved（四项关键决策已确认，待用户审阅本文后执行）
**上游来源**: Qwen-MM-Plugins-plus 的 proxy capability 文档与 PR #40 正文

## 1. 背景与目标

本仓库已从 Qwen-MM-Plugins 抽取为独立项目（见 `2026-08-19-vision-relay-standalone-design.md`），
但当前仅完成 README / pyproject 层面的品牌切换。遗留问题：

- 包目录、配置路径、环境变量、备份后缀、CLI 文案、插件 manifest 仍是 `qwen_mm_plugins*` 命名；
- 设计文档（Phase-1 spec / Phase-2 路线图 / 实现计划 / 验收 / 手工测试 / 生态调研）全部留在上游
  Qwen-MM-Plugins-plus 仓库，本仓库只有 1 份抽取设计 spec（内容还写着旧名 vision-proxy）；
- 仓库管理脚手架不完整：无 PR 模板、无 feature_request 表单、无 CHANGELOG、CI 只测 3.12 单版本、
  Windows 只有 checkout 冒烟、CONTRIBUTING 引用不存在的 `.[proxy]` extras。

本 spec 定义：**(a)** 独立身份的完整命名改造（含向后兼容迁移），**(b)** 设计文档的移植与适配，
**(c)** P1 仓库管理脚手架。目标是让本仓库在命名、文档、工程治理三个层面完全脱离上游、独立成体。

## 2. 已确认决策（2026-08-19 用户确认）

| 决策点 | 结论 |
|---|---|
| 包目录改名 | `qwen_mm_plugins_proxy/` → `vision_relay/`；配置目录迁至 `~/.vision-relay/`，**读取时自动回退旧目录并迁移**（读旧写新） |
| 文档结构 | 方案 A：`docs/superpowers/{specs,plans}` + 根目录 `research/` + `docs/history/` 存 PR 正文原文 |
| 上游遗留组件 | **全部删除**：`src/capabilities/proxy/` 下 4 个插件 manifest、`install-proxy.sh`、`install.sh`、`tests/test_proxy_install.py`（301 行）、CI 的 bash -n 步骤。独立库唯一入口为 `pip install` + `vision-relay start` |
| 仓库脚手架 | P1 全套：PR 模板 + feature_request 表单 + config.yml + CHANGELOG + CI 矩阵/打包冒烟/Windows 全量 + AGENTS.md + CONTRIBUTING 修正 |

## 3. 目标文档结构（方案 A）

```
vision-relay/
├── README.md / README.zh.md
├── CHANGELOG.md                          # 新增，Keep a Changelog 格式
├── CONTRIBUTING.md / SECURITY.md / LICENSE / AGENTS.md   # AGENTS.md 新增
├── docs/
│   ├── superpowers/
│   │   ├── specs/
│   │   │   ├── 2026-08-13-vision-relay-design.md          # 移植 Phase-1 spec（适配）
│   │   │   ├── 2026-08-13-vision-relay-design-phase2.md   # 移植 Phase-2 路线图（适配）
│   │   │   ├── 2026-08-19-vision-relay-standalone-design.md   # 现有抽取 spec（改名+更新）
│   │   │   └── 2026-08-19-vision-relay-rebrand-docs-port-design.md  # 本文档
│   │   └── plans/
│   │       ├── 2026-08-16-vision-relay-phase1-plan.md         # 移植（UTF-16→UTF-8）
│   │       ├── 2026-08-16-vision-relay-phase1-acceptance.md   # 移植
│   │       └── 2026-08-16-vision-relay-phase1-manual-test.md  # 移植（去个人路径）
│   └── history/
│       └── pr40-vision-proxy-body.md     # PR #40 正文原文（只修链接，不改写内容）
├── research/
│   ├── README.md                         # 索引（修失效链接，重指向本仓库 spec）
│   ├── harness-vision-survey.md          # 原 RESEARCH.md（2026-08-12 调研）
│   └── dsh-vision-plugins-survey.md      # DSH 生态调研
├── vision_relay/                         # 改名后的包（含并入的 env_util.py）
├── tests/                                # 12 个文件（install 测试删除）
└── .github/                              # 见 §6
```

文件名保留原始创作日期（2026-08-13 / 08-16）作为历史锚点，topic 部分采用新命名。
删除：`src/` 整个目录、`install-proxy.sh`、`install.sh`、`proxy_env.py`（并入包）、
`tests/test_proxy_install.py`。

## 4. 文档移植清单与处理方式

| 源文件（Qwen-MM-Plugins-plus） | 目标 | 处理 |
|---|---|---|
| `docs/superpowers/specs/2026-08-13-qwen-mm-proxy-design.md` | `specs/2026-08-13-vision-relay-design.md` | 改名 `qwen-mm-plugins-proxy`→`vision-relay`、`~/.qwen-mm-plugins/`→`~/.vision-relay/`、`QWEN_MM_PROXY_*`→`VISION_RELAY_*`；剥离 §8.2 install.sh 接线、§10.4 插件发布机制（plugin-versions.json/manifest/tag_plugin_release.py）章节，替换为独立库的 pip 安装 + CLI 接线描述；`shared.env.get_env` 引用改为 `vision_relay.env_util`；头部加 provenance 注记 |
| `specs/2026-08-13-qwen-mm-proxy-design-phase2.md` | `specs/2026-08-13-vision-relay-design-phase2.md` | 同上轻度适配（install.sh/api-capability 引用替换）；补录 §8 发现的幽灵控制面、威胁模型两项新议题 |
| `plans/2026-08-16-proxy-phase1-plan.md` | `plans/2026-08-16-vision-relay-phase1-plan.md` | **UTF-16 LE → UTF-8 转换**；头部加 provenance + 适配说明：Task 1/2/13/14 描述上游打包方式，已被 standalone/rebrand 两份 spec 取代，正文保留作历史实现记录不逐行改写 |
| `plans/2026-08-16-proxy-phase1-acceptance.md` | `plans/2026-08-16-vision-relay-phase1-acceptance.md` | CLI 入口名 / 测试路径改名 |
| `plans/2026-08-16-proxy-phase1-manual-test.md` | `plans/2026-08-16-vision-relay-phase1-manual-test.md` | 去个人路径（`C:\Users\bunny\...` → 占位符）；CLI 名改名 |
| `research/RESEARCH.md` | `research/harness-vision-survey.md` | 原样移植，修链接 |
| `research/dsh-vision-plugins-survey.md` | 同名 | 原样移植，修 `../RESEARCH.md` 失效链接 |
| `research/README.md` | 同名 | 索引重指向本仓库新路径 |
| `E:\LLMproject\Github\PR-视觉代理-vision-proxy-正文融合版.md` | `docs/history/pr40-vision-proxy-body.md` | **原文存档**，只修相对链接不改写内容（PR 提交记录的历史证据） |
| （本仓库现有）`specs/2026-08-19-vision-proxy-standalone-design.md` | `specs/2026-08-19-vision-relay-standalone-design.md` | 文件改名；内容更新：名称决策已定（vision-relay，弃用别名）、console script 单入口、§8 Open Questions 全部标记已决策 |

## 5. 代码命名改造清单

### 5.1 项目身份层（全部执行）

| # | 现状 | 改为 | 波及 |
|---|---|---|---|
| R1 | 包目录 `qwen_mm_plugins_proxy/` | `vision_relay/` | 12 个测试文件 import 与 monkeypatch 字符串路径（如 `qwen_mm_plugins_proxy.vlm.httpx.Client`）、pyproject、ruff.toml、CONTRIBUTING |
| R2 | 顶层 `proxy_env.py` | `vision_relay/env_util.py`（并入包） | 4 处 import（cli/config/logging_util）、pyproject 去掉 py-modules、ruff known-first-party；测试无直接引用 |
| R3 | console script `vision-relay` + 别名 `qwen-mm-plugins-proxy` | 仅 `vision-relay` | pyproject（从未发布，别名无人依赖） |
| R4 | 配置目录 `~/.qwen-mm-plugins/` | `~/.vision-relay/` + 读旧写新迁移 | env_util、config、logging_util、cli、onboarding、README×2、测试 |
| R5 | 环境变量 `QWEN_MM_PROXY_*`（5 个） | `VISION_RELAY_*`，旧名回退读取并打 deprecation 提示 | config.py、测试、README×2 |
| R6 | `QWEN_MM_CONFIG_DIR` / `QWEN_MM_CONFIG` | `VISION_RELAY_CONFIG_DIR` / `VISION_RELAY_CONFIG`，旧名回退 | env_util.py |
| R7 | 备份后缀 `.qwen-mm-proxy.bak` | `.vision-relay.bak`；restore 兼容识别旧后缀 | wiring.py、测试 |
| R8 | CLI 文案 `qwen-mm-plugins-proxy start` 等 5 处 | `vision-relay start` | cli.py×4、onboarding.py×1 |
| R9 | `__init__.py` docstring 引用上游 spec 路径 | vision-relay 描述 + 本仓库 spec 路径 | 1 文件 |
| R10 | `src/capabilities/proxy/` 4 个 manifest | 整目录删除 | 4 文件 |
| R11 | CONTRIBUTING `pip install -e '.[proxy]'`（extras 不存在的残留 bug） | `pip install -e .` | CONTRIBUTING.md |
| R12 | README×2 旧配置路径 / 环境变量名 | 同步新名 | README.md、README.zh.md |
| R13 | 顺手补充：argparse 无 `--version` | 加 `--version`（读 `__version__`） | cli.py |

### 5.2 功能接口层（白名单，明确不动）

- `HARNESSES = ("claude", "codex", "qwen-code")` 与 `HARNESS_CFG` 的 `~/.qwen/settings.json` → `model.baseUrl` 接线键（Qwen Code harness 适配）
- `_HARNESS_BY_PROTO = {"anthropic": "claude", "responses": "codex", "chat": "qwen-code"}`
- VLM 默认模型 `qwen-vl-max` + DashScope 默认 base_url（真实服务默认值，属产品决策非身份命名）
- `capability.py` 内置 `"qwen-vl-*": "vision"` 模式；测试数据中的 qwen-* 模型名
- README 中 "Qwen Code" 作为受支持 harness 的表述、"extracted from Qwen-MM-Plugins" 出处声明（诚实署名）

### 5.3 兼容性设计（读旧写新）

- **配置迁移**：加载时若 `~/.vision-relay/proxy.json` 不存在且 `~/.qwen-mm-plugins/proxy.json` 存在
  → 读旧路径并提示；首次 `save_config` 落盘到新路径。
- **环境变量**：`_apply_env` / `env_util` 先查 `VISION_RELAY_*`，未命中再查旧 `QWEN_MM_*` 并打一行提示。
- **备份还原**：`wiring_restore` 同时识别 `.vision-relay.bak` 与 `.qwen-mm-proxy.bak`（旧版本接的线也能还原）。
- 用户现有机器配置可无痛切换；全新安装零感知。

## 6. 仓库管理脚手架（P1 全套）

1. `.github/PULL_REQUEST_TEMPLATE.md` — What changed / Why / Verification / Compatibility 三段式
   （含"是否改了协议处理/CLI/配置键"勾选框；参考 Qwen-MM-Plugins 模板形态）
2. `.github/ISSUE_TEMPLATE/feature_request.yml` — 现有 bug_report.yml 保留
3. `.github/ISSUE_TEMPLATE/config.yml` — 禁空白 issue + 安全报告/讨论区引流链接
4. `CHANGELOG.md` — Keep a Changelog 格式，首条 Unreleased 记录本次改名与文档移植
5. CI（ci.yml 重写）：
   - Python **3.10–3.13 矩阵** × ubuntu/macos（当前只测 3.12）
   - `pip install -e .` 打包安装冒烟（当场抓住 extras/打包类 bug）+ `vision-relay --version` 冒烟
   - **windows-latest 跑完整离线套件**（当前只有 checkout 冒烟；代码含 OpenProcess/GBK 等 Windows 专项逻辑反而没在 Windows 测过）
   - 移除 bash -n（shell 脚本已删）；保留既有安全基线（SHA 固定 actions、persist-credentials: false、最小 permissions）
6. `AGENTS.md` — 工程宪法：命名对齐（`vision_relay` 包 ↔ `vision-relay` 命令 ↔ `~/.vision-relay` 配置 ↔ `VISION_RELAY_*` 环境变量）、协议解析只进 ir.py、fail-open 语义不可破坏、行为变更必须同步 spec、测试先行
7. CONTRIBUTING 修正（R11 + 模块路径）+ 「AI 辅助贡献条款」（须能解释每一行、小 PR、issue 先行）
8. `.gitignore` 追加 `项目启动提示词.txt`（个人提示词不入库）

## 7. 验证方案

1. `python -m pytest -q` → 预期 **≥144 passed**（基线 159 − 删除 test_proxy_install.py 的 15 例 = 144；§5.3 迁移/回退逻辑新增的用例计入）；
2. `ruff check .` + `ruff format --check .` 零告警；
3. `pip install -e .` 成功且 `vision-relay --version` 正常输出；
4. `grep -ri qwen` 终检：命中项**必须全部属于 §5.2 白名单**（harness 适配、模型名、出处署名）；
5. 文档链接检查：新 spec 之间及 research 索引的相对链接可达；
6. 兼容性手测（用户机器）：旧 `~/.qwen-mm-plugins/proxy.json` 能被读取并迁移到 `~/.vision-relay/`。

## 8. 明确不做（本轮范围外）

- PyPI 发布与 release.yml（P2，trusted publishing / OIDC）
- labeler / stale bot / dependabot / CODEOWNERS / 分支保护（P3，社区长大后）
- 幽灵控制面（ui_port=8788 无监听）的实现或删除——仅在 phase2 spec 补录议题，不在本轮动代码
- install-proxy.sh 的修复保留（已决策删除，其 qwen-code 过时路径 `~/.qwen-code/.env` 随之消亡）
- 威胁模型 / 本地 token 等安全强化——phase2 spec 补录议题

## 9. 执行顺序（每步一个 commit）

1. 文档移植（§4 全表 + 目录创建）
2. 代码改名 R1–R13（含删除遗留组件）
3. 脚手架 P1（§6 全部）
4. 验证（§7）+ 修复

## 10. 风险与缓解

| 风险 | 缓解 |
|---|---|
| monkeypatch 字符串路径漏改（`"qwen_mm_plugins_proxy.vlm.httpx.Client"` 类） | 全局 grep `qwen_mm` 复核 + 测试套件兜底 |
| 配置迁移逻辑破坏现有测试对 config_dir 的假设 | 迁移仅在"新路径不存在且旧路径存在"时触发，默认路径行为不变 |
| 删除 install 测试后 CI 的 bash 依赖残留 | ci.yml 同步重写，移除 bash -n |
| 2479 行计划文档改写工作量失控 | 按决策只做编码转换 + 头部适配注记，正文保留历史原貌 |
