# vision-relay 独立身份重塑 / 文档移植 / 仓库脚手架 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 vision-relay 从"README 改了名的抽取仓库"变成命名、文档、工程治理三层完全独立的仓库：移植全部设计文档、包目录改名为 `vision_relay`（含读旧写新兼容层）、删除上游遗留组件、补齐 P1 仓库管理脚手架。

**Architecture:** 纯机械改名 + 兼容层（env_util 读旧写新回退）+ 文档移植（适配改写与原文存档分离）。不改动任何协议/管线/服务器业务逻辑；测试基线 159 → 删除 16 例（install 15 + manifest 1）→ 新增 6 例兼容性用例，终点 149 全绿。

**Tech Stack:** Python >=3.10（stdlib + httpx）、pytest、ruff、bash（仅用于 git 操作与文件转换）、GitHub Actions（uv + 3.10–3.13 矩阵）。

**设计依据:** `docs/superpowers/specs/2026-08-19-vision-relay-rebrand-docs-port-design.md`（已获用户批准，四项决策见其 §2）。

**源仓库路径约定:** 本计划中 `SRC` = `E:\LLMproject\Github\Qwen-MM-Plugins-plus`，`PRBODY` = `E:\LLMproject\Github\PR-视觉代理-vision-proxy-正文融合版.md`。所有命令在 `E:\LLMproject\Github\vision-relay` 下执行（Git Bash）。

---

### Task 1: 文档移植与目录结构

**Files:**
- Create: `docs/superpowers/specs/2026-08-13-vision-relay-design.md`（源自 SRC Phase-1 spec）
- Create: `docs/superpowers/specs/2026-08-13-vision-relay-design-phase2.md`（源自 SRC Phase-2 spec）
- Create: `docs/superpowers/plans/2026-08-16-vision-relay-phase1-plan.md`（源自 SRC 实现计划，UTF-16→UTF-8）
- Create: `docs/superpowers/plans/2026-08-16-vision-relay-phase1-acceptance.md`
- Create: `docs/superpowers/plans/2026-08-16-vision-relay-phase1-manual-test.md`
- Create: `research/README.md`、`research/harness-vision-survey.md`（原 RESEARCH.md）、`research/dsh-vision-plugins-survey.md`
- Create: `docs/history/pr40-vision-proxy-body.md`（PR 正文原文存档）
- Rename: `docs/superpowers/specs/2026-08-19-vision-proxy-standalone-design.md` → `2026-08-19-vision-relay-standalone-design.md`

- [ ] **Step 1: 建目录并复制不需要转码的 7 个文件**

```bash
mkdir -p docs/superpowers/plans docs/history research
cp "$SRC/docs/superpowers/specs/2026-08-13-qwen-mm-proxy-design.md" docs/superpowers/specs/2026-08-13-vision-relay-design.md
cp "$SRC/docs/superpowers/specs/2026-08-13-qwen-mm-proxy-design-phase2.md" docs/superpowers/specs/2026-08-13-vision-relay-design-phase2.md
cp "$SRC/docs/superpowers/plans/2026-08-16-proxy-phase1-acceptance.md" docs/superpowers/plans/2026-08-16-vision-relay-phase1-acceptance.md
cp "$SRC/docs/superpowers/plans/2026-08-16-proxy-phase1-manual-test.md" docs/superpowers/plans/2026-08-16-vision-relay-phase1-manual-test.md
cp "$SRC/research/RESEARCH.md" research/harness-vision-survey.md
cp "$SRC/research/dsh-vision-plugins-survey.md" research/dsh-vision-plugins-survey.md
cp "$PRBODY" docs/history/pr40-vision-proxy-body.md
```

- [ ] **Step 2: UTF-16 实现计划转 UTF-8（去 BOM）**

```bash
iconv -f UTF-16LE -t UTF-8 "$SRC/docs/superpowers/plans/2026-08-16-proxy-phase1-plan.md" \
  | sed '1s/^\xEF\xBB\xBF//' > docs/superpowers/plans/2026-08-16-vision-relay-phase1-plan.md
head -3 docs/superpowers/plans/2026-08-16-vision-relay-phase1-plan.md   # 应为 "# Qwen-MM-Plugins Proxy Capability (Phase 1) 实施计划" 且无乱码
```

- [ ] **Step 3: 改名现有 standalone spec**

```bash
git mv docs/superpowers/specs/2026-08-19-vision-proxy-standalone-design.md \
  docs/superpowers/specs/2026-08-19-vision-relay-standalone-design.md
```

- [ ] **Step 4: 适配 Phase-1 spec（`2026-08-13-vision-relay-design.md`）**

先全局替换（对整个文件，顺序执行）：

| 找（全词） | 换 |
|---|---|
| `qwen-mm-plugins-proxy` | `vision-relay` |
| `~/.qwen-mm-plugins/proxy.toml` | `~/.vision-relay/proxy.json` |
| `~/.qwen-mm-plugins/logs/proxy.log` | `~/.vision-relay/logs/proxy.log` |
| `QWEN_MM_PROXY_VLM_MODEL` | `VISION_RELAY_VLM_MODEL` |
| `QWEN_MM_PROXY_VLM_BASE_URL` | `VISION_RELAY_VLM_BASE_URL` |
| `QWEN_MM_PROXY_VLM_API_KEY` | `VISION_RELAY_VLM_API_KEY` |
| `QWEN_MM_PROXY_VLM_FORMAT` | `VISION_RELAY_VLM_FORMAT` |
| `QWEN_MM_PROXY_BIND_PORT` | `VISION_RELAY_BIND_PORT` |
| `shared.env.get_env` | `vision_relay.env_util.get_env` |

再做以下定点编辑：

1. 标题行改为 `# vision-relay 代理安全网设计 — 第一阶段（Phase 1）`。
2. 头部引言块（日期/修订/状态/目标仓库/范围五行）整体替换为：

```markdown
> 日期：2026-08-13
> 修订：2026-08-16 —— 吸收 DSH 视觉插件生态调研启示；同日拆分：**本文件 = 第一阶段（Claude Code + Codex + Qwen Code）**；第二阶段另行设计
> 状态：已实现（Phase 1 完成并验收，见 plans/2026-08-16-vision-relay-phase1-acceptance.md）
> 目标仓库：jarvislee90s-dot/vision-relay（独立仓库；本设计稿原撰写于 Qwen-MM-Plugins fork，2026-08-19 移植适配）
> 范围：为纯文本模型提供一层协议代理安全网，解决"纯文本模型接收图片导致报错"和"工具/用户图片漏进上下文"的问题。
```

3. §1 末段目标句 `本设计的目标：**在 Qwen-MM-Plugins 内新增 \`proxy\` capability，以协议代理为主、hooks 与 MCP 工具为兜底……**` 改为 `本设计的目标：**提供独立协议代理服务 vision-relay，以协议代理为主，在纯文本模型场景下统一拦截所有图片内容，并兼容多协议互转。**`（§1 其余背景叙述保留原貌，属诚实历史）。
4. §2 第一版包含列表里 `生命周期管理：\`install.sh\` 接入、\`proxy start/stop/status/logs/test-image/check\`、三 harness base_url 改写与回滚` 改为 `生命周期管理：\`vision-relay start/stop/status/logs/test-image/check\`、三 harness base_url 改写与回滚`。
5. §6.3 开头 `配置存放：\`~/.vision-relay/proxy.json\`（600 权限），由 \`install.sh\` Configure 读写。`（全局替换后）改为 `配置存放：\`~/.vision-relay/proxy.json\`（0600 权限），由 \`vision-relay start\` 交互引导与 \`models\` 命令读写。`
6. §6.3 的 TOML 配置示例整块（`[server]` 到 `auto_local_ollama = true`）替换为 JSON 等价物（实现即此格式）：

```json
{
  "server": { "bind_host": "127.0.0.1", "bind_port": 8787, "ui_port": 8788 },
  "relays": [
    { "name": "deepseek-official", "protocol": "responses",
      "base_url": "https://api.deepseek.com", "api_key": "…",
      "models": ["deepseek/*"], "capability": "text_only" },
    { "name": "claude-official", "protocol": "anthropic",
      "base_url": "https://api.anthropic.com", "api_key": "…", "models": ["anthropic/*"] }
  ],
  "vlm": {
    "model": "qwen-vl-max",
    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "api_key": "…",
    "format": "chat",
    "cache_disk": false,
    "auto_local_ollama": true
  }
}
```

