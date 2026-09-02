# 2026-09-02 · PR#5「零行为重构」后的回归修复（5 bug + 2 优化）

状态：已实现（分支 `fix/post-refactor-regressions`）。

## 背景

用户在 Windows 实测 v1.0.1 报 5 个 bug + 2 个体验问题，最初怀疑是 wiring/cli/verbs
拆分重构（PR #5，自称零行为）改坏的。系统化排查（新旧分支逐行 diff + 本机运行时
证据 + 复现实验）结论：**重构无辜，全部是存量缺陷**，被 v1.0.x 打包发布与 zcode
迁移逻辑集中暴露。对照基线保留在分支 `pre-refactor-baseline`（=3480b85）。

## Bug 与根因

### 1. 取消勾选 zcode 不生效（qwen 正常；zcode+qwen 同轮取消正常）

- **根因**：`config.py` 旧默认全集迁移用「精确列表相等」判定——单独取消 zcode 存盘
  恰好等于旧默认 3 件套，每次 `load_config()` 都把 zcode 加回。zcode+qwen 同轮存盘
  ≠ 旧默认所以幸存，qwen 单独取消也 ≠ 旧默认所以幸存：三种现象一个根因。
- **修复**：一次性哨兵 `routing.default_harnesses_upgraded`——未置位才升级、升级即
  置位并随下次 save 持久化。主 spec（zcode §8）已同步修订。
- **放大器（一并缓解）**：zcode 被加回后任何对账都会重新接线它；哨兵修复后不再发生。
- **测试**：`test_proxy_config.py::TestZcodeHarnessRegistration`（哨兵置位不升级、
  save→load 回环、显式排除不受影响）。

### 2. 打包版「开启路由」永远起不来（总览一直显示"自动对账中"）

- **根因 A（功能性）**：`_spawn_detached([sys.executable, "-m", "vision_relay", "start"])`
  在 PyInstaller 冻结态下 `sys.executable` 是核心 exe 自身，`-m` 被 argparse 拒绝
  （exit 2、stderr→DEVNULL 静默）→ 孙进程死亡：不写 pid、不接线（配置文件原样）、
  不起服务（proxy.log 零新增）。`reconcile._restart_service` 崩溃自愈同病。源码运行
  （python.exe）不受影响——M3-D/E 真机验收未执行导致漏网。已在用户安装的 v1.0.1
  exe 上实测复现（`vision-relay.exe -m ...` → invalid choice, exit 2）。
- **根因 B（误导性）**：总览地址行的"自动对账中"是无条件写死的装饰文案，服务起不来
  时被读成"卡在对账循环"。
- **修复**：`pid_util.core_argv()` 统一构造重拉 argv（冻结态直接传子命令），
  `cmd_start_detach` 与 `_restart_service` 共用；status 新增 `auto_wire` 字段，地址行
  改显「自动接线已开启/关闭」。**v1.0.1 草稿 release 带此缺陷，修复后需重打包再发**。
- **测试**：`test_proxy_pid_util.py::TestCoreArgv`、`test_proxy_cli.py` detach 冻结态
  argv、`test_proxy_reconcile.py` 自愈重启冻结态 argv、`Overview.test.tsx` 文案两态。

### 3. "direct-claude 直连上游缺 API key" 误报

- **根因**：`direct-*` 直连中继**设计上无自有 key**（认证靠客户端请求头透传，
  server 原样转交 `Authorization`/`x-api-key`），而 GUI `autoNeedsYou` 与对账
  `needs_you` 都把「relay 自身无 key」当「上游缺 key」。真实 token 位置后端本来知道
  （快照 `key_ref`，如 claude 的 `env.ANTHROPIC_AUTH_TOKEN`）但两处判定都没看。
- **修复**：新增 `snapshot.key_ref_resolvable()`（key_ref 非空且不在
  not-found/unknown/unparsable 之列）；GUI 与对账在 key 位置可解析时不再告缺 key，
  key 位置确实缺失时保留提醒。
