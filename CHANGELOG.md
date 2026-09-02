# Changelog

All notable changes to vision-relay will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.2] - 2026-09-02

真机回归修复批次（v1.0.1 Windows 实测反馈）：7 项修复 + 4 项体验优化。系统化排查（新旧分支逐行 diff + 运行时证据 + 真机复现）确认均为存量缺陷、与 1.0.1 的模块化重构无关（证据链见 `docs/superpowers/specs/2026-09-02-post-refactor-regression-fixes-design.md`）；v1.0.1 草稿发布作废，由本版取代。

### Fixed
- **打包版「开启路由」完全无法启动服务（安装包用户必更）**：分离重拉核心进程的 argv 带 `-m`，在 PyInstaller 冻结态下被 argparse 拒绝、孙进程静默死亡——不写 pid、不改写各工具配置、代理进程永不监听，点开关无任何效果；崩溃自愈自动重启同病。影响 v1.0.0 与 v1.0.1 全部安装包；源码运行不受影响。
- **cc-switch 两层转接偶发 401/502**：旧一次「吸收直连」遗留的 `direct-<harness>` 中继无密钥指纹钉死，选路按列表顺序截胡 `cc-anthropic` 等工具中继，带着 cc-switch 的会话 token 直连旧上游被拒。对账现于两跳接线真相（快照 second_hop 指向工具）下自动清理此类遗留中继；服务端热加载即生效。
- **设置页取消勾选 zcode 保存后重进又变回勾选**：旧默认全集迁移用「精确列表相等」判定，取消 zcode 后存盘恰等于旧默认 3 件套，每次读配置都把 zcode 加回（故单独取消 qwen、或同轮取消 zcode+qwen 均正常）。改为一次性升级哨兵 `routing.default_harnesses_upgraded`。
- **总览 zcode 卡显示 relay「已旁路」但实际请求在走中继**：zcode CLI 会把它托管的内置供应商条目在磁盘上改回原始地址（条目级漂移），而归属判定只看激活条目地址。zcode 归属改用条目级信号（任一可接管条目指本代理即「已接管」）。
- **「direct-claude 直连上游缺 API key」误报**：直连中继设计上无自有密钥（认证靠客户端请求头透传）。harness 配置里密钥位置真实存在（如 claude 的 `env.ANTHROPIC_AUTH_TOKEN`）时不再提示；真正缺失时保留提醒与补填入口。
- **模型能力页把 claude 的 `*_MODEL_NAME` 展示名当独立模型**（如 `GLM-5.3[1M]` 与 `GLM-5.3` 显示为两个模型）：模型收集白名单收紧为只认 `*_MODEL` 真实字段。
- **模型能力探测完成后「当前标注」列不更新**（切走页面再回来才可见）：探测写入的新标注被未保存的本地草稿遮住；探测成功后非用户手动标注的行自动清掉草稿，实测结果即时上屏。

### Changed
- 总览地址行显示「自动接线已开启/关闭」真实状态，替换无条件写死的「自动对账中」文案（服务未起时曾被误读为「卡在对账循环」）。
- 「探测全部未测」进行中按钮变为「⏹ 终止探测」（当前行跑完、剩余行跳过，汇总注明完成数）；探测候选收紧为真正未测的行——用户手动标注或已有实测结论的模型不再重复探测。
- 总览详情抽屉的中继列表按所属工具过滤（此前每个工具下都显示全部中继与停用按钮）；`direct-*` 直连中继标注「直连透传」与工具转发区分。
- （开发者）`tauri dev` 在 Windows 上随机崩溃修复：vite 文件监视器盯到 cargo 并发写入的 `target/` 构建产物（EBUSY 未处理直接崩 Node），vite watch 忽略 `src-tauri` 目录。

## [1.0.1] - 2026-09-01

稳定性补丁：修复 v1.0.0 在 Python 3.10 上的回落直连崩溃；两大子系统按职责模块化（零行为变更）。GUI 与 CLI 使用方式完全不变。

### Fixed
- Python 3.10 上 cc-switch（codex）请求期回落直连整条路径抛 `ModuleNotFoundError`：v1.0.0 的回落功能裸 `import tomllib`（3.11+ 标准库），而 3.10 是声明支持的最低版本（`requires-python >=3.10`）。现为守卫导入 + 正则兜底（取首个 `[model_providers.*]` 条目，同 model_sources 既有模式）。影响面：外层 fail-open 兜底一直在（代理不崩），但受影响请求会从设计上的「保持死端口 502 可见」劣化为整请求异常降级。pip 安装且 Python 3.10 的用户建议升级。