7. §6.3 「上游同步 修订 · VLM 配置 env 覆盖惯例」段中 `一律经 \`vision_relay.env_util.get_env\` 读取（与上游一致，不直读 \`os.environ\`；env 字段登记进 \`CONFIG_FIELDS\` 与 \`install.sh:CONFIG_SPEC\`）` 改为 `一律经 \`vision_relay.env_util.get_env\` 读取（env > 配置文件 > 默认）；旧 \`QWEN_MM_PROXY_*\` 环境变量名仍可读取（打印一次性 deprecation 提示）`。
8. §7 整章替换为（原章为上游 api capability 复用，独立库不携带）：

```markdown
## 7. 兜底通道（上游生态）

> 独立化说明（2026-08-19）：原稿此章描述「MCP 工具派兜底（复用上游 Qwen-MM-Plugins 的 api capability）」。独立仓库不携带 api capability，该机制保留在上游生态；本仓库的兜底即 fail-open（§9）与 `test-image` 手动诊断。路径形式图片（§5.3 边界）的兜底同样交由上游/用户侧 MCP 工具处理。
```

9. §8.1 第一段（`新增 \`src/capabilities/proxy/\`，Python，MCP 无关……遵循仓库 capability 规范（manifest、版本 tag、发布流程）`）替换为 `独立 Python 包 \`vision_relay\`（PyPI: \`vision-relay\`），MCP 无关（常驻 HTTP server），命令行入口 \`vision-relay\`，配置与日志在 \`~/.vision-relay/\`。`（数据面/控制面两段保留）。
10. §8.1 「上游同步 修订 · 与 \`support_cua\` 分支……」整段（4 个要点）删除——上游分支专属内容。
11. §8.2 整节替换为：

```markdown
### 8.2 安装与接线（独立库形态）

`pip install vision-relay` 后，`vision-relay start` 完成一切接线：

- 读取 `~/.vision-relay/proxy.json`（0600；首次运行交互引导生成）；`bind_host`/`bind_port` 可配（默认 `127.0.0.1:8787`），`check` 检测端口占用。
- `start` 自动改写三处 harness base_url 指向本代理（改写前备份，`stop` 自动还原；`check` 检测冲突并给链路顺序建议）：
  - Claude Code：`~/.claude/settings.json` 的 `env.ANTHROPIC_BASE_URL`。
  - Codex：`~/.codex/config.toml` 的 `base_url`。
  - Qwen Code：`~/.qwen/settings.json` 的 `model.baseUrl`。
- 旧版 `~/.qwen-mm-plugins/proxy.json` 会被自动读取并迁移（读旧写新，见 rebrand spec §5.3）。
```

12. §10.3 替换为 `- 回归基线：\`python3 -m pytest -q\`、\`ruff format --check .\`、\`ruff check .\`。（上游的 check_manifests.py 与 bash -n 随独立化移除）`。
13. §10.4 整节替换为：

```markdown
### 10.4 发布

- 版本：`vision_relay.__version__`（pyproject dynamic version），SemVer。
- 发布：打 `v*` tag → CI 构建 sdist/wheel 并创建 GitHub Release；PyPI 用 trusted publishing（OIDC，零 token）。
- `CHANGELOG.md`（Keep a Changelog 格式）随每次发布更新。
```

14. §11 对比表行 `| 生命周期 | 内嵌于 Codex++ | 独立 capability + install.sh + 备份回滚 |` 改为 `| 生命周期 | 内嵌于 Codex++ | 独立 PyPI 包 + CLI + 备份回滚 |`。
15. §12 决策记录三行更新：
    - `落地方式：在 Qwen-MM-Plugins fork 上新增 \`proxy\` capability，成熟后回馈上游。` → `落地方式：独立仓库 vision-relay（原为 Qwen-MM-Plugins fork 上的 proxy capability，2026-08-19 抽取独立）。`
    - `VLM 配置惯例：\`[vlm]\` 支持 \`QWEN_MM_PROXY_VLM_*\` env 覆盖（显式配置 > env > 默认），统一经 \`shared.env.get_env\`。` → `VLM 配置惯例：\`[vlm]\` 支持 \`VISION_RELAY_VLM_*\` env 覆盖（显式配置 > env > 默认），统一经 \`vision_relay.env_util.get_env\`；旧 \`QWEN_MM_PROXY_*\` 名回退兼容。`
    - `发布机制：proxy capability 纳入 \`scripts/tag_plugin_release.py\` 强校验的 release index（CAP_ITEMS/CAP_VERSIONS/plugin-versions.json/\`__version__\` 一致）。` → `发布机制：独立 PyPI 包 + tag 驱动发布（上游 plugin-versions.json 机制不适用，已随独立化移除）。`
16. §13 风险第一条后追加一行已解决注记：`官方上游是否接受"常驻 HTTP 代理"这一新能力形态……` 条目末尾加 `（已解决：2026-08-19 独立成库，不再受上游评审约束。）`

- [ ] **Step 5: 适配 Phase-2 spec（`2026-08-13-vision-relay-design-phase2.md`）**

1. 标题行改为 `# vision-relay 代理安全网设计 — 第二阶段（Phase 2）`。
2. 头部引言块整体替换为：

```markdown
> 日期：2026-08-16
> 前置：第一阶段见 [`2026-08-13-vision-relay-design.md`](2026-08-13-vision-relay-design.md)（本文件是它的第二阶段）
> 状态：设计稿（待排期）
> 范围：第一阶段完成后的全部延后项：**OpenCode 一等接入、DSH 兼容（provider 路由形态 + 准入声明）、Claude Code hooks、Web UI 控制面板、磁盘缓存与 Phase 2 后台深缓存、跨协议流式（Responses ↔ 其他）**，及可选方向（relay 轮转、TRAE/Kimi hooks、服务端部署、未知模型启发式学习）。
> 移植注记（2026-08-19）：本稿撰写于上游 fork，路径与命名已按独立仓库适配；§11 为独立化后新增议题。
```

3. 全局替换：`qwen-mm-plugins-proxy` → `vision-relay`。
4. §2.2 的 `\`install.sh\` 改写 \`opencode.json\`` 改为 `自动接线改写 \`opencode.json\`（\`start\`/\`stop\` 机制扩展第四个 harness）`。
5. §3.1 与 §3.4 的 `research/dsh-vision-plugins-survey.md` 文字引用改为可点击相对链接 `[research/dsh-vision-plugins-survey.md](../../../research/dsh-vision-plugins-survey.md)`。
6. 文末追加新章：

```markdown
## 11. 独立化后新增议题（2026-08-19 移植时补录）

- **幽灵控制面**：主 spec §8.1 设计了控制面（ui_port=8788，JSON 管理 API），但当前实现 `/status` 服务在数据面端口上，8788 无任何监听（config/CLI 输出/`check` 端口检查仍引用 ui_port）。Phase 2 落地 Web UI 前需先决策：实现真正的独立控制面，或从配置与输出中移除 ui_port（spec 与实现不许互相撒谎）。
- **威胁模型与监听安全**：当前唯一防线是绑定 127.0.0.1——本地任意进程都可借本代理转发请求、消耗上游 key。需要：本地鉴权 token（可选开关）、VLM 转述不可信图片内容的间接 prompt-injection 边界文档化（注入文本带 `[图片描述]` 标记但内容不消毒）、描述缓存的留存/清理策略。
- **上游遗留组件已删除**：`src/capabilities/proxy/` 插件 manifest、`install.sh`/`install-proxy.sh` 及其测试随独立化移除（决策见 `2026-08-19-vision-relay-rebrand-docs-port-design.md` §2）；独立库唯一入口是 `pip install vision-relay` + `vision-relay start`。
```

- [ ] **Step 6: 适配实现计划（`2026-08-16-vision-relay-phase1-plan.md`，只动头部）**

1. 标题行 `# Qwen-MM-Plugins Proxy Capability (Phase 1) 实施计划` 改为 `# vision-relay Phase 1 实施计划（原 Qwen-MM-Plugins proxy capability）`。
2. 在 `**Tech Stack:**` 行之后、`## Global Constraints` 之前插入：

