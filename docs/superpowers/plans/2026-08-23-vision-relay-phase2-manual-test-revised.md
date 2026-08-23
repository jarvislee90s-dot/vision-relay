# vision-relay 二期 手工测试范围（自动化后修订版）· 2026-08-23

> 本文件划分 M2 验收的「已自动化」与「必须人工」范围，取代
> [2026-08-22 手册](2026-08-22-vision-relay-phase2-m2-manual-test.md) 的执行范围划分（原文保留为历史记录）。
> **实际执行请按操作手册（Runbook）逐条走**：[2026-08-23-vision-relay-phase2-manual-test-runbook.md](2026-08-23-vision-relay-phase2-manual-test-runbook.md)
> ——本文负责「为什么这么划分」，Runbook 负责「具体怎么点」。
> 环境：Windows 本机；`vision-relay` 已 pip 安装且在 PATH；CC Switch / Codex++ 至少一个可用；VLM key 可用。

## 一、已自动化（跑门禁即验证，无需人工）

| 原手工项 | 自动化位置 |
|---|---|
| G1 首次向导（两步/跳过/完成/不重跑） | `tests/test_e2e_g1_wizard.py`（后端链路）+ `gui/src/wizard/Wizard.test.tsx`（表单门禁/payload） |
| G2 路由开关（启停/接线/还原全程） | `tests/test_e2e_g2_routing.py` + `gui/src/pages/Overview.test.tsx`（开关调用/横幅/旁路） |
| G3 刷新与抢回 | `tests/test_e2e_g3_reclaim.py` + Overview 测试（自动处理区文案） |
| G4 诊断报告（两种意图） | `tests/test_e2e_g4_diagnose.py` + Overview 测试（弹层两态） |
| G5 模型能力（三态/保存/重测） | `tests/test_e2e_g5_models.py` + `gui/src/pages/Models.test.tsx` |
| G6 VLM 设置与测试 | `tests/test_e2e_g6_vlm.py` + Settings 测试（四模式映射/payload/打码留空规则） |
| G7 提示词编辑 | `tests/test_e2e_g7_prompts.py` |
| G8 识图记录（三段/会话/转发） | `tests/test_e2e_g8_visionlog.py` |
| G12 未标注开关 | `tests/test_e2e_g8_visionlog.py`（核心行为）+ Settings 测试（留存兜底） |
| G10 关闭**决策逻辑**（四种路径/记住选择） | `gui/src/shell/CloseGuard.test.tsx` |
| G11 保存语义（脏计数/payload/放弃/失败提示） | `gui/src/pages/Settings.test.tsx` |
| G13 核心不可用**UI 契约**（红条文案） | `gui/src/App.test.tsx` + `gui/src/core.integration.test.ts`（detectCore 指引） |

## 二、必须人工（含原因）

| 项 | 步骤 | 为什么不能自动化 |
|---|---|---|
| G10 实机：窗口真隐藏到托盘、托盘四项菜单（打开/路由开关/诊断/退出真停服务） | dev 运行 GUI 点托盘逐项 | jsdom 无法模拟系统托盘/真窗口生命周期 |
| G11 实机：切页往返后的视觉脏指示 | 设置页改字段→切总览→切回 | 组件测试已钉行为（见三）；视觉确认留人工 |
| G13 实机：PATH 真移除 vision-relay 启动 GUI→设置页手填路径恢复 | 临时改 PATH 启动 | 真进程发现链路（which_core→PATH 扫描）无法 jsdom 模拟 |
| 决策③实机：「打开」按钮真调起系统默认编辑器 | 总览抽屉点打开 | open_path 走系统 shell（cmd start），jsdom 无法验证 |
| 视觉核对 mockup（布局/措辞/配色/三段明细颜色） | 对照 `docs/superpowers/specs/gui-mockups/index.html` | 视觉回归不在本期范围 |
| M1 手册 A2：真 CC Switch 抢线/吸收（真工具回读行为） | CC Switch 切换供应商→refresh | E2E 用手改文件模拟；真工具的档案回读只有实机能测 |
| M1 手册 A4：真供应商探针三档判定（含吞图模型） | probe 不同模型核对三档 | 需真实 key 与真实模型行为 |
| M1 手册 A5 实机：真 VLM key 发图→识图记录三段 | curl 发带图请求→GUI 看记录 | 数据面已用 mock 全链路验证；真 VLM 返回质量需目验 |

## 三、自动化发现的分歧（裁决记录）

1. **G11「切页保持」**：手册原预期（原文见
   [2026-08-22 手册](2026-08-22-vision-relay-phase2-m2-manual-test.md) 第 20 行）「设置页改多个字段
   不保存直接切页再回来 → 脏计数与字段保持（组件内）」；实现是切页卸载组件、本地 state 丢失
   （`Settings.test.tsx` 重挂用例钉住现状）。**当前按选项①执行（接受现状：切页即丢弃未保存修改，
   交互文案已提示「输入类修改统一由页底保存」）**；如将来要跨页保持，需把草稿状态提升到 App 层
   或用 CSS 隐藏代替卸载（一次小重构）。

2. **Settings 四模式测试的 tier1 默认**：计划原预期「切到 tier1 后 vlm-test 发送 question:null」；
   实现保留 `testQ` state（切换模式不清空「聚焦问题」输入），tier1 仍发送上一次输入的问题。
   **2026-08-23 用户裁决：保持现状**（服务端 tier1 提示词会忽略 question，仅 payload 不洁）。
   用例已转正为钉现状断言（见 `Settings.test.tsx`）。

## 四、M3 打包后另行验收（不属于本轮手工范围）

三平台安装包「装完即用」（MSI/DMG/AppImage 装完→开关路由→诊断报告）、macOS/Linux 实机回归、杀毒误报与体积观察（spec §8 已知代价）。
