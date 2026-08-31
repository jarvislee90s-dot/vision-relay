# vision-relay 设计 — codex 接线漂移检测与重启 + 系统代理回环健康检查

- 日期：2026-08-26
- 状态：设计稿（待评审）
- 关联：`2026-08-26-zcode-harness-design.md`（§7 对账与重启提示、§10 风险）；
  server.py:193 出站回环直连注释（同一劫持机制的出站侧先例）

## 1. 背景与动机

同晚两起真实事故（均已定位复现，见 §1.1），暴露两个盲区：

1. **接线漂移盲区**：`~/.codex/config.toml` 是 vision-relay（接管时写
   `http://127.0.0.1:<bind_port>`）与 codex-plus（providerSync，重启/切档时写
   `http://127.0.0.1:57321/v1`）的共争文件。reconcile 能把磁盘改回来，但
   **codex 进程只在启动时读一次 provider 配置、不热加载**——reconcile 之后，
   已运行的 codex 桌面端仍持旧接线，请求绕过本代理直达 codex-plus → 上游纯文本
   模型拒图（"Model do not support image input"）。磁盘对、运行态错，且无任何
   可见信号。
2. **系统代理回环劫持盲区**：Windows 系统代理开启且 ProxyOverride 不含
   127.0.0.1/localhost 时，走系统代理栈的客户端（Codex 桌面端等）把发往
   `127.0.0.1:8787` 的请求先交给本地代理客户端（实测 Aurora @29290），后者拒绝
   转发回环目标，返回 **502 + 空 body**（客户端显示 "Unknown error"）。本代理
   全程收不到请求，日志零记录，排障成本极高。

### 1.1 事故时间线（2026-08-26 晚，实证）

| 时间 | 事件 |
|---|---|
| 20:41:26 | Aurora（系统代理 @29290）启动，Override 无 localhost |
| 20:45-48 | Codex 桌面端 → 8787 被 Aurora 劫持成 502 空 body；代理日志零记录 |
| 20:58 | vision-relay 接管：config.toml → 8787，目录模态补丁生效 |
| 21:07:27 | codex TUI 启动（读到 8787），此后识图经代理中转正常（injected≥1） |
| 21:20:47 | codex-plus 重启，providerSync 把 config.toml 回写成 57321 |
| 21:21:02 | Codex 桌面端启动（读到 57321），请求全部绕过本代理 |
| 21:25:33 | 桌面端 view_image 回传图片随请求直达 ark → 400 拒图（报错回显） |
| 21:34:10 | reconcile 重新接管（磁盘恢复 8787），但桌面端进程仍持 57321 |
| 21:50 | 用户重启 Codex 桌面端（读到 8787），但又被 Aurora 劫持成 502 |
| ~22:00 | 用户关闭系统代理 → 识图立即可用（全链恢复） |

## 2. 需求清单

- R1 codex 接线漂移可感知：reconcile 改写 codex 配置后，状态面暴露
  "有 codex 进程早于最后一次改写"（needs_restart）。
- R2 codex 桌面端可一键重启：GUI 提示条 + 立即重启按钮，复用 zcode 模式；
  终端 TUI 会话不自动重启（见 §3 决策 D2）。
- R3 系统代理回环劫持可感知：状态面暴露系统代理开关/服务器/回环绕过情况，
  并以**实证探测**（经系统代理访问本代理 /status）判定劫持风险；GUI 出告警卡。
- R4 CLI `vision-relay status` 同步展示上述两项信号。
- R5 全部 best-effort：检测失败/平台不支持 = 静默降级为"无信号"，绝不影响数据面。

## 3. 术语与全局决策

- **D1 复用 zcode 三件套**：codex 检测/重启镜像 `zcode_proc.py` 的
  find → needs_restart → restart 结构与 `zcode.rewrite.json` 改写时间戳模式，
  verbs/GUI 字段与提示条组件一一对应（`codex_runtime` ↔ `zcode_runtime`，
  `codex-restart` ↔ `zcode-restart`）。
- **D2 终端类不自动重启**：codex TUI / claude / qwen-code 同样"启动时读一次、
  不热加载"（事故中 TUI pid 11628 持 21:07 快照即实例），但杀用户终端进程
  不可接受且无法还原其会话上下文。故：检测覆盖 codex 全部进程；自动重启仅限
  桌面端来源进程；TUI 仅靠提示文案。claude/qwen 的进程级检测本期不做（§10）。