```markdown
> **移植注记（2026-08-19）**：本文是 Phase 1 的历史实现计划，撰写于上游 Qwen-MM-Plugins fork（UTF-16 原稿转 UTF-8 移植）。正文按原貌保留：其中 Task 1/2/13/14 描述的上游打包方式（`src/capabilities/` 布局、`install.sh` CAP_ITEMS、`plugin-versions.json`、manifest 校验）已被独立化取代，见 `2026-08-19-vision-relay-standalone-design.md` 与 `2026-08-19-vision-relay-rebrand-docs-port-design.md`；Task 3–12 的算法内容与当前 `vision_relay/` 实现一致。
```

- [ ] **Step 7: 适配验收清单（`2026-08-16-vision-relay-phase1-acceptance.md`）**

1. 标题行改为 `# vision-relay — Phase 1 验收清单（spec §10.5）`，并在标题下加一行：`> 移植注记（2026-08-19）：验证数据为上游 fork 时期的历史记录；独立化后以本仓库 CI 为准。`
2. 全局替换：`qwen-mm-plugins-proxy` → `vision-relay`；`~/.qwen-mm-plugins/proxy.json` → `~/.vision-relay/proxy.json`。
3. 第 3 节验证命令注释 `# Qwen Code 配置 DASHSCOPE_BASE_URL 指向代理（Task 13 写入 ~/.qwen-code/.env）` 改为 `# Qwen Code base_url 指向代理（现行接线写入 ~/.qwen/settings.json 的 model.baseUrl）`。
4. 第 8 节 `\`bash install.sh uninstall\` 恢复三 harness 原 base_url（\`*.qwen-mm-proxy.bak\` 还原）` 改为 `\`vision-relay stop\` 恢复三 harness 原 base_url（\`*.vision-relay.bak\` 还原）`。

- [ ] **Step 8: 适配手工测试（`2026-08-16-vision-relay-phase1-manual-test.md`）**

1. 标题行改为 `# vision-relay - Phase 1 手工测试（精简版）`。
2. 全局替换：`qwen-mm-plugins-proxy` → `vision-relay`；`~/.qwen-mm-plugins/proxy.json` → `~/.vision-relay/proxy.json`；`QWEN_MM_PROXY_VLM_API_KEY` → `VISION_RELAY_VLM_API_KEY`。
3. `C:\Users\bunny\Downloads\test.png` 两处 → `<测试图路径>`。
4. 第 3 节表格上方无改动；第 4 节第 ③ 条 `裸 Qwen Code：relay \`{protocol:"chat", base_url:"<直连端点>"}\`` 保留；第 6 节常见问题表保留。

- [ ] **Step 9: 新建 research/README.md（整文件替换内容）**

`research/README.md` 全文（原文件只做参考，直接写新内容）：

```markdown
# research / 调研文档

本目录存放 vision-relay 的调研资料，聚焦"给纯文本 Agent harness 补视觉能力"的生态与实现形态（调研始于 Qwen-MM-Plugins 时代，随项目独立化迁入）。

## 文档索引

| 文档 | 日期 | 主题 |
|---|---|---|
| [dsh-vision-plugins-survey.md](dsh-vision-plugins-survey.md) | 2026-08-16 | **DSH 视觉插件生态调研**：30+ 个 DeepSeek Harness 视觉插件的作用层分类（7 层 / 6 范式）、与 Skill/MCP 工具派、代理派（proxy 设计 / Codex++）的对比，及对 proxy 设计的启示 |
| [harness-vision-survey.md](harness-vision-survey.md) | 2026-08-12 | 前置调研：Claude Code / Codex 生态的视觉方案（Qwen-MM-Plugins、Codex++ PR #1550、claude-vision-skill、claude-video-vision） |

## 调研脉络

1. **2026-08-12 · harness-vision-survey.md**：harness 通用调研，得出"透明代理改写 / Agent 工具 / 指令脚本"三派。
2. **2026-08-13 · proxy 设计稿**：基于 Codex++ PR #1550 的代理派思路，设计 proxy capability（协议归一化 + 图片安全网 + fail-open），今为独立项目 vision-relay（`../docs/superpowers/specs/2026-08-13-vision-relay-design.md`）。
3. **2026-08-16 · 本目录**：DSH 发布后视觉插件爆发，调研其作用层与实现范式，验证/修正 proxy 设计的前提。

## 核心结论（详见 dsh-vision-plugins-survey.md）

- DSH 视觉插件作用在 **7 个层**：外部本地计算（L0）、Web GUI 发送接管（L1）、附件准入（L2）、LLM 适配器包装（L3）、模型工具（L4）、子代理（L5）、MCP 桥（L6）。
- **L3 进程内适配器包装是 DSH 生态主流**（约 1/3 插件），它本质是"代理派"在 harness 内的原生实现：拦截图片→VLM→替换→透传，与 proxy 设计目标一致，但省掉了协议归一化层。
- **与 Skill/MCP 工具派**：DSH 社区验证了"依赖模型调工具不够稳健"，纯工具派普遍升级为结构化证据或与自动通道混合。
- **与外部代理派**：DSH 多了一道 `inputModalities` 附件准入闸门（Claude Code/Codex 没有），未来若支持 DSH 客户端需在 proxy 能力判定层补充"准入声明"。
```

- [ ] **Step 10: 修 research/dsh-vision-plugins-survey.md 的 3 处内部链接**

| 找 | 换 |
|---|---|
| `[RESEARCH.md](../RESEARCH.md)`（2 处：第 6 行、第 318 行） | `[harness-vision-survey.md](harness-vision-survey.md)` |
| `[proxy 设计稿](../docs/superpowers/specs/2026-08-13-qwen-mm-proxy-design.md)`（第 6 行） | `[proxy 设计稿](../docs/superpowers/specs/2026-08-13-vision-relay-design.md)` |
| `[Qwen-MM-Plugins proxy 设计稿](../docs/superpowers/specs/2026-08-13-qwen-mm-proxy-design.md)`（第 319 行） | `[vision-relay 设计稿](../docs/superpowers/specs/2026-08-13-vision-relay-design.md)` |

（`harness-vision-survey.md` 为纯外部链接的原文复制，无需编辑。）

- [ ] **Step 11: PR 正文存档头部（`docs/history/pr40-vision-proxy-body.md`）**

在文件标题行之后插入：

```markdown
> **存档说明（2026-08-19）**：本文是提交给 QwenLM/Qwen-MM-Plugins 的 PR #40 正文原文（视觉代理 vision-proxy），按原貌存档、不作改写。文中 `docs/superpowers/...` 路径指上游仓库，对应内容已移植至本仓库 `docs/superpowers/specs|plans/`；文中 `qwen_mm_plugins_proxy` 命名在独立化后改为 `vision_relay`。
```

- [ ] **Step 12: 更新 standalone spec（`2026-08-19-vision-relay-standalone-design.md`）**

1. 标题行改为 `# vision-relay - 独立项目设计规格 (Standalone Design Spec)`。
2. §2 定位表下的 `名称: vision-proxy（PyPI 包名）/ 可执行名 qwen-mm-plugins-proxy（保留，避免破坏上游文档）` 改为 `名称: vision-relay（PyPI 包名 / 仓库名 / 可执行名；旧别名 qwen-mm-plugins-proxy 已移除——从未发布，无人依赖）`。
3. §5 发布与生命周期里 `console script: qwen-mm-plugins-proxy = qwen_mm_plugins_proxy.__main__:main` 改为 `console script: vision-relay = vision_relay.__main__:main`；`包发现 include = qwen_mm_plugins_proxy*` 改为 `include = vision_relay*`；`版本: 由 qwen_mm_plugins_proxy.__version__…驱动` 改为 `版本: 由 vision_relay.__version__…驱动`。
4. §7 Roadmap 首项 `- [ ] 独立 git 仓库 + 首个 commit（当前任务）` 改为 `- [x] 独立 git 仓库 + 首个 commit（已完成）`。
5. §8 整节替换为：

```markdown
## 8. 待决策 / Open Questions（2026-08-19 全部已决策）

- 包名最终用 vision-proxy 还是 qwen-mm-plugins-proxy？→ **vision-relay**（品牌切换定案；执行见 `2026-08-19-vision-relay-rebrand-docs-port-design.md`）。
- console script 名是否保留 qwen-mm-plugins-proxy 以兼容上游文档？→ **不保留**（从未发布，无人依赖）。
- 是否要同时为中文本地化维护 README.zh.md？→ **是**（双语言 README 已就位）。
```

