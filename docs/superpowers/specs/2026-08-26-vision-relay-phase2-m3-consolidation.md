# vision-relay 二期 M3（监听 + 打包）事项汇总

> **性质:** 本文件是把散落在各 spec / plan 文档中的 M3 相关描述与待办事项**汇总**成的一份总览，不新增决策、不改动任何既有文档。M3 尚无独立细化设计，所有条目均为既有文档中「归 M3 / 待办」的原始表述。
> **来源:** 逐条标注出处（文件 + 行号），以 `docs/superpowers/specs/2026-08-21-vision-relay-phase2-control-plane-design.md` 为上游基准；冲突时以 spec 为准。
> **回写（2026-08-31）:** 按仓库现状核对各项进度并就地勾选（证据注记在各条目下）；本文件自 `docs/` 移入本目录，作为 M3 进度跟踪文档。核对基准：`.github/workflows/release.yml`、`gui/src-tauri/tauri.conf.json`、`vision_relay/server.py`、`vision_relay/visionlog.py`。

---

## 一、M3 里程碑定义（唯一权威出处）

**出处:** `docs/superpowers/specs/2026-08-21-vision-relay-phase2-control-plane-design.md` §11 排期里程碑表（L239）

| 里程碑 | 内容 | 交付判据 |
|---|---|---|
| **M3 监听 + 打包** | 自动对账（可开关）、留存策略生效、三平台安装包 CI、一体化发布 | **单安装包三平台「装完即用」验收** |

M1 = 命令行层面刷新/诊断/自动修复/探测全部可用；M2 = GUI 端到端剧本 1–8 手工跑通。**M3 是二期收尾里程碑。**

---

## 二、M3 范围内的事项（待办清单）

### A. 自动监听与对账（自动对账，可开关）

- 出处: spec §11（M3 行「自动对账（可开关）」）；spec §2 R2（「先做手动刷新，后做自动监听」）；spec §3 总体架构 `WATCH["周期/事件触发的自动对账（后期）"]`（L56）；spec §监听分阶段（L155: 「后自动监听（服务内文件通知 + 周期对账，可开关、默认开）」）。
- 待办分解:
  - [ ] 服务内文件通知监听（配置文件被工具/用户改走 → 感知）
  - [ ] 周期对账（设置页已有「自动对账开·5 秒」默认值；M3 让周期触发真正生效）
  - [ ] 自动监听开关 UI 化（**M2 已明确不做**: M1 后端已具备，M2 GUI 只展示状态；开关 UI 归 M3/后期 —— `docs/superpowers/plans/2026-08-22-vision-relay-phase2-m2-gui.md` L14）
  - [ ] 对账触发统一走既有 reconcile 引擎（spec §4: 所有触发源——GUI 刷新按钮、自动监听、启停、诊断修复——走同一套规则）

  > 回写（2026-08-31）现状：对账引擎 `vision_relay/reconcile.py` 与 GUI 设置页默认值已在；server 侧仅有 30s provider 缓存刷新线程（`server.py` `_start_provider_cache_refresher`，能力判定用，非对账）——周期对账与文件监听**仍未落地**，以上维持待办。

### B. 留存策略生效

- 出处: spec §11（M3 行「留存策略生效」）；spec §6 识图留痕（L191: 本地留存默认 7 天、可关闭）；spec § 设置页（「识图留痕 7 天·仅本机」）。
- 待办:
  - [x] 识图留痕的留存策略（默认 7 天 / 可关闭）真正生效并定期清理
    （回写 2026-08-31 已落地：`vision_relay/config.py` `retention_days=7`；`vision_relay/visionlog.py` `cleanup()`；`vision_relay/server.py` `_start_retention_worker` 启动即清一次 + 每 24h 周期，清理量记 `visionlog_cleanup` 事件，fail-open；关闭走 `vision_log.enabled=false`）

### C. 打包分发（三平台安装包 + CI + 一体化发布）