- **D3 防误杀红线**：Windows 进程枚举 glob `codex*` 会命中
  `codex-plus-plus.exe` / `codex-plus-plus-manager.exe`（同晚均在运行）。
  过滤规则以 **exe 基名 == `codex.exe`** 为准入，枚举查询一次取齐
  pid/父 pid/启动时间/exe 路径（`Get-CimInstance Win32_Process`，不用
  `Get-Process -Name codex*` 的宽匹配）。
- **D4 时间戳语义**：`needs_restart` 判定为"进程启动时间 < 本代理对 codex 配置的
  最后一次实际写入时间"。无法取到启动时间的进程（权限/竞态）按需重启处理
  （与 zcode 相同的安全侧取向）。
- **D5 系统代理检测以实证探测为准、注册表解析为解释层**：注册表（Win）/scutil
  （mac）/gsettings（Linux）判断"是否启用、是否绕过回环"；劫持风险最终由
  "经该代理访问 `http://127.0.0.1:<bind_port>/status` 是否 200"判定——比
  解析 Override 字符串更可靠（覆盖 PAC/奇异格式）。
- **D6 只读不改**：本代理不代用户改系统代理设置（不写注册表/不出"修复"按钮），
  告警卡只给操作指引。
- **D7 探测开销**：系统代理探测 TTL 10s 缓存（status 轮询 5s，即两轮一探），
  单次超时 2s，仅本机回环一跳。

## 4. 事实描述（现状代码锚点）

- zcode 现成机制：
  - `zcode_proc.py`：`find_zcode_processes`（PS 批量 + 5s TTL 缓存）、
    `zcode_needs_restart(rewrite_ts)`（全部进程 start_ts < rewrite_ts）、
    `restart_zcode()`（taskkill /T /F + 按 exe 分离重启）。
  - `wiring.py:188/203`：`zcode.rewrite.json` 与 `zcode_rewrite_ts()`。
  - `verbs.py:167-171`：status 里 `zcode_runtime:{running,needs_restart}`；
    `verbs.py:493` `zcode_restart` verb（事件 `zcode_restart`）。
  - GUI：`Overview.tsx:45-48` 待重启提示条（testid `zcode-restart-hint`，
    按钮 `core("zcode-restart")`），`useStatus.ts:15` 类型。
- codex 现状：接管/还原写 `~/.codex/config.toml`（wiring），目录模态补丁
  （`_patch_codex_catalog_modalities`）；**无**改写时间戳标记、无进程检测、
  无重启；snapshots.json 有 codex 快照 ts，但其语义属快照机，不复用（D4）。
- codex 进程形态（同晚实测）：桌面端为 Electron 应用 + 其子进程 `codex.exe`
  （Rust core，真正读 config.toml 发请求者）；TUI 为终端内 `codex.exe`。
  两形态同名不同父，父进程名/路径可区分（桌面端父为 Electron 主进程，
  TUI 父为 shell/终端宿主）。
- Windows 系统代理：`HKCU\...\Internet Settings` 的 `ProxyEnable/ProxyServer/
  ProxyOverride/AutoConfigURL`；实测 `ProxyServer=http://127.0.0.1:29290`、
  `ProxyOverride=windows10.microdone.cn`（无回环）时，经其访问 8787 返回
  `502` 且 `Content-Length: 0`。

## 5. codex 接线漂移检测与重启（复用 zcode 模式）

### 5.1 `vision_relay/codex_proc.py`（新模块，镜像 zcode_proc）

- `find_codex_processes(force=False) -> list[dict]`
  - Windows 单次 `Get-CimInstance Win32_Process -Filter "Name='codex.exe'"`，
    输出 `{pid, ppid, start_ts(epoch 秒), exe, origin}`：
    - 过滤：exe 基名 == `codex.exe`（D3；基名缺失的条目丢弃）。
    - origin 分类：exe 路径命中桌面端安装目录特征，或父进程为非终端宿主
      （终端宿主名单：bash/pwsh/powershell/cmd/wezterm/alacritty/
      WindowsTerminal 等）→ `"desktop"`；否则 `"tui"`。实现期在真机校准
      两形态的实际 exe 路径后固化路径规则，父进程规则作兜底。
  - unix：`ps -eo pid,ppid,comm`，同名精确匹配 + 父进程终端名单，best-effort。
  - TTL 5s 缓存（与 zcode 同），`force=True` 供重启前强刷。