6. 文件头部引言 `**上游来源**: Qwen-MM-Plugins 的 proxy capability（分支 vision-proxy）` 保留（诚实署名）。

- [ ] **Step 13: 链接自检并提交**

```bash
# 检查移植文档间无失效相对链接（人工浏览要点：research README → 两个调研文件 → spec）
grep -rn "qwen-mm-proxy-design\|](../RESEARCH" docs/ research/   # 预期：0 命中
git add docs/ research/
git commit -m "docs: port design specs, plans, and research from Qwen-MM-Plugins-plus; archive PR body"
```

---

### Task 2: 删除上游遗留组件

**Files:**
- Delete: `src/`（4 个 manifest）、`install.sh`、`install-proxy.sh`、`tests/test_proxy_install.py`
- Modify: `tests/test_proxy_config.py`（删 manifest 测试与 `__version__` import）
- Modify: `.github/workflows/ci.yml`（删 bash -n 步骤——脚本已不存在）
- Modify: `docs/superpowers/specs/2026-08-19-vision-relay-rebrand-docs-port-design.md`（修正 §7 用例数）

- [ ] **Step 1: 删除文件**

```bash
git rm -r src/
git rm install.sh install-proxy.sh tests/test_proxy_install.py
```

- [ ] **Step 2: 删 test_proxy_config.py 中的 manifest 测试**

删除 `test_proxy_manifests_are_standalone_non_mcp` 整个函数（含 docstring），以及顶部 `from qwen_mm_plugins_proxy import __version__` 一行（仅该测试使用）。

- [ ] **Step 3: ci.yml 删除 bash -n 步骤**

删除整个 `Check shell syntax` step（`bash -n install.sh` / `bash -n install-proxy.sh` 已无对象）。

- [ ] **Step 4: 修正 spec 用例数**

rebrand spec §7 第 1 条 `预期 **≥144 passed**（基线 159 − 删除 test_proxy_install.py 的 15 例 = 144；§5.3 迁移/回退逻辑新增的用例计入）` 改为 `预期 **≥143 passed**（基线 159 − install 测试 15 例 − manifest 测试 1 例 = 143；§5.3 迁移/回退逻辑新增 6 例后终点 149）`。

- [ ] **Step 5: 跑测试验证**

```bash
python -m pytest -q
# 预期：143 passed
```

- [ ] **Step 6: 提交**

```bash
git add -A
git commit -m "chore: remove upstream plugin-marketplace packaging (manifests, install scripts, their tests)"
```

---

### Task 3: 包改名 vision_relay + env_util 并入

**Files:**
- Rename: `qwen_mm_plugins_proxy/` → `vision_relay/`
- Rename: `proxy_env.py` → `vision_relay/env_util.py`（内容重写）
- Modify: `vision_relay/cli.py`、`config.py`、`logging_util.py`、`onboarding.py`、`__init__.py`、`wiring.py`
- Modify: `pyproject.toml`（重写）、`ruff.toml`、`CONTRIBUTING.md`
- Modify: `tests/` 11 个文件（sed 批量 + manifest 相关已在 Task 2 处理）

- [ ] **Step 1: git mv**

```bash
git mv qwen_mm_plugins_proxy vision_relay
git mv proxy_env.py vision_relay/env_util.py
```

- [ ] **Step 2: 重写 env_util.py（本任务先做"纯改名版"，兼容层在 Task 4）**

`vision_relay/env_util.py` 全文：

```python
"""Env/config accessor for vision-relay: env vars first, shell-style config file fallback.

VISION_RELAY_CONFIG_DIR / VISION_RELAY_CONFIG point at ~/.vision-relay and
~/.vision-relay/config. (Legacy Qwen-MM-Plugins era names are handled by the
compat layer; see the final version of this module.)
"""
from __future__ import annotations

import os


def get_env(name: str, default: str | None = None) -> str | None:
    val = os.environ.get(name)
    return val if val is not None else _config().get(name, default)


def config_dir() -> str:
    return os.path.expanduser(os.environ.get("VISION_RELAY_CONFIG_DIR") or "~/.vision-relay")


def _parse_config(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
            val = val[1:-1]
        if key.strip():
            out[key.strip()] = val
    return out


_config_cache: dict[str, str] | None = None


def config_file() -> str:
    override = os.environ.get("VISION_RELAY_CONFIG")
    return os.path.expanduser(override) if override else os.path.join(config_dir(), "config")


def _config() -> dict[str, str]:
    global _config_cache
    if _config_cache is None:
        try:
            with open(config_file(), encoding="utf-8") as f:
                _config_cache = _parse_config(f.read())
        except (OSError, UnicodeDecodeError):
            _config_cache = {}
    return _config_cache
```

- [ ] **Step 3: 改包内 5 处 proxy_env import 与身份字符串**

| 文件 | 编辑 |
|---|---|
| `vision_relay/cli.py:11` | `from proxy_env import config_dir` → `from .env_util import config_dir` |
| `vision_relay/cli.py:58` | `请用交互终端重跑 qwen-mm-plugins-proxy start，或 qwen-mm-plugins-proxy models-scan 复核。` → `请用交互终端重跑 vision-relay start，或 vision-relay models-scan 复核。` |
| `vision_relay/cli.py:72` | `print(f"qwen-mm-plugins-proxy listening on ...")` → `print(f"vision-relay listening on ...")` |
| `vision_relay/cli.py:265` | `" → 请重跑 qwen-mm-plugins-proxy start 重新接线"` → `" → 请重跑 vision-relay start 重新接线"` |
| `vision_relay/cli.py:272` | `首次启用需走交互引导（qwen-mm-plugins-proxy start 或 models-scan）` → `首次启用需走交互引导（vision-relay start 或 models-scan）` |
| `vision_relay/config.py:13` | `from proxy_env import get_env` → `from .env_util import get_env` |
| `vision_relay/config.py:166,181` | 两处函数内 `from proxy_env import config_dir` → `from .env_util import config_dir` |
| `vision_relay/config.py:1` | docstring `(~/.qwen-mm-plugins/proxy.json, 0600)` → `(~/.vision-relay/proxy.json, 0600)` |
| `vision_relay/config.py:4` | docstring `Read via shared.env.get_env for env overrides.` → `Read via vision_relay.env_util.get_env for env overrides.` |
| `vision_relay/config.py:144` | docstring `Env overrides (QWEN_MM_PROXY_*), applied at load time via shared.env.get_env.` → `Env overrides (VISION_RELAY_*), applied at load time via vision_relay.env_util.get_env.` |
| `vision_relay/config.py:145-157` | `QWEN_MM_PROXY_*` 五个变量名 → `VISION_RELAY_*`（`_apply_env` 本任务只改名，legacy 回退在 Task 4） |
| `vision_relay/logging_util.py:1,9` | docstring 路径改 `~/.vision-relay/logs/proxy.log`；import 改 `from .env_util import config_dir` |
| `vision_relay/onboarding.py:261` | docstring `qwen-mm-plugins-proxy models` → `vision-relay models` |
| `vision_relay/wiring.py:12` | `BAK_SUFFIX = ".qwen-mm-proxy.bak"` → `BAK_SUFFIX = ".vision-relay.bak"`（legacy 回退在 Task 4） |
| `vision_relay/__init__.py` | 整文件替换（见 Step 4） |

- [ ] **Step 4: 重写 `vision_relay/__init__.py`**

```python
"""vision-relay: a transparent HTTP proxy at the harness boundary that gives text-only models vision.

Standalone resident HTTP server on 127.0.0.1:8787 that intercepts images in
Anthropic / Responses / Chat requests, transcribes them via a VLM, and forwards
text to the real upstream. Design: docs/superpowers/specs/2026-08-13-vision-relay-design.md.
"""

__version__ = "0.1.0"
```

- [ ] **Step 5: 重写 pyproject.toml**

