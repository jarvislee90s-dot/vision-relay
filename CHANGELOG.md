# Changelog

All notable changes to vision-relay will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