- 出处: spec §11（M3 行「三平台安装包 CI、一体化发布」）；spec §2 R7（**硬约束**:「单一安装包分发：装完即用，零 Python 环境依赖（Windows MSI/EXE、macOS DMG、Linux AppImage/deb）」）；spec § 优先级 P0（L207: 「单包分发 | 硬约束（R7） | 决定技术路径（冻结打包 + sidecar）」）；`tauri.conf.json` 当前 `"bundle": { "active": false }`。
- 待办:
  - [x] 冻结打包（Python 核心随包分发）—— CI 以 PyInstaller onedir 统一三平台（release.yml「Freeze core (PyInstaller onedir)」，入口 `scripts/freeze_entry.py`，冻结产物含 `--version` smoke 校验）
  - [x] sidecar 分发（GUI 壳与核心的打包方式）—— 冻结产物置入 `gui/src-tauri/resources/core/`，`tauri.conf.json` `"resources": { "resources/core": "core/" }`
  - [x] 三平台 CI（MSI/EXE、DMG、AppImage/deb）—— `.github/workflows/release.yml` matrix：Windows NSIS(EXE) / macOS DMG(arm64) / Linux AppImage+deb(x64)（实际为 NSIS 而非 MSI）
  - [x] 一体化发布（GUI 与核心同版本发布）—— release.yml 手动触发（`scripts/set_version.py` 版本注入）+ 发布正文人工文案优先（`docs/release-notes/v<版本>.md`）；v1.0.0 已于 2026-08-27 发布
  - [x] M2 阶段 GUI 需要 PATH 上有 `vision-relay` 命令；打包后不再依赖 pip 安装（m2-gui.md L2169「打包随 M3」）—— 安装包内含冻结核心，装完即用

### D. macOS / Linux 实机回归（M3 打包后补）

- 出处: m2-gui.md L14（「macOS/Linux 实机回归（代码跨平台，实机验证 M3 打包后补）」）、L2183（手工测试环境说明）、`2026-08-22-vision-relay-phase2-m2-manual-test.md` L6（「macOS/Linux 实机回归留 M3 打包后」）。
- 待办:
  - [ ] macOS 实机回归（DMG 装完 → 开关路由 → 诊断报告）
  - [ ] Linux 实机回归（AppImage/deb 装完 → 开关路由 → 诊断报告）
  - [ ] Windows MSI/EXE 安装包回归

  > 回写（2026-08-31）现状：v1.0.0 三平台产物已由 CI 发布（win NSIS / macos dmg / linux appimage+deb），但实机回归记录暂未见于仓库，维持待办。

### E. 安装包交付验收（另行验收，不在 M2 手工测试范围）

- 出处: `docs/superpowers/plans/2026-08-23-vision-relay-phase2-manual-test-revised.md` §四（L53 起）与 `docs/superpowers/plans/2026-08-23-vision-relay-phase2-manual-to-auto.md` §四（L860 起，内容同源）:

> 三平台安装包「装完即用」（MSI/DMG/AppImage 装完→开关路由→诊断报告）、macOS/Linux 实机回归、杀毒误报与体积观察（spec §8 已知代价）。

- 待办:
  - [ ] 三平台安装包「装完即用」验收（装 → 开关路由 → 诊断报告）
  - [ ] 杀毒误报与安装包体积观察（详见下文「已知代价」）

### F. M3 前置门禁（进入 M3 的条件）

- 出处: `docs/superpowers/plans/2026-08-23-vision-relay-phase2-manual-test-runbook.md` L268: 「R1–R7 任何一条失败都应记录后停下，不要带病进入 M3。」
- 含义: M2 手工测试的 R1–R7（前置运行结果类检查）全部通过后，才能进入 M3。

---

## 三、已知代价与明确不做（与 M3 相关的边界）

### 已知代价（记档，不阻断，来源 spec § 已知代价 L199）

- 安装包体积约 50–80MB
- 冻结程序偶发杀毒软件误报（缓解手段在实现计划中评估）
- 暂不做签名 / 公证

### P2（下期/按需，非 M3 必交，来源 spec § 优先级 L216）

- 签名 / 公证、安装包体积优化、状态推送流

### 明确不做（三期/范围外，来源 spec §12「明确不做（本期范围外 → 三期）」L243 起）

- 多 harness 拦截 / 注入 / 调度平台（IR 层 transformer 链——本 spec 只留插槽方向）
- 新 harness 接入（OpenCode、DSH、TRAE、Kimi）、DSH 准入声明
- 磁盘缓存、后台深缓存、跨协议流式、Claude Code hooks、上游转发重试、请求级总超时
- relay 多供应商轮转、服务端部署、签名 / 公证（注: 签名/公证也列在「P2 下期/按需」中）
- 用户自定义回退档案
- 视频 / 音频模态探测（本期只做 image）