```toml
[build-system]
requires = ["setuptools>=77", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "vision-relay"
dynamic = ["version"]
description = "vision-relay: a transparent HTTP proxy at the harness boundary that gives text-only models vision - intercepts images in Anthropic / Responses / Chat requests, transcribes them via a VLM, and relays the text to the real upstream text model."
readme = "README.md"
requires-python = ">=3.10"
license = "Apache-2.0"
license-files = ["LICENSE"]
authors = [{ name = "jarvislee90s-dot" }]
keywords = ["proxy", "vision", "vlm", "anthropic", "responses", "chat", "agent", "relay"]
classifiers = [
    "Development Status :: 4 - Beta",
    "Intended Audience :: Developers",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.10",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Operating System :: OS Independent",
    "Topic :: Scientific/Engineering :: Artificial Intelligence",
]
dependencies = [
    "httpx",
]

[project.urls]
Homepage = "https://github.com/jarvislee90s-dot/vision-relay"
Repository = "https://github.com/jarvislee90s-dot/vision-relay"

[project.scripts]
vision-relay = "vision_relay.__main__:main"

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.setuptools.packages.find]
where = ["."]
include = ["vision_relay*"]

[tool.setuptools.dynamic]
version = { attr = "vision_relay.__version__" }
```

（相比旧文件：删掉 `qwen-mm-plugins-proxy` 别名 script、删掉 `[tool.setuptools] py-modules = ["proxy_env"]` 段、包名与动态版本指向 `vision_relay`。）

- [ ] **Step 6: ruff.toml 与 CONTRIBUTING.md**

`ruff.toml`：

```toml
# Ruff config for vision-relay
# Lint focuses on real problems (pyflakes F = unused imports/vars/dead code);
# E501 is ignored because some protocol field names / URLs are intentionally long.

target-version = "py310"
line-length = 120
src = ["vision_relay"]

[lint]
select = ["E", "F", "W", "I"]
ignore = ["E501"]

[lint.isort]
known-first-party = ["vision_relay"]
```

`CONTRIBUTING.md` 三处编辑（其余保留，AI 条款在 Task 6 加）：
- `python -m pip install -e '.[proxy]'` → `python -m pip install -e .`（extras 不存在，上游残留 bug）
- `Keep proxy protocol logic in qwen_mm_plugins_proxy/; each module has one clear job.` → `Keep proxy protocol logic in vision_relay/; each module has one clear job (see AGENTS.md for the architecture invariants).`
- `bash -n install-proxy.sh` 一行删除（脚本已删）。

- [ ] **Step 7: 测试批量改名（sed）**

```bash
# 包标识符（import + monkeypatch 字符串路径，如 "qwen_mm_plugins_proxy.vlm.httpx.Client"）
grep -rl "qwen_mm_plugins_proxy" tests/ | xargs sed -i 's/qwen_mm_plugins_proxy/vision_relay/g'
# 环境变量
grep -rl "QWEN_MM_PROXY_\|QWEN_MM_CONFIG_DIR" tests/ | xargs sed -i -e 's/QWEN_MM_PROXY_/VISION_RELAY_/g' -e 's/QWEN_MM_CONFIG_DIR/VISION_RELAY_CONFIG_DIR/g'
# 备份后缀断言
grep -rl "qwen-mm-proxy.bak" tests/ | xargs sed -i 's/qwen-mm-proxy\.bak/vision-relay.bak/g'
# 终检：不应再有旧名（白名单外）
grep -rn "qwen_mm_plugins_proxy\|QWEN_MM_PROXY_\|QWEN_MM_CONFIG" tests/ vision_relay/ pyproject.toml ruff.toml CONTRIBUTING.md
# 预期：0 命中
```

- [ ] **Step 8: 全量验证并提交**

```bash
python -m pytest -q
# 预期：143 passed
ruff check .
ruff format --check .
git add -A
git commit -m "refactor!: rename package qwen_mm_plugins_proxy -> vision_relay; fold proxy_env into vision_relay/env_util"
```

---

### Task 4: 兼容层 TDD（读旧写新：env 回退 / 配置迁移 / 旧备份还原）+ --version

**Files:**
- Test: `tests/test_proxy_config.py`（追加 3 个用例）
- Test: `tests/test_proxy_routing.py`（追加 2 个用例）
- Test: `tests/test_proxy_cli.py`（追加 1 个用例）
- Modify: `vision_relay/env_util.py`（升级为兼容版）、`vision_relay/config.py`、`vision_relay/wiring.py`、`vision_relay/cli.py`

- [ ] **Step 1: 写失败测试——test_proxy_config.py 末尾追加**

```python
# ── 独立化兼容层：读旧写新 ────────────────────────────────────────────
def test_legacy_env_vars_still_override_with_warning(tmp_path: Path, monkeypatch, capsys):
    """旧 QWEN_MM_PROXY_* 环境变量回退读取（一次性 deprecation 提示）。"""
    from vision_relay import env_util

    monkeypatch.setattr(env_util, "_warned", set())
    monkeypatch.delenv("VISION_RELAY_BIND_PORT", raising=False)
    monkeypatch.setenv("QWEN_MM_PROXY_BIND_PORT", "9100")
    cfg_path = tmp_path / "proxy.json"
    cfg_path.write_text(json.dumps({"server": {"bind_port": 9000}}))
    cfg = load_config(str(cfg_path))
    assert cfg.bind_port == 9100
    assert "deprecated" in capsys.readouterr().err


def test_new_env_vars_win_over_legacy(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("VISION_RELAY_BIND_PORT", "9200")
    monkeypatch.setenv("QWEN_MM_PROXY_BIND_PORT", "9100")
    cfg_path = tmp_path / "proxy.json"
    cfg_path.write_text(json.dumps({"server": {"bind_port": 9000}}))
    assert load_config(str(cfg_path)).bind_port == 9200


def test_load_config_reads_legacy_dir_and_save_migrates(tmp_path: Path, monkeypatch, capsys):
    """读旧写新：新目录无 proxy.json 时回退读旧目录；save_config 落盘到新目录。"""
    from vision_relay import env_util

    new_dir, old_dir = tmp_path / "new", tmp_path / "old"
    new_dir.mkdir()
    old_dir.mkdir()
    (old_dir / "proxy.json").write_text(json.dumps({"server": {"bind_port": 9300}}), encoding="utf-8")
    monkeypatch.setattr(env_util, "config_dir", lambda: str(new_dir))
    monkeypatch.setattr(env_util, "legacy_config_dir", lambda: str(old_dir))
    cfg = load_config()
    assert cfg.bind_port == 9300
    assert "legacy" in capsys.readouterr().err.lower()
    save_config(cfg)
    assert (new_dir / "proxy.json").exists()
```

（文件顶部 import 区把 `save_config` 加入 `from vision_relay.config import (...)` 列表。）

- [ ] **Step 2: 写失败测试——test_proxy_routing.py 末尾追加**

```python
# ── 独立化兼容层：旧后缀备份还原 ──────────────────────────────────────
def test_wiring_restore_accepts_legacy_bak(tmp_path, monkeypatch):
    """旧版 .qwen-mm-proxy.bak 备份也能被还原（升级前接的线，升级后 stop 仍能收尾）。"""
    home = _mk_home(tmp_path)
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path / "cfg"))
    claude_dir = home / ".claude"
    claude_dir.mkdir()
    f = claude_dir / "settings.json"
    f.write_text(json.dumps({"env": {"ANTHROPIC_BASE_URL": "http://127.0.0.1:8787"}}), encoding="utf-8")
    legacy_bak = claude_dir / "settings.json.qwen-mm-proxy.bak"
    legacy_bak.write_text(json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://real.example"}}), encoding="utf-8")
    cfg = ProxyConfig(bind_port=8787, routing=RoutingConfig(harnesses=["claude"]))
    msg = wiring.wiring_restore(cfg)
    assert any("claude: restored" in m for m in msg)
    assert json.loads(f.read_text(encoding="utf-8"))["env"]["ANTHROPIC_BASE_URL"] == "https://real.example"
    assert not legacy_bak.exists()


def test_wiring_backup_does_not_shadow_legacy_bak(tmp_path, monkeypatch):
    """已有旧后缀备份时 start 不再新建新后缀备份（防止把指向代理的当前值存成新备份）。"""
    home = _mk_home(tmp_path)
    monkeypatch.setenv("VISION_RELAY_CONFIG_DIR", str(tmp_path / "cfg"))
    claude_dir = home / ".claude"
    claude_dir.mkdir()
    f = claude_dir / "settings.json"
    f.write_text(json.dumps({"env": {"ANTHROPIC_BASE_URL": "http://127.0.0.1:8787"}}), encoding="utf-8")
    (claude_dir / "settings.json.qwen-mm-proxy.bak").write_text(
        json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://real.example"}}), encoding="utf-8"
    )
    cfg = ProxyConfig(bind_port=8787, routing=RoutingConfig(harnesses=["claude"]))
    wiring.wiring_backup_and_rewrite(cfg)
    assert not (claude_dir / "settings.json.vision-relay.bak").exists()
```