- **测试**：`test_proxy_reconcile.py` 吸收后 key 位置存在→不误报 / 确实缺失→保留；
  `Overview.test.tsx` 同两态。

### 4. 模型能力把 `*_MODEL_NAME` 展示名当独立模型（8 条目显示 7 个）

- **根因**：`model_sources._CLAUDE_MODEL_KEY` 正则尾部 `(?:_NAME)?` 同时吃掉
  `*_MODEL`（真实模型字段）与 `*_MODEL_NAME`（展示名）；去重是精确字符串比较，只有
  值完全相同的 MiniMax-M3 一对合并，`GLM-5.3[1M]`/`GLM-5.3` 成对全部保留。
- **修复**：正则收紧为 `^ANTHROPIC_(?:DEFAULT_\w+_)?MODEL$`；实测（真实 cc-switch
  库）「火山 Coding Plan」从 7 行收敛到 4 行真实模型。存量 proxy.json 里已成对的
  能力键**不自动清理**（隐藏无害，请求期按真实模型名查键）。
- **附带核查**：直连兜底扫描（onboarding）报出的 `1m`/`1M` 条目经查是 live
  settings.json 里 `*_MODEL` 字段**真实存在的值**（供应商简写），不是正则误抓——
  扫描行为如实，不改。
- **测试**：`test_proxy_model_sources.py`（`_MODEL_NAME` 值不同也不独立成行）。

### 5. 探测完成后「当前标注」不更新

- **根因**：后端探测链路无辜（事件流水 `auto_annotate applied:true` 证明已写入）。
  Models 页 `refreshRows()` 刻意保留未保存 draft，渲染 `effective = draft ?? value`：
  探测+刷新后标注列仍显示草稿旧值，切页重挂载（draft 清空）才可见新值——与实测
  症状（GLM-5.3-Flash 被间隔 57s 重复探测两次）吻合。
- **修复**：探测成功后，**来源非 user** 的行清掉该行 draft（后端标注已被实测覆盖，
  草稿不该盖住新值）；**来源 user** 的行保留 draft（用户意图优先，探测只更新实测列）。
- **测试**：`Models.test.tsx`（非 user 行清、user 行留）。

## 优化项

1. **批量探测可终止**：进行中按钮切「⏹ 终止探测」，置 cancelRef 跳过剩余行（当前行
   跑完），汇总注明「已手动终止，完成 n/N」。单行刷新失败不再中断整批（原
   refreshRows 在逐行 try/catch 之外，一次失败静默放弃后续）。
2. **总览详情 relay 按工具过滤**：后端 relay 行新增 `harness` 归属字段
   （`harness_spec.relay_harness()` 按命名约定判定：direct-\<harness\> / zcode- /
   qwen- / cc-anthropic / cc-codex / codex-plus），抽屉只挂相关 harness 卡片；
   `direct-*` 标记「直连透传」、不再给「停用转发」按钮（停用直连=断路），
   「补填 key」只在 key 位置确实缺失时出现。

## 验收记录

- `python -m pytest -q`：765 passed, 1 skipped。
- `ruff format --check .` / `ruff check .`：全绿。
- GUI `vitest run`：107 passed；`tsc --noEmit` 无错。
- 冻结态 argv：真实 v1.0.1 exe 上 `vision-relay.exe -m vision_relay` → argparse
  exit 2（修复前行为），单测覆盖修复后 argv。

## 残余与后续

- v1.0.1 草稿 release 含 Bug2，**暂缓发布**；本分支合并后重打包（v1.0.2 或重建
  draft）并补 M3-D/E 三平台真机验收（安装→开关路由→诊断）。
- 存量污染数据（`*_MODEL_NAME` 时代写入的能力键、cc-switch 改名后的孤儿供应商组）
  不自动清理，必要时手工整理 proxy.json。
- `_MODEL_NAME` 作为展示标签（配对渲染在模型行上）是后续增强，本期不做。