### 其他明确「不做/不动」（来源 `docs/superpowers/plans/2026-08-23-vision-relay-phase2-review-decisions.md` L16）

- stop 不撤销 absorb 产生的 `direct-*` relay（用户供应商定义，非运行时接线）
- 配置文件不做行号定位
- 语言切换保持即时生效
- **M3 事项（打包 / 自动监听）在 review 落地计划中不做** —— 即 review 计划只到 M2，M3 另行启动

---

## 四、M3 相关配置/状态的现状（M2 已具备、M3 需落地生效）

| 配置项 | 默认值 | 出处 | 状态 |
|---|---|---|---|
| 自动对账 | 开·5 秒 | spec § 设置页（L178） | M2 后端已具备；周期触发与开关 UI 归 M3（2026-08-31 核对仍未落地） |
| 识图留痕 | 7 天·仅本机 | spec § 设置页（L178）、spec §6（L191） | **已生效**（M3-B 完成：启动即清 + 24h 周期，见 §二B 回写注记） |
| 未标注模型默认 | 走识图（安全）| spec § 设置页 | 已定（无需 M3 处理） |
| GUI 打包 | `bundle.active=true`（targets: all） | `gui/src-tauri/tauri.conf.json` | **已打开**（M3-C 完成，见 §二C 回写注记） |

---

## 五、M3 落地时需要新增/细化的东西（参考）

M3 尚无独立细化设计，后续若启动 M3，建议在此基础上补齐:

- 自动监听的架构细化: 文件通知 vs 周期轮询的取舍、监听对象（三个 harness 配置文件）、事件驱动路径
- 打包技术路径: 冻结打包（PyInstaller?）+ sidecar 分发、三平台 CI 流水线设计（✅ 已按 PyInstaller onedir + Tauri resources sidecar + release.yml matrix 落地，见 §二C）
- 一体化发布流程: 版本对齐、发布产物、CHANGELOG（✅ 已落地: release.yml + 人工文案优先的 release notes）
- 留存策略的实现细节: 清理时机、目录结构、配置项透出（✅ 已落地，见 §二B）
- M3 手工/验收手册（对标 M2 手册的格式）（仍待补，§二D 实机回归依赖它）

---

## 六、来源索引（全部出处一览）

| 文档 | 位置 | 内容 |
|---|---|---|
| `docs/superpowers/specs/2026-08-21-vision-relay-phase2-control-plane-design.md` | L239 | M3 里程碑定义（权威） |
| 同 spec | L26（R2）、L55、L155 | 自动监听的分阶段计划 |
| 同 spec | L191、L178 | 留存策略与设置项 |
| 同 spec | L207、L216 | 单包分发 P0 / 签名·体积 P2 |
| 同 spec | L199 | 已知代价 |
| 同 spec | L243 起 | 明确不做（三期） |
| `docs/superpowers/plans/2026-08-22-vision-relay-phase2-m2-gui.md` | L14 | M2 不做（打包/CI、自动监听 UI、sidecar、自动更新、实机回归）→ M3 |
| 同 m2-gui | L2169、L2183 | GUI 打包随 M3；环境要求 |
| `docs/superpowers/plans/2026-08-22-vision-relay-phase2-m1-core.md` | L13 | M1 不做（周期自动监听、安装包）→ M3 |
| `docs/superpowers/plans/2026-08-23-vision-relay-phase2-manual-test-revised.md` | L53 起 | M3 打包后另行验收 |
| `docs/superpowers/plans/2026-08-23-vision-relay-phase2-manual-to-auto.md` | L860 起 | 同上（内容同源） |
| `docs/superpowers/plans/2026-08-23-vision-relay-phase2-review-decisions.md` | L16 | M3 事项不在 review 落地范围内 |
| `docs/superpowers/plans/2026-08-22-vision-relay-phase2-m2-manual-test.md` | L6 | macOS/Linux 实机回归留 M3 |
| `docs/superpowers/plans/2026-08-23-vision-relay-phase2-manual-test-runbook.md` | L268 | 不「带病进入 M3」门禁 |

> 注意: `docs/superpowers/plans/2026-08-24-models-scan-provider-matrix.md` 中的 "MiniMax-M3" 是模型名，不是里程碑，勿混淆。