- [ ] **Step 3: 写失败测试——test_proxy_cli.py 末尾追加**

```python
def test_version_flag(capsys):
    from vision_relay import __version__
    from vision_relay.cli import parse_args

    with pytest.raises(SystemExit) as exc:
        parse_args(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out
```

- [ ] **Step 4: 跑测试确认失败**

```bash
python -m pytest -q tests/test_proxy_config.py tests/test_proxy_routing.py tests/test_proxy_cli.py
# 预期：6 个新用例 FAIL——旧环境变量名不被读取（bind_port 断言失败）、legacy 目录不回退、
# 旧后缀备份不被 wiring_restore 识别、--version 是未知选项（SystemExit code 2）；其余 143 通过
```

- [ ] **Step 5: 升级 env_util.py 为兼容版（整文件替换）**

```python
"""Env/config accessor for vision-relay: env vars first, shell-style config file fallback.

VISION_RELAY_CONFIG_DIR / VISION_RELAY_CONFIG point at ~/.vision-relay and
~/.vision-relay/config. Legacy Qwen-MM-Plugins era env names (QWEN_MM_*) are
still honored with a one-time deprecation warning, so existing setups keep
working while migrating.
"""
from __future__ import annotations

import os
import sys

_warned: set[str] = set()


def _env(name: str, legacy: str | None = None) -> str | None:
    val = os.environ.get(name)
    if val is not None:
        return val
    if legacy is not None:
        val = os.environ.get(legacy)
        if val is not None:
            if legacy not in _warned:
                _warned.add(legacy)
                print(f"warning: env {legacy} is deprecated; use {name} instead", file=sys.stderr)
            return val
    return None


def get_env(name: str, legacy: str | None = None, default: str | None = None) -> str | None:
    val = _env(name, legacy)
    return val if val is not None else _config().get(name, default)


def config_dir() -> str:
    """State dir for vision-relay (config, pid, logs)."""
    if v := _env("VISION_RELAY_CONFIG_DIR", "QWEN_MM_CONFIG_DIR"):
        return os.path.expanduser(v)
    return os.path.expanduser("~/.vision-relay")


def legacy_config_dir() -> str:
    """Qwen-MM-Plugins era config dir; read-only fallback for migration."""
    return os.path.expanduser("~/.qwen-mm-plugins")


def _parse_config(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
            val = val[1:-1]
        if key.strip():
            out[key.strip()] = val
    return out


_config_cache: dict[str, str] | None = None


def config_file() -> str:
    override = _env("VISION_RELAY_CONFIG", "QWEN_MM_CONFIG")
    return os.path.expanduser(override) if override else os.path.join(config_dir(), "config")


def _config() -> dict[str, str]:
    global _config_cache
    if _config_cache is None:
        try:
            with open(config_file(), encoding="utf-8") as f:
                _config_cache = _parse_config(f.read())
        except (OSError, UnicodeDecodeError):
            _config_cache = {}
    return _config_cache
```

- [ ] **Step 6: config.py —— _apply_env 加 legacy 参数 + load_config 读旧目录**

`_apply_env` 整函数替换为：

```python
def _apply_env(cfg: ProxyConfig) -> ProxyConfig:
    """Env overrides (VISION_RELAY_*; legacy QWEN_MM_PROXY_* still honored, with a warning)."""
    if v := get_env("VISION_RELAY_BIND_PORT", legacy="QWEN_MM_PROXY_BIND_PORT"):
        cfg.bind_port = int(v)
    if v := get_env("VISION_RELAY_VLM_MODEL", legacy="QWEN_MM_PROXY_VLM_MODEL"):
        cfg.vlm.model = v
    if v := get_env("VISION_RELAY_VLM_BASE_URL", legacy="QWEN_MM_PROXY_VLM_BASE_URL"):
        cfg.vlm.base_url = v
    # API key 用 is not None 而非真值判断：环境变量显式设为空串也必须清掉配置里的 key。
    # 否则 walrus 写法会把空串 '' 当 falsy 跳过，导致 T7 这类"拔 VLM key 测 fail-open"永远失效
    # （proxy.json 里的 key 原样保留，VLM 照常被调用）。
    vlm_key = get_env("VISION_RELAY_VLM_API_KEY", legacy="QWEN_MM_PROXY_VLM_API_KEY")
    if vlm_key is not None:
        cfg.vlm.api_key = vlm_key
    if v := get_env("VISION_RELAY_VLM_FORMAT", legacy="QWEN_MM_PROXY_VLM_FORMAT"):
        if v in VLM_FORMATS:
            cfg.vlm.format = v
    return cfg
```

`load_config` 的默认路径逻辑替换（函数体其余部分不动），并在 import 区加 `import sys`：

```python
def _default_config_path() -> str:
    from .env_util import config_dir

    return os.path.join(config_dir(), "proxy.json")


def _legacy_config_path() -> str:
    from .env_util import legacy_config_dir

    return os.path.join(legacy_config_dir(), "proxy.json")


def load_config(path: str | None = None) -> ProxyConfig:
    if path is None:
        path = _default_config_path()
        if not os.path.exists(path):
            legacy = _legacy_config_path()
            if os.path.exists(legacy):
                path = legacy
                print(
                    f"note: using legacy config {legacy}; it will move to {_default_config_path()} on next save",
                    file=sys.stderr,
                )
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        # 首次运行无配置文件：回退默认（合法），由 check 提示未配置。
        return default_config()
    except (OSError, ValueError) as exc:
        raise ConfigError(f"cannot read proxy.json: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"proxy.json: expected a JSON object at top level, got {type(raw).__name__}")
    try:
        cfg = ProxyConfig.from_dict(raw)
    except ConfigError:
        raise
    except (ValueError, TypeError, AttributeError) as exc:
        raise ConfigError(f"invalid proxy.json: {exc}") from exc
    return _apply_env(cfg)
```

（`save_config` 不用改——它本来就经 `config_dir()` 写新目录，"读旧写新"的迁移半边自动成立。）

- [ ] **Step 7: wiring.py —— 双后缀识别**

模块常量与三个函数的修改：

```python
BAK_SUFFIX = ".vision-relay.bak"
LEGACY_BAK_SUFFIX = ".qwen-mm-proxy.bak"


def _find_bak(p: str) -> str | None:
    new = p + BAK_SUFFIX
    if os.path.exists(new):
        return new
    old = p + LEGACY_BAK_SUFFIX
    if os.path.exists(old):
        return old
    return None
```

- `wiring_backup_and_rewrite` 中备份段（`bak = _bak_path(p)` 起三行）替换为：

```python
        if _find_bak(p) is None:  # 已有备份（含旧后缀）不覆盖，防止把代理地址存成"原始值"
            try:
                os.makedirs(os.path.dirname(p), exist_ok=True)
                import shutil

                shutil.copyfile(p, p + BAK_SUFFIX)
            except OSError:
                pass
```

- `wiring_restore` 中 `bak = _bak_path(p)` / `if not os.path.exists(bak): continue` 替换为 `bak = _find_bak(p)` / `if bak is None: continue`（还原后 `os.unlink(bak)` 原样——删的是实际使用的那个备份文件）。
- `wiring_report` 中 `"has_backup": os.path.exists(_bak_path(p))` 替换为 `"has_backup": _find_bak(p) is not None`。
- `_bak_path` 函数删除（无剩余调用者）。

- [ ] **Step 8: cli.py —— --version**

```python
from . import __version__


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="vision-relay")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    # ……以下不变
```

- [ ] **Step 9: 跑测试确认通过并提交**

```bash
python -m pytest -q
# 预期：149 passed（143 + 6 新用例）
ruff check .
git add -A
git commit -m "feat: read-old-write-new compat layer (legacy env vars, config dir migration, legacy .bak restore); add --version"
```

---

### Task 5: README 双语更新

**Files:**
- Modify: `README.md`、`README.zh.md`

- [ ] **Step 1: README.md 编辑**

1. 第 73 行附近 `~/.qwen-mm-plugins/proxy.json` → `~/.vision-relay/proxy.json`。
2. Configuration 节整段替换为：