- `codex_needs_restart(rewrite_ts) -> bool`：与 `zcode_needs_restart` 同式
  （列表非空且全部 start_ts < rewrite_ts；start_ts 未知按需重启）。
- `restart_codex() -> bool`：
  1. force 枚举；无 desktop 进程 → False（按钮语义为无操作，事件留痕）。
  2. 对每个 desktop 进程树根（Electron 主进程）`taskkill /T /F`（unix `kill`）；
     TUI 进程一律不动。
  3. 按探测到的桌面端 exe 分离重启（DETACHED，镜像 `restart_zcode`）；
     exe 缺失 → 已 kill 但返回 False（事件可区分）。
  - 不做"杀 core 等桌面端自愈"的捷径：桌面端是否自动重生 core 未验证，
    整应用重启语义确定（D1 对齐 zcode）。

### 5.2 改写时间戳：`codex.rewrite.json`

- `wiring.py` 新增 `codex_rewrite_marker_path()` / `codex_rewrite_ts()`，
  形状与 zcode 同（`{"ts": ...}`），在**实际写入** `~/.codex/config.toml` 的
  全部代码点（接管、reconcile 漂移吸收、还原）同步落盘；无写入不更新
  （幂等，保证 D4 时间戳语义）。
- 测试钉死该幂等性（reconcile 无漂移路径不得 touch 标记）。

### 5.3 verbs 与事件

- status 增 `codex_runtime: {running, needs_restart, desktop_running}`
  （`desktop_running` 供 GUI 决定按钮可见性；needs_restart 涵盖 tui+desktop）。
- 新 verb `codex_restart`：调 `restart_codex()`，事件 `codex_restart`
  `{ok}` 入 events.jsonl（镜像 `zcode_restart`）。

## 6. 系统代理回环健康检查

### 6.1 `vision_relay/sysproxy.py`（新模块，stdlib + httpx）

- `detect() -> dict`（TTL 10s 缓存）：
  - Windows：winreg 读 `ProxyEnable/ProxyServer/ProxyOverride/AutoConfigURL`；
  - macOS：`scutil --proxy`（HTTPEnable/HTTPProxy/ExceptionsList）；
  - Linux：`gsettings get org.gnome.system.proxy mode` + `ignore-hosts`；
  - 失败/不支持 → `{supported: False, enabled: False, ...}`（R5 静默降级）。
- `loopback_bypassed(bypass: str) -> bool`：分隔符 `;`/`,`/空白切词，
  大小写不敏感；命中 `<local>`、`127.0.0.1`、`localhost`、`[::1]` 或
  fnmatch 覆盖（如 `127.*`）任一 → True；出现 `<-loopback>`（Chromium 强制
  代理回环标记）强制 False。
- `probe(bind_port: int, server: str) -> str`：仅在 enabled 且未绕过回环时执行；
  `httpx.Client(proxy=f"http://{server}", trust_env=False)` GET
  `http://127.0.0.1:{bind_port}/status`，timeout 2s：
  200 → `"ok"`；非 200 → `"hijacked"`；连接失败/超时 → `"unreachable"`；
  PAC（AutoConfigURL 存在）→ `"skipped"`（不解析 PAC，§8 局限）。
- 组装 `system_proxy: {enabled, server, loopback_bypass, probe, risk}`：
  - `risk="high"`：enabled ∧ ¬bypass ∧ probe=hijacked（实测劫持，即 1.1 事故形态）
  - `risk="warn"`：enabled ∧ ¬bypass ∧ probe∈{skipped, unreachable}
  - 其余（未启用/已绕过/探测 ok）→ `"none"`

### 6.2 呈现

- verbs status 附 `system_proxy` 块；`vision-relay status` 文本输出在
  risk≠none 时加一行告警 + 修复指引。
- GUI Overview 在重启提示条下方新增告警卡（amber=warn / red=high，
  testid `sysproxy-hint`）：文案含服务器地址与指引——"系统代理
  {server} 未绕过本机回环，Codex 桌面端等客户端访问代理会被劫持为 502。
  请在系统代理绕过列表加入 `127.0.0.1;localhost;<local>`，或暂时关闭
  系统代理。"无操作按钮（D6）。