### Changed
- wiring（配置接线）与 CLI/verbs（参数解析 + 动词层）按职责拆分为单一职责模块，`wiring.py` / `cli.py` / `verbs.py` 收敛为组装层门面（PR #5，零行为变更：CLI 接口、输出文本、退出码、stdin JSON 协议、envelope 契约全部不变；既有测试断言零改动，测试 561→757、行覆盖率 85%→88%；迁移映射与守护测试索引见 `REFACTOR_NOTES.md`）。
- 发布流水线正文改为人工文案优先：`docs/release-notes/v<版本>.md` 存在则整体覆盖 CHANGELOG 兜底正文，重跑工作流不再盖掉已发布的图文公告（2026-08-27 v1.0.0 正文被覆盖事故的修复）。
- CI：GitHub 托管 macOS runner 上 9 个派生进程类 e2e 条件跳过——定性为 runner 环境对分离进程的清理/限制（本机 macOS 与 ubuntu/windows CI 全过），非代码问题，根因排查后移除。
- README 按读者漏斗重排：对比表讲透（跨 harness / fail-open / 成本）、界面速览前置、快速开始瘦身；harness 列表三家统一为四家（补 zcode）；中英同步。

## [1.0.0] - 2026-08-27

首个公开版本：一期数据面（三协议透明代理 + VLM 转述 + fail-open）+ 二期控制面（GUI 控制台、对账与自动修复、模型能力实测、识图留痕、四 harness 支持）+ 三平台安装包分发（装完即用零 Python 依赖）。

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
- M1 control plane — `refresh` / `diagnose` verbs, reconcile engine with intent-based auto-repair, tool dossiers, modality probe (tri-state), takeover snapshots, file lock, vision call records, per-harness VLM, tri-state capability store (image terminology).
- M2（GUI）：Tauri 2 + React 控制台（5 页 + 两步向导 + 托盘与关闭确认），新增写动词 models-set/vlm-set/vlm-test/settings-set/relay-set/probe --json/models-fetch，status 总览增强
- 集成/E2E 测试：真实子进程 CLI 契约、跨进程文件锁与原子写、三态轮转、快照 absorb/reclaim、mock HTTP 上游、手动剧本 G1–G8 自动化（`tests/test_integration_*.py`、`tests/test_e2e_g*.py`、`gui/src/core.integration.test.ts`）。
- `status --json` 暴露每个 harness 的 `config_path` 与顶层 `bind_port`；GUI 详情抽屉提供配置文件路径与系统打开入口，总览不再硬编码 8787。
- 事件日志页「导出」：`events --json --limit 0` 拉全量并下载 JSONL。
- `probe --json` 无结论（含糊不下结论）改返回 `ok:true, result:null`（合法三态而非错误）。
- zcode harness 接入：接管/还原/模态门、密钥指纹选路（同名跨协议认家）、zcode-restart 动词与三选弹窗、路由范围勾选（harnesses 白名单）。
- 三平台安装包与发布流水线（Windows NSIS exe / macOS DMG (Apple Silicon) / Linux AppImage+deb，GUI + PyInstaller 冻结核心单包分发，零 Python 依赖；GitHub Actions 手动触发构建，Draft Release 人工验收后发布；Intel Mac 暂不支持——Rosetta 2 只做 Intel→Apple Silicon 方向的转译，无法运行 arm64 包，原生 universal 支持随后版本）。
- 识图留痕留存策略生效：默认 7 天自动清理、可关闭（启动即清 + 每 24h 周期，fail-open，清理量入事件日志）。
- 版本对齐脚本 `scripts/set_version.py`（一次写核心 / tauri.conf / gui package.json 三处）；CI 新增 GUI 门禁 job（vitest + tsc/vite build + cargo check）。

### Fixed
- `settings-set` 接受 `retention_days=0` 会写出下次 `load_config` 必报 ConfigError 的 proxy.json（`VisionLogConfig` 要求 ≥1）；现入口直接拒绝（关闭留存请用 `vision_log.enabled=false`），GUI 留存输入不再把空值强制成 0，设置保存失败会弹出错误。
- 首次向导「完成（过目）」路径不置 `capability_confirmed`：向导会反复弹出；现在任何一次成功的 `models-set`（含空数组跳过）都视为确认完成。
- 僵尸接线自动重启三处缺陷：重启 spawn 原在 `config_lock` 内，与子进程 `start` 的对账互等（锁倒置死锁，`ok` 恒 False）；多 harness 僵尸会重复 spawn；Windows PID 复用 + 残留 pid 文件会让重启子进程误判 "already running" 后退出。现在 spawn 移到锁外、整组只重启一次、重启前清理残留 pid、端口等待窗口 2s→8s。
- 正常 stop 的还原依据统一为最新接管组合快照（运行中吸收过新供应商时不再回跳最早的原始地址）；快照缺失的 harness 退回第一次接管前的整文件备份兜底。
- pid 文件升级为 `{pid, token}`（进程创建时间指纹）：Windows PID 复用不再导致 status 误报“运行中”、stop 误杀无关进程；老格式纯数字文件保持兼容。
- 停用转发的 relay 选路全层不可见（suppressed_relays 收严：坏条目停用后不再被三层选路选中，杜绝全线 502 无法自救）。
- zcode 重启失败给出 UI 错误反馈（M1）；无 baseURL 供应商纳入统计并以「未接线」呈现（M3）；「保留勾选」后复选框回滚到已保存值（M4）；模态门还原清除模型级 `zcode:{}` 空壳（M5）；zcode 探测无目标 reason 文案与实际原因对齐（M7）。