```markdown
## Configuration

Shared config lives in `~/.vision-relay/config` (fallback for env vars); proxy settings in `~/.vision-relay/proxy.json`. Env overrides: `VISION_RELAY_BIND_PORT`, `VISION_RELAY_VLM_MODEL`, `VISION_RELAY_VLM_BASE_URL`, `VISION_RELAY_VLM_API_KEY`, `VISION_RELAY_VLM_FORMAT` (config dir: `VISION_RELAY_CONFIG_DIR`).

### Upgrading from qwen-mm-plugins-proxy

vision-relay is the standalone successor of the `qwen-mm-plugins-proxy` capability. On first start it reads an existing `~/.qwen-mm-plugins/proxy.json` automatically and migrates it to `~/.vision-relay/` on next save; legacy `QWEN_MM_PROXY_*` env vars and `.qwen-mm-proxy.bak` wiring backups are still recognized (with a deprecation warning).
```

- [ ] **Step 2: README.zh.md 编辑**

1. 第 69 行附近 `~/.qwen-mm-plugins/proxy.json` → `~/.vision-relay/proxy.json`。
2. 配置节整段替换为：

```markdown
## 配置

共享配置在 `~/.vision-relay/config`(作为环境变量的回退);代理设置在 `~/.vision-relay/proxy.json`。环境变量覆盖:`VISION_RELAY_BIND_PORT`、`VISION_RELAY_VLM_MODEL`、`VISION_RELAY_VLM_BASE_URL`、`VISION_RELAY_VLM_API_KEY`、`VISION_RELAY_VLM_FORMAT`(配置目录:`VISION_RELAY_CONFIG_DIR`)。

### 从 qwen-mm-plugins-proxy 升级

vision-relay 是 `qwen-mm-plugins-proxy` 能力的独立继任者。首次启动会自动读取已有的 `~/.qwen-mm-plugins/proxy.json`,并在下次保存时迁移到 `~/.vision-relay/`;旧 `QWEN_MM_PROXY_*` 环境变量与 `.qwen-mm-proxy.bak` 接线备份仍被识别(带 deprecation 提示)。
```

- [ ] **Step 3: 提交**

```bash
git add README.md README.zh.md
git commit -m "docs: READMEs point at ~/.vision-relay and VISION_RELAY_* env vars; add upgrade note"
```

---

### Task 6: P1 仓库管理脚手架

**Files:**
- Create: `.github/PULL_REQUEST_TEMPLATE.md`、`.github/ISSUE_TEMPLATE/feature_request.yml`、`.github/ISSUE_TEMPLATE/config.yml`、`CHANGELOG.md`、`AGENTS.md`
- Modify: `.github/workflows/ci.yml`（重写）、`CONTRIBUTING.md`（重写）、`.gitignore`（追加）

- [ ] **Step 1: 写 `.github/PULL_REQUEST_TEMPLATE.md`**

```markdown
## What changed

<!-- One-paragraph summary; link related issues -->

## Why

<!-- The problem this solves and the reason for this approach -->

## Verification

<!-- Commands you ran and their results -->

- [ ] `python -m pytest -q` passes
- [ ] `ruff format --check .` and `ruff check .` pass

## Compatibility

- [ ] No breaking change to CLI commands/flags
- [ ] No breaking change to `proxy.json` config keys (or documented in CHANGELOG)
- [ ] Protocol handling (ir.py parse/serialize) unchanged, or the change is explained above
- [ ] fail-open semantics preserved (proxy failures never 4xx/deadlock a request)
- [ ] No API keys, credentials, private media, or machine-specific config in this PR
```

- [ ] **Step 2: 写 `.github/ISSUE_TEMPLATE/feature_request.yml`**

```yaml
name: Feature request
description: Suggest a new capability or improvement for vision-relay
title: "[Feature] "
labels: ["enhancement"]
body:
  - type: markdown
    attributes:
      value: |
        Thanks for the suggestion. For bigger designs, please open a discussion first — large features go through a spec in `docs/superpowers/specs/` before implementation.
  - type: textarea
    id: problem
    attributes:
      label: What problem would this solve?
      description: What are you trying to do that vision-relay currently cannot do?
    validations:
      required: true
  - type: textarea
    id: solution
    attributes:
      label: Proposed solution
      description: How should it work? Which harness / protocol / VLM does it involve?
    validations:
      required: true
  - type: textarea
    id: alternatives
    attributes:
      label: Alternatives you considered
  - type: input
    id: version
    attributes:
      label: Version or commit
      placeholder: Release, commit SHA, or install date
```

- [ ] **Step 3: 写 `.github/ISSUE_TEMPLATE/config.yml`**

```yaml
blank_issues_enabled: false
contact_links:
  - name: Security vulnerability
    url: https://github.com/jarvislee90s-dot/vision-relay/security/advisories/new
    about: Report security issues privately, not in a public issue (see SECURITY.md).
```

- [ ] **Step 4: 写 `CHANGELOG.md`**

```markdown
# Changelog

All notable changes to vision-relay will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- Renamed the Python package `qwen_mm_plugins_proxy` to `vision_relay`; the console script is now `vision-relay` only (the `qwen-mm-plugins-proxy` alias is gone).
- Configuration moved from `~/.qwen-mm-plugins/` to `~/.vision-relay/`; an existing `~/.qwen-mm-plugins/proxy.json` is read automatically and migrates on next save.
- Environment variables renamed `QWEN_MM_PROXY_*` / `QWEN_MM_CONFIG*` to `VISION_RELAY_*` (legacy names still honored with a deprecation warning).
- Harness wiring backups now use the `.vision-relay.bak` suffix; older `.qwen-mm-proxy.bak` backups are still recognized by `vision-relay stop`.

### Removed
- Upstream plugin-marketplace packaging: `src/capabilities/proxy/` manifests, `install.sh`, `install-proxy.sh`, and their tests. Install with `pip install vision-relay` and run `vision-relay start`.

### Added
- `vision-relay --version` flag.
- Ported design docs from the Qwen-MM-Plugins-plus development repo: Phase-1 spec, Phase-2 roadmap, implementation plan, acceptance checklist, manual test guide, and ecosystem research; PR #40 body archived under `docs/history/`.
- Repository scaffolding: PR template, feature-request issue form, AGENTS.md, CHANGELOG, and a CI matrix for Python 3.10–3.13 on Linux/macOS/Windows plus a build-and-install smoke job.
```

- [ ] **Step 5: 写 `AGENTS.md`**

```markdown
# AGENTS.md — working rules for AI coding agents in this repo

These rules keep agent-driven changes consistent with the design in
`docs/superpowers/specs/`. Read them before touching code.

## Project identity (naming alignment)

| Layer | Name |
|---|---|
| PyPI package / repo | `vision-relay` |
| Python package (import) | `vision_relay` |
| Console command | `vision-relay` |
| Config directory | `~/.vision-relay/` (config: `proxy.json`, logs: `logs/proxy.log`) |
| Env vars | `VISION_RELAY_*` (`VISION_RELAY_CONFIG_DIR`, `VISION_RELAY_VLM_API_KEY`, ...) |

Legacy `QWEN_MM_*` env names and `~/.qwen-mm-plugins/proxy.json` are read-only
compatibility fallbacks — never write to them, never use them in new code.

`qwen-code` / `claude` / `codex` inside `HARNESSES`, `HARNESS_CFG`, and
`_HARNESS_BY_PROTO` are *harness adapters* (they point at real config files
like `~/.qwen/settings.json`), not project naming. Do not "de-Qwen" them.
Default VLM model names (`qwen-vl-max`, DashScope base URL) are real service
defaults, not project naming either.

## Architecture invariants

- Protocol parsing/serialization lives only in `ir.py` (IR + parse_*/serialize_*).
  All three protocols are parse/serialize shells around the IR; image handling
  is written once against the IR (`pipeline.py`), never per-protocol.
- fail-open is a hard invariant: any proxy-internal failure (VLM down, parse
  error, overflow) must degrade to a text injection, never a 4xx/deadlock.
- Vision models pass through untouched; text-only models go through the
  pipeline; unknown models default to text-only (safe side).
- API keys live only in `~/.vision-relay/proxy.json` (0600) or env; logs must
  never contain keys (`log_json` strips them).
- The proxy must remain the first hop: wiring rewrites harness base_url to
  `http://127.0.0.1:<bind_port>` with backup + guarded restore.

## Workflow rules