- TUN 模式不影响回环（OS 不把 127/8 引入 TUN），不检测、不告警。

## 7. 风险与残余

| # | 风险 | 缓解 |
|---|---|---|
| 1 | 进程过滤误杀 codex-plus-plus | D3 基名准入 + 单测表驱动（含 codex-plus-plus/manager 反例）+ 验收剧本 A3 |
| 2 | origin 分类误判（桌面端被当 TUI → 不重启） | needs_restart 提示条不依赖 origin；仅按钮可见性依赖；真机校准 + 父进程兜底 |
| 3 | 重启桌面端丢失未保存会话状态 | 与 zcode 立即重启同等语义（用户已接受该模式）；按钮文案明示"立即重启" |
| 4 | rewrite 标记与实际写入失步（漏写点多） | 单测钉死全部写入点（接管/还原/reconcile 吸收）都更新标记 |
| 5 | PAC 场景探测 skipped（检测不全） | 降级 warn + 文案指引；明确不做 PAC 解析 |
| 6 | 探测经系统代理的副作用（代理记一条 127.0.0.1:8787 请求） | 只读 GET /status、TTL 10s，可接受 |
| 7 | Win32_Process 查询被安全软件盯上 | 仅本机 WMI 只读查询，与现有 PowerShell 枚举同级 |

## 8. 测试与验收

单测（pytest，匹配 `tests/test_proxy_*.py` 命名）：
- codex_proc：过滤表驱动（codex.exe 通过；codex-plus-plus* 拒绝；基名缺失拒绝）；
  needs_restart 时间比较与 ts=0 安全侧；origin 分类（路径规则/父进程兜底）；
  restart 仅触达 desktop 树（taskkill 参数断言）、TUI 不被杀。
- sysproxy：loopback_bypassed 表驱动（含 `<local>`/通配/`<-loopback>`）；
  detect 的 winreg monkeypatch；probe 的 httpx monkeypatch（200/502/超时/PAC）；
  risk 矩阵全覆盖。
- wiring：codex.rewrite.json 写入点与幂等性。
- verbs：status 新字段形状；codex_restart 事件留痕。

真机验收剧本（对照 1.1 事故）：
- A1 漂移→告警→自愈：接管后手动把 config.toml base_url 改 57321 → 等
  reconcile 吸收 → Overview 出 codex 待重启条 → 点立即重启 → codex 桌面端
  回归 8787（proxy.log 出现 responses 记录，识图 injected≥1）。
- A2 劫持→告警→消除：开 Aurora 系统代理 → status risk=high、告警卡出现 →
  绕过列表加 `127.0.0.1;localhost;<local>` → risk 转 none、卡片消失。
- A3 误杀红线：全程 codex-plus-plus / manager 存活，点击立即重启后仍存活。
- A4 TUI 只提示不代杀：终端内 codex 会话在立即重启后继续存活，提示条文案含
  "终端会话请手动重启"。

## 9. 明确不做（本期范围外）

- claude / qwen-code 的进程级 needs_restart 检测（CLI 会话短生命周期、进程名
  不可靠；磁盘层 reconcile 收敛已覆盖，新会话自然生效）。
- PAC（AutoConfigURL）解析与经 PAC 的劫持探测。
- 代理代改系统代理设置（写注册表/调 Aurora）——只检测、只指引（D6）。
- "杀 codex core 等桌面端自愈"的轻量重启捷径（未验证，语义不确定）。
- macOS/Linux 的系统代理探测仅 best-effort 桩，验收以 Windows 为准。

## 10. 决策记录（2026-08-26 与用户逐条确认）

- 复用 zcode 三件套（find/needs_restart/restart + rewrite 标记 + verbs/GUI
  对称扩展）——用户提问确认方向，本设计落实并补 D3 防误杀细节。
- "不热加载的只有 codex APP 和 zcode、终端不涉及"——**修正**：终端 CLI 同样
  不热加载（TUI 持旧快照有实证），但产品决策为终端只提示、不自动重启（D2）；
  claude/qwen 检测不做（§9）。
- 系统代理检查定位为"可观测性"（检测+指引），不越权代改（D6）。