- Tests first for behavior changes: `python -m pytest -q` must stay green;
  add tests for new behavior in the matching `tests/test_proxy_*.py`.
- Before opening a PR: `ruff format --check .`, `ruff check .`, full pytest.
- Behavior changes update the matching spec under `docs/superpowers/specs/`
  in the same PR.
- Docs follow the superpowers convention: `docs/superpowers/specs/` (design),
  `docs/superpowers/plans/` (implementation), `research/` (surveys),
  `docs/history/` (as-written records).
- Never commit API keys, private test media, or machine-specific paths.
```

- [ ] **Step 6: 重写 `.github/workflows/ci.yml`**

```yaml
name: CI

on:
  pull_request:
  push:
    branches: [main]

permissions:
  contents: read

jobs:
  test:
    name: ${{ matrix.os }} / py${{ matrix.python-version }}
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, macos-latest, windows-latest]
        python-version: ["3.10", "3.11", "3.12", "3.13"]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false

      - name: Set up uv and Python
        uses: astral-sh/setup-uv@08807647e7069bb48b6ef5acd8ec9567f424441b # v8.1.0
        with:
          version: "0.10.9"
          python-version: ${{ matrix.python-version }}
          enable-cache: true

      - name: Run offline tests
        env:
          PYTHONUTF8: "1"
        run: >-
          uv run --no-project
          --python ${{ matrix.python-version }}
          --with pytest
          --with httpx
          python -m pytest -q

      - name: Lint
        run: |
          uv run --no-project --python ${{ matrix.python-version }} --with ruff ruff check .
          uv run --no-project --python ${{ matrix.python-version }} --with ruff ruff format --check .

  packaging:
    name: build + install smoke
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false

      - name: Set up uv and Python
        uses: astral-sh/setup-uv@08807647e7069bb48b6ef5acd8ec9567f424441b # v8.1.0
        with:
          version: "0.10.9"
          python-version: "3.12"
          enable-cache: true

      - name: Build wheel and smoke-test it
        run: |
          uv build
          uv venv .smoke
          uv pip install --python .smoke dist/*.whl
          .smoke/bin/python -m vision_relay --version
          .smoke/bin/vision-relay --version
```

（Windows 首跑若暴露真实测试失败，修代码而不是缩矩阵——那正是加 Windows 的意义。）

- [ ] **Step 7: 重写 `CONTRIBUTING.md`**

```markdown
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

- Keep proxy protocol logic in `vision_relay/`; each module has one clear job (see AGENTS.md for the architecture invariants).
- Preserve existing CLI flags, protocol handling, and config keys unless the change is required to fix functionality. Explain any interface change in the PR.
- Import optional dependencies lazily.
- Do not commit API keys, credentials, private test media, generated artifacts, or machine-specific configuration.
- Add or update tests and documentation when behavior changes; behavior changes also update the matching spec under `docs/superpowers/specs/`.

## Verification

Run the full offline test suite and lint before opening a PR:

```bash
python -m pytest -q
ruff format --check .
ruff check .
```

If a test needs a live VLM/upstream or hardware not available to you, state what was not run in the PR.

## Larger changes

Features that change protocol handling, the image pipeline, or configuration semantics go through a short design spec first (`docs/superpowers/specs/`) — open an issue to discuss before investing in a big PR.

## AI-assisted contributions

PRs written with AI assistance are welcome, but you must be able to explain every line you submit. Keep them small and focused; maintainers may close PRs that look like unreviewed bulk AI output.

## Pull requests

Describe the problem and the reason for the chosen fix, link related issues, and include the commands and results used for verification. Keep unrelated changes in separate PRs.

Report security issues according to SECURITY.md, not through a public issue. Contributions are licensed under the repository's Apache-2.0 license.
```

- [ ] **Step 8: `.gitignore` 追加一行**

```
项目启动提示词.txt
```

- [ ] **Step 9: 提交**

```bash
git add .github/ CHANGELOG.md AGENTS.md CONTRIBUTING.md .gitignore
git commit -m "chore: P1 repo scaffolding (PR/issue templates, CHANGELOG, AGENTS.md, CI matrix + packaging smoke)"
```

---

### Task 7: 最终验证

**Files:**
- 视问题而定（修复即提交）

- [ ] **Step 1: 全量离线测试 + lint**

```bash
python -m pytest -q
# 预期：149 passed
ruff check . && ruff format --check .
# 预期：无输出（干净）
```

- [ ] **Step 2: 打包安装冒烟（本地复现 CI 的 packaging job + spec §7.3 的 editable 安装）**

```bash
python -m venv .smoke
.smoke/Scripts/python -m pip install -e .          # Windows；Linux/macOS 为 .smoke/bin/python
.smoke/Scripts/python -m vision_relay --version    # 预期输出 vision-relay 0.1.0
.smoke/Scripts/python -m pip install build
.smoke/Scripts/python -m build 2>&1 | tail -2      # 预期 successfully built vision_relay-0.1.0 (sdist + wheel)
rm -rf .smoke dist *.egg-info
```

- [ ] **Step 3: 命名白名单审计（spec §7 第 4 条）**

```bash
grep -rni "qwen" --include="*.py" --include="*.toml" --include="*.yml" --include="*.json" --include="*.sh" --include="*.md" . | grep -v ".git/"
```

逐条核对命中项，**允许**存在的只有：`HARNESSES/HARNESS_CFG/_HARNESS_BY_PROTO` 中的 `qwen-code` harness 适配、`~/.qwen/settings.json` 路径、`qwen-vl-max` 默认模型与 DashScope 端点、`capability.py` 的 `qwen-vl-*`/`qwen3.5-omni-*` 模式、测试数据里的 qwen-* 模型名、README/spec/research/history 中作为受支持 harness 或出处署名的 "Qwen Code" / "Qwen-MM-Plugins" 表述、兼容层的 `QWEN_MM_*`/`~/.qwen-mm-plugins` 字面量（env_util.py、config.py、wiring.py、README 升级说明）。其余任何命中都要处理。

- [ ] **Step 4: 兼容性手测（用户机器上的真实迁移路径，可选但推荐）**

```bash
vision-relay status        # not running（新 PID 路径）
vision-relay check         # 应提示读取 legacy config（若 ~/.qwen-mm-plugins/proxy.json 存在）
```

- [ ] **Step 5: 修复与收尾提交（如有）**

```bash
# 修复 Step 1-4 发现的问题后：
git add -A
git commit -m "fix: post-verification cleanup"
# 若全部干净则无此提交
```

---

## Self-Review 记录（写计划时已核）

1. **Spec 覆盖**：spec §3 结构（Task 1 全部文件）、§4 移植表（Task 1 Step 1-12 逐文件）、§5.1 R1-R13（R1/R2/R5 Task 3；R3 Task 3 Step 5；R4/R6 Task 3 Step 2 + Task 4 Step 5-6；R7 Task 3 Step 3 + Task 4 Step 7；R8 Task 3 Step 3；R9 Task 3 Step 4；R10 Task 2；R11 Task 3 Step 6；R12 Task 5；R13 Task 4 Step 8）、§5.3 兼容设计（Task 4）、§6 脚手架 8 项（Task 6 Step 1-8 一一对应）、§7 验证（Task 7，含 §7.3 的 editable 安装冒烟）、§8 不做项（未纳入任何任务 ✓）。
2. **占位符扫描**：所有代码步骤均给出完整代码块（load_config 给出替换后的完整函数体）；文档适配给出精确查找替换表与替换文本；无 TBD/TODO。
3. **类型一致性**：`get_env(name, legacy=None, default=None)` 签名在 Task 4 Step 5 定义、Step 6 按此调用；`_find_bak`/`legacy_config_dir` 定义与调用处一致；`_warned` 集合在 env_util 定义、测试经 monkeypatch 重置；test_proxy_config 的 `save_config` import 在 Step 1 显式加入。
4. **用例数**：159（基线）− 15（install）− 1（manifest）= 143；+6（Task 4）= 149。spec §7 数字已在 Task 2 Step 4 修正为一致。
5. **自检修订**（复核时修正）：Task 1 Step 2 去掉 Git Bash 不保证存在的 `file` 命令；Task 3 Step 3 补 config.py 第 4/144 行 docstring 的 shared.env 引用清理；Task 4 Step 4 的预期失败描述改为断言级（旧名不被读取等，而非 TypeError）；Task 7 Step 2 补 editable 安装（spec §7.3）。
