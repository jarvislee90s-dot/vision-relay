# REFACTOR NOTES — vision-relay wiring 模块重构

> 任务：在**零外部可观察行为变更**前提下，把 `vision_relay/wiring.py`（原 1297 行单体）
> 拆分为职责单一、可独立测试的模块，补强守护测试，交付可重复的端到端冒烟脚本。
>
> 基线分支：`main` ｜ 日期：2026-08-31

---

## 0. 结果速览

| 维度 | 指标 | 结果 |
|---|---|---|
| A1 | `pytest` 全绿 | **578 passed, 1 skipped**（基线 561 passed, 1 skipped；+17 新测试） |
| A2 | `git diff main --stat -- tests/` 仅新增 | 2 个新文件 / 仅 insertions / **0 deletions**，既有断言零改动 |
| A3 | 沙箱 HOME 端到端冒烟 | `tests/smoke/smoke_wiring.py` 三场景全过、免交互、可重复 |
| B1 | `wiring.py` ≤ 300 行 | **186 行**（原 1297 行，-86%） |
| B2 | 新模块数 4~8 | **8 个**，各一句话职责 docstring |
| B3 | 拆分模块单文件 ≤ 500 行 | 最大 `zcode_providers.py` 364 行 |
| B4 | 包内顶层导入无环 | 见 §6 导入图（DAG，无环） |
| C1 | 三条点名路径守护测试 | legacy 迁移 / zcode 条目级 / 部分失败回滚时序，全部覆盖 |
| C2 | 自选 ≥3 未覆盖路径 | 4 条，理由见 §4.2 |
| C3 | 覆盖率不低于基线 | wiring 相关 84.9%→**88.5%**；整包 85.0%→**85.7%** |
| D1 | 旧→新映射 100% | 60 个符号 + HOME 全部映射，见 §3 |
| E1 | 每新模块独立提交 | 10 个语义化提交，见 §8 |
| E2 | ruff + 测试全绿 | `ruff check .` All checks passed；575 passed |
| E3 | 零新增运行时依赖 | `pyproject.toml` dependencies 未改（仍仅 `httpx`） |
| F1 | docstring 漂移清零 | "三处 harness"→四处（wiring/config/cli），README 本已正确 |

**验证命令（仓库根、激活 `.venv` 后）：**

```bash
ruff check .                                   # E2
python -m pytest -q                            # A1 / E2
python -m pytest --cov=vision_relay --cov-report=term-missing   # C3
python tests/smoke/smoke_wiring.py             # A3
```

---

## 1. 拆分后的模块布局（B2 / D2）

| 新模块 | 行数 | 一句话职责（docstring 原文摘要） |
|---|---:|---|
| `harness_spec.py` | 80 | 四种 harness 的配置表、备份后缀、base_url 归属判定与 home→路径解析（无副作用、不读 HOME） |
| `modalities.py` | 50 | 跨 qwen/zcode/codex 三种配置形态共用的 image 准入门原语 |
| `harness_io.py` | 191 | 四种格式 base_url 读写、原子 JSON 落盘、模型名抽取、codex 目录补丁 |
| `qwen_providers.py` | 300 | qwen modelProviders 条目级改写/还原/relay 维护/统计/对账 |
| `zcode_providers.py` | 364 | zcode v2 `provider.<id>.options.baseURL` 条目级接线 + 改写时间戳 |
| `relays.py` | 92 | routing 模板激活/还原与在线工具档案自动 relay 增删（不碰 harness 配置） |
| `wiring_status.py` | 45 | 四处 harness base_url 归属与条目级统计的只读报告（无副作用） |
| `wiring_orchestrate.py` | 297 | start/stop 编排与回滚：备份→改写→快照，按快照或 .bak 还原 |
| `wiring.py`（facade） | 186 | 持有测试可 monkeypatch 的 HOME、重导出公共 API、为 home 依赖入口注入 HOME |

> 每个模块顶部均有一句话职责 docstring（D2）。`wiring.py` 保留为**薄组装层**（B1 明确允许），
> 它持有 `HOME`（测试 monkeypatch 隔离点）并以 `__all__` 重导出子模块公共面，
> 外部调用面（`wiring.read_base_url` / `wiring.wiring_backup_and_rewrite` / …）完全不变。

### 关键设计决策：HOME 注入而非跨模块读取

测试以 `monkeypatch.setattr(wiring, "HOME", …)` / `wiring.HOME = …` 重绑定 **wiring 模块**的
`HOME`。若子模块函数直接读各自的全局 `HOME`，monkeypatch `wiring.HOME` 不会传播到子模块。
为在不改变测试、不引入导入环的前提下保持该隔离语义：

- 子模块中**所有需要 home 的入口函数**改为接收显式 `home: str` 参数（依赖注入，纯函数化，更易测）；
- `wiring.py` facade 为这些入口提供**薄包装**，在调用时把 `wiring.HOME` 注入：
  ```python
  def wiring_backup_and_rewrite(cfg):
      return wiring_orchestrate.backup_and_rewrite(cfg, HOME)
  ```
- 因此 `monkeypatch wiring.HOME` 立即对全部入口生效（包装在调用时读 facade 的全局 `HOME`）。

`cli.py` / `reconcile.py` / `verbs.py` / `model_sources.py` / `onboarding.py` 等既有调用方
仍以 `wiring.<func>(...)` 调用，签名不变；它们对 `wiring.HOME` 的直接读取（如
`wiring._path(wiring.HOME, name)`）也不受影响。`cli.py` 原本的**函数内延迟 import**
（`from .wiring import …` 写在 cmd_start/cmd_stop 内）天然兼容 facade 重导出与测试
monkeypatch，未改动。

---

## 2. 行为保持论证（A1 / 约束 1）

- **逐函数搬运**：每个函数体在新模块中**逐字保留**（仅调整 import 来源与 `HOME`→`home` 参数），
  控制流、字符串消息、备份命名（`.vision-relay.bak` + legacy `.qwen-mm-proxy.bak`）、
  快照合并/守卫/回滚时序、日志与 visionlog 语义全部不变。
- **公共 API 不变**：`wiring.` 命名空间下原 60 个符号 + `HOME` 全部可达（§3 映射表，已用脚本
  逐条 `hasattr` 校验：`missing from wiring facade: NONE`）。
- **既有测试零改动**（A2）：`git diff main --stat -- tests/` 仅 2 个新文件，0 删除。
- **回归**：561 既有测试全绿，与基线一致；新增 14 例守护测试亦全绿。

---

## 3. 旧 → 新 函数/类/常量映射表（D1，100%）

> 覆盖原 `wiring.py` 全部 53 个函数 + 1 个 dataclass + 8 个常量 = 62 个顶层符号。
> 「facade 包装」表示该符号在 `wiring.py` 中是注入 HOME 的薄包装，真实实现位于右列模块。

### 常量 / 类

| 原符号 | 新位置 | 说明 |
|---|---|---|
| `BAK_SUFFIX` | `harness_spec` | 重导出 |
| `LEGACY_BAK_SUFFIX` | `harness_spec` | 重导出 |
| `_ZCODE_PROTO` / `_ZCODE_RELAY_PREFIX` | `harness_spec` | 重导出 |
| `_QWEN_AUTH_PROTO` / `_QWEN_RELAY_PREFIX` | `harness_spec` | 重导出 |
| `_MOD_ABSENT` | `harness_spec`（modalities 等导入） | 重导出 |
| `_Harness`（dataclass） | `harness_spec` | 重导出；`rel_path` 注解由 `str` 收紧为 `str \| tuple`（纯类型注解，frozen dataclass 无运行时影响） |
| `HOME` | `wiring`（facade 保留） | 测试 monkeypatch 隔离点，唯一留在 facade 的可变状态 |

### 函数

| 原函数 | 新位置 | 搬运理由 |
|---|---|---|
| `_path` | `harness_spec` | 纯路径解析，规格层 |
| `_find_bak` | `harness_spec` | 备份后缀回退逻辑，规格层 |
| `classify_base_url` | `harness_spec` | base_url 归属判定（ours/工具/other/none），被 qwen/zcode/orchestrate 共用 |
| `_modalities_open` | `modalities` | qwen 准入门原语 |
| `_open_modalities` | `modalities` | qwen 准入门代开 |
| `_mod_input` | `modalities` | zcode modalities.input 原语 |
| `_ensure_image` | `modalities` | 补 image 原语，zcode 与 codex 目录共用 |
| `_json_save_atomic` | `harness_io` | 原子 JSON 落盘，qwen/zcode/codex 共用 |
| `read_base_url` | `harness_io` | 四格式 base_url 读取 |
| `write_base_url` | `harness_io` | 四格式 base_url 写入 |
| `_first_model` | `harness_io` | 模型名抽取（快照用） |
| `_codex_catalog_path` | `harness_io` | codex 目录路径解析 |
| `_patch_codex_catalog_modalities` | `harness_io` | codex 目录 image 模态补丁 |
| `_restore_codex_catalog` | `harness_io` | codex 目录补丁对称还原 |
| `_qwen_provider_items_from` | `qwen_providers` | qwen 条目收集（dict 版） |
| `_qwen_provider_items` | `qwen_providers` | qwen 条目收集（文件版） |
| `_qwen_entry_keys` | `qwen_providers` | qwen 改写侧快照键 |
| `_qwen_resolve_key` | `qwen_providers` | qwen 还原侧键解析 |
| `_rewrite_qwen_providers` | `qwen_providers` | qwen 条目改写 |
| `_restore_qwen_providers` | `qwen_providers` | qwen 条目还原 |
| `_qwen_relay_name` | `qwen_providers` | qwen relay 命名 |
| `_qwen_relay_groups` | `qwen_providers` | qwen relay 分组 |
| `ensure_qwen_relays` | `qwen_providers`（+`home`） | facade 包装 |
| `reconcile_qwen_providers` | `qwen_providers`（+`home`） | facade 包装 |
| `_qwen_provider_stats` | `qwen_providers` | qwen 条目统计 |
| `_zcode_marker_path` | `zcode_providers` | 改写时间戳路径 |
| `_mark_zcode_rewrite` | `zcode_providers` | 记录改写时间 |
| `zcode_rewrite_ts` | `zcode_providers` | 读取改写时间 |
| `_zcode_key` | `zcode_providers` | zcode 身份键 |
| `_zcode_entries` | `zcode_providers` | zcode 可接管条目收集 |
| `_rewrite_zcode_providers` | `zcode_providers` | zcode 条目改写 |
| `_restore_zcode_providers` | `zcode_providers` | zcode 条目还原 |
| `_zcode_slug` | `zcode_providers` | zcode relay slug |
| `_zcode_relay_desired` | `zcode_providers` | zcode 期望 relay 列表 |
| `_is_zcode_relay` | `zcode_providers` | zcode 自动条目判定 |
| `ensure_zcode_relays` | `zcode_providers`（+`home`） | facade 包装 |
| `remove_zcode_relays` | `zcode_providers` | 不读 home，直接重导出 |
| `reconcile_zcode_providers` | `zcode_providers`（+`home`） | facade 包装 |
| `_zcode_provider_gated` | `zcode_providers` | zcode 门已开判定 |
| `_zcode_provider_stats` | `zcode_providers` | zcode 条目统计 |
| `relays_activate` | `relays` | 模板激活，不读 home |
| `relays_restore` | `relays` | 模板/前缀还原，不读 home |
| `ensure_tool_relays` | `relays` | 工具档案自动 relay，不读 home |
| `_relay_name` | `relays` | relay 命名 |
| `wiring_backup_and_rewrite` | `wiring_orchestrate.backup_and_rewrite`（+`home`） | facade 包装保留原名 |
| `wiring_restore` | `wiring_orchestrate.restore`（+`home`） | facade 包装保留原名 |
| `wiring_report` | `wiring_status.report`（+`home`） | facade 包装保留原名；自审把只读查询移出编排模块（§11） |
| `wiring_restore_by_snapshot` | `wiring_orchestrate.restore_by_snapshot`（+`home`） | facade 包装保留原名 |
| `wiring_restore_harness` | `wiring_orchestrate.restore_harness`（+`home`） | facade 包装保留原名 |
| `wiring_restore_on_stop` | `wiring_orchestrate.restore_on_stop`（+`home`） | facade 包装保留原名 |
| `_restore_harness_on_stop` | `wiring_orchestrate`（+`home`） | 内部函数，重导出 |
| `_generic_snapshot_or_bak_restore` | `wiring_orchestrate` | 不读 home（接 `p` 路径参数），重导出 |

> 校验脚本：对比 `git show main:vision_relay/wiring.py` 的全部顶层 `def/class/常量` 与
> `hasattr(vision_relay.wiring, …)`——60 个符号（除 HOME）全部可达，6 个 `wiring_*`
> 入口为 facade 包装（实现在 `wiring_orchestrate` 的改名函数）。

---

## 4. 守护测试索引（C1 / C2）

新文件：`tests/test_proxy_wiring_guard.py`（14 例，纯新增）。沿用仓库 HOME monkeypatch
隔离纪律（`wiring.HOME` + `snapshot.HOME` + `VISION_RELAY_CONFIG_DIR` 全指沙箱）。

### 4.1 C1 三条点名路径

| 点名路径 | 测试 | 守护的行为 |
|---|---|---|
| **① legacy `.qwen-mm-proxy.bak` 迁移** | `TestLegacyBakMigration::*`（5 例） | `_find_bak` 新后缀优先/legacy 回退/皆无返 None；`wiring_backup_and_rewrite` 不覆盖既有 legacy 备份、不另建新备份；`wiring_restore_on_stop` 无快照时消费 legacy `.bak` 整文件还原并清理；非本代理指向时保留备份不动 |
| **② zcode 条目级 `provider.<id>.options.baseURL` 读取与还原** | `TestZcodeEntryLevel::*`（3 例） | `read_base_url` zcode-v2 返回 enabled 供应商地址、跳过 disabled 与无 options 畸形条目；`_rewrite_zcode_providers`/`_restore_zcode_providers` 条目级 baseURL + 模态门往返一致；用户把某条目改走别处时还原守卫只动仍指本代理的条目 |
| **③ 部分接线失败时 stop 的回滚时序** | `TestPartialFailureRollback::*`（2 例） | 注入 `snapshot.save` 抛错：备份先于改写已落盘、接管不被打断、stop 退回 `.bak` 整文件兜底还原（时序：备份→改写→快照失败→stop .bak 恢复）；多 harness 部分失败时各自独立回滚（被用户改走的跳过、其余照常） |

### 4.2 C2 自选未覆盖路径（≥3，理由）

| 自选路径 | 测试 | 选择理由 |
|---|---|---|
| env 格式（`KEY=VALUE`）读写 | `TestHarnessIoFormats::test_env_format_read_write_roundtrip` | 基线 wiring.py 覆盖报告中 env 读分支（原 661-665）与写分支（原 685-695）均未覆盖；当前四种 harness 未用 env 种类，但 `read_base_url`/`write_base_url` 保留该分支，属易回归的防御路径 |
| toml 无 `base_url` 时 append | `TestHarnessIoFormats::test_toml_write_appends_when_no_base_url` | 基线原 701 行 append 分支未覆盖（既有 toml 测试都带 base_url）；新增/损坏配置首次接线走此分支 |
| `ensure_tool_relays` 尊重 `suppressed_relays` | `TestRelayMaintenance::test_ensure_tool_relays_respects_suppressed` | 原 1180-1183 压制名单分支未覆盖；用户显式停用须优先于自动探测，是 spec §7.5 关键守卫 |
| `relays_restore` 按前缀移除一层 relay | `TestRelayMaintenance::test_relays_restore_removes_prefixed_one_hop_relays` | 原 948-950 qwen-/zcode- 前缀识别移除未覆盖；stop 时一层直连 relay 须清理，残留会继续参与选路 |

### 4.3 既有测试守护的行为（未改动，回归保证）

- `tests/test_proxy_wiring.py`：归属分类、接管写快照、快照还原、快照失败不打断接管、
  工具 relay、stop 快照优先/.bak 兜底、codex 目录补丁与还原。
- `tests/test_proxy_zcode.py` / `tests/test_proxy_qwen_providers.py`：条目级改写/还原、
  relay 维护、对账吸收、指纹随行。
- `tests/test_proxy_reconcile.py` / `test_proxy_routing.py` / `test_proxy_model_sources.py` /
  `test_proxy_verbs.py` / `test_proxy_cli.py`：跨模块调用面（`wiring.*`）回归。

---

## 5. 覆盖率前后对比（C3）

报告留档：`refactor/coverage-baseline.txt`、`refactor/coverage-after.txt`。

**wiring 相关代码（原单体 → 8 个拆分模块 + facade 合计）：**

| | Stmts | Miss | Cover |
|---|---:|---:|---:|
| 基线 `wiring.py` | 940 | 142 | **84.9%** |
| 重构后 9 文件合计 | 999 | 115 | **88.5%** |

**整包 `vision_relay/`：**

| | Stmts | Miss | Cover |
|---|---:|---:|---:|
| 基线 | 4589 | 690 | **85.0%** |
| 重构后 | 4648 | 664 | **85.7%** |

> 覆盖率**上升**（wiring 相关 +3.6pt，整包 +0.7pt），未低于基线。新模块中 facade `wiring.py`
> 与 `wiring_status.py` 达 100%；`harness_spec` 97.6%、`zcode_providers` 91.9%、`modalities` 92%、
> `relays` 90.3%、`qwen_providers` 87.6%、`wiring_orchestrate` 84.2%、`harness_io` 81.3%。
> 未覆盖行主要为各 `OSError`/`JSONDecodeError` 静默兜底与极端畸形输入分支（与基线同性质）。

---

## 6. 导入图（B4：包内顶层导入无环）

脚本（AST 解析相对导入，DFS 检测）输出：`NO TOP-LEVEL IMPORT CYCLES in vision_relay/`。
wiring 子图（边 = "导入"）：

```
harness_spec      -> tools
modalities        -> harness_spec
harness_io        -> harness_spec, modalities
qwen_providers    -> config, harness_io, harness_spec, modalities, snapshot, tools
zcode_providers   -> config, env_util, fingerprint, harness_io, harness_spec, modalities, snapshot, tools
relays            -> config, harness_spec, tools
wiring_status     -> harness_io, harness_spec, qwen_providers, zcode_providers
wiring_orchestrate-> harness_io, harness_spec, qwen_providers, snapshot, tools, zcode_providers
wiring (facade)   -> harness_io, harness_spec, modalities, qwen_providers, relays,
                     wiring_orchestrate, wiring_status, zcode_providers
```

这是一个 DAG：`tools/config/snapshot/fingerprint/env_util` 为叶子（不回链 wiring 子树），
`harness_spec` 为子树根，`wiring` facade 为唯一汇点。**无任何子模块 import wiring**
（已 grep 校验），故 facade ↔ 子模块无环。

---

## 7. 端到端冒烟脚本（A3）

路径：`tests/smoke/smoke_wiring.py` ｜ 运行：`python tests/smoke/smoke_wiring.py`（退出码 0=全过）。
沙箱 HOME（`tempfile.mkdtemp`）+ `snapshot.HOME` + `VISION_RELAY_CONFIG_DIR` 全隔离，
免交互、可重复（每次独立沙箱，finally 清理）。输出留档：`refactor/smoke-output.txt`。

三场景：
1. **正常接线**：四种 harness start 后 base_url 指本代理、claude/codex 生成 `.vision-relay.bak`；
   stop 后有效路由状态与原始一致（codex TOML 逐字节一致，JSON 语义一致）。
2. **重复启动**：已有 `.bak` 时二次 start 不覆盖（`.bak` 始终保存首次原始值，未被代理地址污染）；
   stop 仍能还原。
3. **legacy 迁移**：仅存 `.qwen-mm-proxy.bak` 时，stop 按既有兼容逻辑整文件还原并清理 legacy 备份。

> 说明：JSON harness（claude/qwen/zcode）的 `write_base_url` 会整体重序列化（缩进/尾换行/
> qwen 准入门留下的空 `generationConfig`），逐字节比对无意义；故比对**有效路由状态**
>（全局 base_url + 各条目 baseUrl + zcode 模态门）。codex 为 TOML 正则原位替换，额外逐字节比对。

冒烟运行输出：

```
wiring 端到端冒烟（沙箱 HOME，四种 harness）
  ① 正常接线：start 改写 + .bak；stop 还原为原始内容
    [PASS] claude: start 后 base_url 指向本代理
    [PASS] codex: start 后 base_url 指向本代理
    [PASS] qwen-code: start 后 base_url 指向本代理
    [PASS] zcode: start 后 base_url 指向本代理
    [PASS] claude: 生成 .vision-relay.bak
    [PASS] codex: 生成 .vision-relay.bak
    [PASS] claude: stop 后有效路由状态与原始一致
    [PASS] claude: .bak 已清理
    [PASS] codex: stop 后有效路由状态与原始一致
    [PASS] codex: stop 后与原始内容逐字节一致
    [PASS] codex: .bak 已清理
    [PASS] qwen-code: stop 后有效路由状态与原始一致
    [PASS] qwen-code: .bak 已清理
    [PASS] zcode: stop 后有效路由状态与原始一致
    [PASS] zcode: .bak 已清理
  ② 重复启动：已有 .bak 不静默覆盖丢失原配置
    [PASS] claude: 重复 start 后 .bak 仍为首次原始值
    [PASS] codex: 重复 start 后 .bak 仍为首次原始值
    [PASS] claude: .bak 内容等于原始配置（未被代理地址污染）
    [PASS] claude: stop 还原原始路由状态
    [PASS] codex: stop 还原原始
  ③ legacy 迁移：.qwen-mm-proxy.bak 按既有兼容逻辑处理
    [PASS] 前置：仅 legacy .bak 存在
    [PASS] 产生还原消息: ['claude: bak restored']
    [PASS] claude: 从 legacy .bak 还原为原始内容
    [PASS] legacy .bak 已清理
    [PASS] 无残留新后缀 .bak

冒烟全部通过 ✅（三场景）
```

---

## 8. 提交脉络（E1）

```
10fb73e test(wiring): 门面公共面契约测试（自审修正：__all__ 漂移无守护）
6f9641e refactor(wiring): 自审修正——抽出 wiring_status、消除 restore 重复块
4d5f83e docs(wiring): 清零"三处 harness"漂移注释→四处（F1）
4994e1f test(wiring): 补强守护测试 + 沙箱 HOME 端到端冒烟脚本
3215551 refactor(wiring): wiring.py 收敛为薄 facade——持有 HOME 隔离点并重导出公共 API
3280742 refactor(wiring): 抽出 wiring_orchestrate 模块——start/stop 编排与回滚
36047c4 refactor(wiring): 抽出 relays 模块——模板激活/还原与工具自动 relay
2ddd301 refactor(wiring): 抽出 zcode_providers 模块——zcode v2 条目级接线
b1c58f4 refactor(wiring): 抽出 qwen_providers 模块——qwen 条目级接线
5c198bf refactor(wiring): 抽出 harness_io 模块——base_url 读写与 codex 目录补丁
26e36e1 refactor(wiring): 抽出 modalities 模块——跨 harness 图片门原语
8c52096 refactor(wiring): 抽出 harness_spec 模块——接线规格/路径/归属判定
```

每个新模块一个独立提交（依赖顺序：spec → modalities → io → qwen/zcode → relays → orchestrate → facade），
facade 提交前旧单体仍在、测试可独立回退。

---

## 9. 工程约束核对

- **约束 1（行为零变更）**：见 §2，逐函数搬运 + 公共面不变 + 既有测试零改动。
- **约束 2（不改既有断言）**：`git diff main --stat -- tests/` 仅新增 2 文件、0 删除。未发现测试自身缺陷。
- **约束 3（ruff 零告警）**：`ruff check .` → All checks passed（遵循仓库 `ruff.toml`，line-length 120，忽略 E501）。
- **约束 4（零新增运行时依赖）**：`pyproject.toml` 的 `dependencies` 未改（仍仅 `httpx`）；未加任何运行时依赖。
- **约束 5（gui/ 不动）**：未触碰 `gui/`。
- **约束 6（不碰真实家目录）**：新测试与冒烟脚本全部 monkeypatch `wiring.HOME`/`snapshot.HOME`
  并设 `VISION_RELAY_CONFIG_DIR` 至沙箱；`conftest.py` 的 autouse `_isolated_config_dir` 继续生效。
- **约束 7（语义化小步提交）**：见 §8。

---

## 10. 假设与说明（歧义自行裁决，记录于此）

1. **HOME 注入方式**：选择"子模块接 `home` 参数 + facade 薄包装注入"，而非"子模块 `from . import wiring`
   读 `wiring.HOME`"。后者会在 facade 与子模块间形成导入环（虽 Python 可容忍，但脆弱且隐晦）；
   依赖注入更纯、更易测、零环。代价是 6 个入口在 facade 多一层薄包装，已计入 186 行。
2. **`_Harness.rel_path` 注解收紧**：由 `str` 改为 `str | tuple[str, ...]`（实际本就是 tuple）。
   纯类型注解，frozen dataclass 无运行时校验，行为零变更；使注解与实际相符。
3. **`wiring_orchestrate` 内部改名**：`wiring_backup_and_rewrite` 等 6 个 `wiring_*` 函数在
   orchestrate 模块内去掉 `wiring_` 前缀（`backup_and_rewrite`/`restore`/`report`/…），
   因模块名已含 wiring 语义；facade 保留原名重导出，外部调用面不变。
4. **冒烟脚本 JSON 比对口径**：JSON harness 比对有效路由状态而非逐字节（`write_base_url`
   重序列化），codex TOML 逐字节比对。这是对"stop 后与原始内容 diff 一致"的语义化落实。
5. **docs/ 历史记录不改**：`docs/history/**`、`docs/superpowers/**` 中"三处 harness"字样属历史
   记录/spec（`ruff.toml` 明确 docs 保持原样），未改动；README 经核对接线描述已为四家、无过时。
6. **`.coverage` 等本地产物入 `.gitignore`**：新增 `.coverage`/`coverage.json`/`htmlcov/` 忽略项，
   避免覆盖率产物污染工作区（与重构无行为耦合）。

---

## 11. 自审与修正（第二轮 review）

对首版拆分做严格自审，定位 3 处薄弱并已修复（提交 `6f9641e` / `10fb73e`）：

### 薄弱 1：`wiring_orchestrate` 混入只读查询 `report()`（cohesion 错位）

- **问题**：orchestrate 的 docstring 是"start/stop 编排与回滚"，但 `report()` 是**只读状态查询**
  （既非 start 也非 stop 也非回滚），且使该模块成为最大文件（328 行）。按"生命周期"切分，
  查询与编排是两类职责，混在一起会让"改 start 流程"与"改 report 展示"互相干扰 diff/评审。
- **修前→修后**：抽出 `wiring_status.py`（45 行，纯查询无副作用），`report()` 整体迁入；
  facade 的 `wiring_report` 改委托 `wiring_status.report`。orchestrate 328→297 行。
  ```diff
  - def report(cfg, home):  # 在 wiring_orchestrate.py
  + # wiring_status.py（新模块）
  + def report(cfg, home): ...
  - def wiring_report(cfg): return wiring_orchestrate.report(cfg, HOME)
  + def wiring_report(cfg): return wiring_status.report(cfg, HOME)
  ```
- **测试**：`wiring_report` 经 facade 被既有测试覆盖（`tests/test_proxy_qwen_providers.py`
  多处断言 `wiring_report` 行的 wired/stat），移动后全绿；`wiring_status.py` 覆盖率 100%。

### 薄弱 2：restore 路径 4 处重复"删过期 .bak"、3 处重复"codex 目录还原"（DRY）

- **问题**：`restore` / `restore_by_snapshot` / `_restore_harness_on_stop` /
  `_generic_snapshot_or_bak_restore` 各内联一份
  `bak = _find_bak(p); if bak is not None: try os.unlink(bak) except OSError: pass`；
  codex 目录还原 `if name == "codex": cat_msg = _restore_codex_catalog(p); if cat_msg: ...`
  也重复 3 处。复制块意味着"改备份清理语义"要改 4 处，易漏改导致漂移。
- **修前→修后**：抽 `_drop_stale_bak(p)` 与 `_codex_catalog_restore_msg(name, p)` 两个 helper，
  6 处调用点改为单行委托，**行为逐字保持**（同样的守卫、同样的 OSError 静默、同样的消息文案）。
  ```diff
  - bak = _find_bak(p)
  - if bak is not None:
  -     try: os.unlink(bak)
  -     except OSError: pass
  + _drop_stale_bak(p)
  ```
- **测试**：helper 由既有 restore 测试覆盖（`test_proxy_wiring.py::TestRestoreOnStop`、
  `TestCodexCatalogModalities`、`TestRestoreBySnapshot`），未新增专门测试（既有断言已锁住
  "还原后删 .bak""codex 目录还原消息"行为）；全量 578 passed 证明语义未变。

### 薄弱 3：facade 公共面无回归测试（`__all__` 漂移只在运行时暴露）

- **问题**：facade 的 `__all__` 是手写 72 项。子模块搬运/改名后，`from vision_relay.wiring import X`
  或 `wiring.X` **不会在 import 期报错**，只在真正调用时 `AttributeError`——原无任何测试守护
  这条契约，是"测试弱"。
- **修前→修后**：新增 `TestFacadeContract`（3 例，纯测试提交 `10fb73e`）：
  - `test_original_public_surface_still_reachable`：原 wiring 单体的 61 个顶层符号仍可经
    `wiring.X` 访问（防搬运丢失/改名）；
  - `test_all_dunder_resolves_to_real_objects`：`__all__` 每项真实可解析（防 star-import 静默缺项）；
  - `test_home_bound_wrappers_delegate_to_submodules`：10 个 HOME 绑定包装确实注入 HOME 并委托
    子模块（防退化为空壳/自实现业务逻辑）。
- **测试**：本身即测试，17 守护测试全绿。

> 自审后 wiring 相关覆盖率 87.4%→**88.5%**，整包 85.5%→**85.7%**；模块数 7→**8**（仍在 4~8 区间）。

---

# 第二部分：CLI 子系统结构化重构（cli.py + verbs.py）

> 基线分支：`main` ｜ 日期：2026-08-31
> 目标：契约层与领域实现分离、变更热点降温；新增一个 GUI 动词时改动面局部化。

## CLI-0. 结果速览

| 维度 | 指标 | 结果 |
|---|---|---|
| 结构 | cli.py / verbs.py ≤ 300 行 | **159 / 93 行**（原 595 / 642） |
| 结构 | 新模块数 6~10 | **9 个**（6 verbs 域 + 3 cli 域），各一句话 docstring |
| 结构 | 涉及文件 ≤ 500 行 | 最大 `cli_commands.py` 244 行 |
| 结构 | 包内顶层导入无环 | DAG（见 CLI-5）；领域/命令模块仅在**函数内**延迟 import facade |
| 契约 | envelope/contract_version 零变更 | `verbs_contract.py` 单点持有，审查契约只看此文件 |
| 测试 | 4 条点名守护 | 解析矩阵 / envelope / stdin 非法输入 / 交互确认流 |
| 测试 | 自选 ≥3 未覆盖路径 | 3 条（config_get 打码 / vlm_secret 豁免 / relay_set） |
| 测试 | 覆盖率不低于基线 | CLI+verbs 合计 79.0%→**82.7%**；整包 85.7%→**86.4%** |
| 过程 | 既有测试断言零改动 | `git diff main -- tests/` 仅新增 |
| 过程 | ruff 零告警 / 零新增运行时依赖 | `pyproject` dependencies 未改 |
| 过程 | 每新模块独立语义化提交 | 11 个提交（见下） |

```
d33f97c refactor(cli): cli.py 收敛为薄 facade
14c8acc refactor(cli): 抽出 cli_commands 模块
6aa3da2 refactor(cli): 抽出 cli_lifecycle 模块
6b1b0f4 refactor(cli): 抽出 cli_args 模块
b06fea8 refactor(verbs): verbs.py 收敛为薄 facade
ba69402 refactor(verbs): 抽出 verbs_status 模块
ab60a26 refactor(verbs): 抽出 verbs_settings 模块
dda1004 refactor(verbs): 抽出 verbs_probe 模块
d670c6c refactor(verbs): 抽出 verbs_vlm 模块
b5a6e26 refactor(verbs): 抽出 verbs_models 模块
43dd80d refactor(verbs): 抽出 verbs_contract 模块
```

## CLI-1. 模块布局（9 新模块 + 2 facade）

**verbs 侧（契约 + 领域）：**

| 模块 | 行数 | 一句话职责 |
|---|---:|---|
| `verbs_contract.py` | 70 | 通信契约层：envelope/CONTRACT_VERSION/_stdin_json/_locked_save + 5 个 DI 注入点 |
| `verbs_models.py` | 127 | 模型能力：models-scan/set/fetch + 矩阵扫描原语 |
| `verbs_vlm.py` | 130 | VLM：vlm-set/secret/test + _VLMClient |
| `verbs_probe.py` | 125 | 探针：probe（单/批）+ 探测目标解析 |
| `verbs_settings.py` | 113 | 设置/relay/zcode 写动词：settings-set/relay-set/zcode-restart |
| `verbs_status.py` | 160 | 只读观测：status/refresh/diagnose/config/tools/events/visionlog |
| `verbs.py`（facade） | 93 | 重导出公共面 + 暴露 httpx；DI 点经本模块在调用时解析 |

**cli 侧（参数 + 命令）：**

| 模块 | 行数 | 一句话职责 |
|---|---:|---|
| `cli_args.py` | 75 | argparse 解析 + _JSON_MAP（16 --json 动词分发表） |
| `cli_lifecycle.py` | 243 | start/stop/status/logs + pid 管理 + 分离 spawn + 意图 |
| `cli_commands.py` | 244 | refresh/diagnose/tools/probe/events/visionlog/test-image/models/check |
| `cli.py`（facade） | 158 | main() 分发 + _safe_stdio + reconcile_reconcile DI 别名 + 重导出 |

## CLI-2. 旧 → 新 100% 映射（verbs 34 + cli 31 = 65 符号，全部可达）

> 校验脚本对比 `git show main:vision_relay/{verbs,cli}.py` 全部顶层符号与
> `hasattr(verbs|cli, …)`：verbs 34/34、cli 31/31，`missing: NONE`。

**verbs.py：**

| 旧符号 | 新位置 | 说明 |
|---|---|---|
| `CONTRACT_VERSION` | `verbs_contract` | 契约版本，facade 重导出 |
| `envelope` | `verbs_contract` | 统一信封 |
| `_stdin_json` / `_locked_save` | `verbs_contract` | 共用 IO 段 |
| `_observe_for_status`/`_reconcile`/`_probe_tools`/`_tail_events`/`_vl_query` | `verbs_contract` | DI 注入点（测试 monkeypatch 目标） |
| `_lookup_cap`/`_lookup_probe`/`_scan_triples` | `verbs_models` | 矩阵扫描原语 |
| `models_scan`/`models_set`/`models_fetch` | `verbs_models` | models_scan 经 facade 调 `verbs._scan_triples` |
| `_VLMClient`/`vlm_set`/`vlm_secret`/`vlm_test` | `verbs_vlm` | vlm_test 经 facade 调 `verbs._VLMClient` |
| `probe_target_for`/`probe_target_info`/`_run_probe`/`probe_one`/`probe_all_untested` | `verbs_probe` | probe_one/all 经 facade 调 `verbs.probe_target_info`/`verbs._run_probe` |
| `settings_set`/`relay_set`/`zcode_restart` | `verbs_settings` | 无 facade 依赖 |
| `status`/`refresh`/`diagnose`/`config_get`/`tools`/`events`/`visionlog` | `verbs_status` | 观测依赖经 facade 调 `verbs._observe_for_status` 等 |
| `httpx`（import） | `verbs` facade | `import httpx as httpx` 暴露 `verbs.httpx`（测试 patch `verbs.httpx.get`） |

**cli.py：**

| 旧符号 | 新位置 | 说明 |
|---|---|---|
| `PID_FILE`/`LOG_FILE` | `cli_lifecycle` | facade 重导出 |
| `_JSON_MAP` | `cli_args` | --json 动词分发表 |
| `parse_args` | `cli_args` | facade 重导出 |
| `_pid_path`/`_log_path`/`_write_pid` | `cli_lifecycle` | 被测试 patch，经 `cli.*` 调用 |
| `_terminate`/`_pid_running`/`_pid_matches_ours`/`_spawn_detached` | `cli_lifecycle` | 被测试 patch，经 `cli.*` 调用 |
| `cmd_start`/`cmd_stop`/`cmd_status`/`cmd_logs`/`cmd_start_intent`/`cmd_start_detach` | `cli_lifecycle` | facade 重导出 |
| `cmd_refresh`/`cmd_diagnose`/`cmd_tools`/`cmd_probe`/`_provider_for_group`/`cmd_events`/`cmd_visionlog`/`cmd_test_image`/`cmd_models`/`cmd_models_scan`/`cmd_check` | `cli_commands` | facade 重导出 |
| `_safe_stdio`/`main` | `cli` facade | 保留 |
| `reconcile_reconcile`（import 别名） | `cli` facade | DI 点（测试 patch `cli.reconcile_reconcile`），命令经 `cli.*` 调用 |

## CLI-3. 关键设计：DI 注入点经 facade 在调用时解析

测试 monkeypatch / mock.patch 的目标是 `verbs._reconcile`、`verbs._run_probe`、
`verbs._VLMClient`、`verbs.httpx`、`verbs.probe_target_info`、`verbs._scan_triples`、
`cli._pid_path`、`cli._log_path`、`cli._pid_running`、`cli._terminate`、
`cli._spawn_detached`、`cli.reconcile_reconcile`。这些符号被**领域/命令模块**使用，
若直接 import 会绕过 facade 上的替换。故：

- 被 patch 的符号定义在原子模块（`verbs_contract`/`verbs_models`/`verbs_vlm`/
  `verbs_probe`/`cli_lifecycle`），由 facade 重导出；
- 领域/命令模块在**函数体内** `from . import verbs` / `from . import cli`（延迟导入，
  与本仓库 cli.py 既有风格一致），在调用时解析 `verbs._reconcile(...)` /
  `cli._pid_path()` 等——替换 facade 绑定即对全部调用生效；
- 未被 patch 的符号（`envelope`/`_stdin_json`/`probe_target_for`/`reconcile_reconcile`
  的实现等）由各模块直接 import，零额外开销。

这与第一部分 wiring 的 HOME 注入同构：facade 是"测试可替换面"的唯一汇点。
顶层导入无环（领域模块顶层不 import facade；仅函数内延迟引用）。

## CLI-4. 测试守护索引

新文件：`tests/test_cli_contract_guard.py`（54 例，纯新增）。沿用 `VISION_RELAY_CONFIG_DIR`
沙箱隔离，不触碰真实家目录。

| 点名路径 | 测试 |
|---|---|
| ① 解析矩阵 | `test_parse_subcommand_name[22 例]`、`test_parse_flags_matrix`、`test_json_map_covers_all_json_verbs` |
| ② envelope 契约 | `test_envelope_shape_ok/error`、`test_contract_version_pinned`、`test_verbs_return_envelope_wrapped` |
| ③ stdin 非法输入 | `test_stdin_invalid_input_returns_error_envelope[12 例]`（models_set/vlm_set/vlm_test/settings_set） |
| ④ 交互确认流 + models-scan | `test_cmd_start_onboarding_failure_aborts`、`test_cmd_start_restart_skips_onboarding`、`test_models_scan_output_stable_keys`、`test_cmd_models_scan_delegates_to_report` |

自选 3 条（理由）：
- `test_config_get_masks_secrets_and_strips_auth_hints`：config_get 打码 + 剥离 auth_hints
  是"输出不带 key"工程宪法的被动面，原 verbs.py 中该分支无直接断言。
- `test_vlm_secret_is_the_only_plaintext_exemption`：vlm-secret 是宪法唯一刻意豁免，
  锁定其回明文范围（仅 vlm + vlm_by_harness）。
- `test_relay_set_suppressed_and_api_key`：relay-set 的压制/补 key/打码拒绝/未知 relay
  四条分支，原无集中覆盖。

既有测试（未改动，回归保证）：`test_proxy_verbs.py`、`test_proxy_cli.py`、
`test_proxy_route_fallback.py`、`test_proxy_vlm_test_image.py` 等覆盖动词语义与
cmd_start/stop/probe 全链路。

## CLI-5. 新旧调用链示意

```
__main__.py
  └─ cli.main(argv)                     [cli.py facade]
       ├─ cli_args.parse_args           [cli_args.py]
       ├─ 非 JSON: cli.cmd_*             [cli_lifecycle / cli_commands]
       │     ├─ cmd_start ──cli.*──▶ _pid_path/_terminate/reconcile_reconcile (DI)
       │     │            └─ onboarding/wiring/server/reconcile (既有低层模块)
       │     └─ cmd_probe ──verbs.*──▶ probe_target_for
       └─ --JSON: cli_args._JSON_MAP[cmd](cfg)  ──▶ verbs.<verb>
                                                    ├─ verbs_contract.envelope (契约)
                                                    ├─ verbs.* DI (_reconcile/_run_probe/...)
                                                    └─ 低层模块 (config/locking/reconcile/annotate/...)
```

顶层导入边（无环）：
```
cli_args        -> verbs
cli_lifecycle   -> pid_util, env_util            (函数内延迟: cli)
cli_commands    -> verbs                          (函数内延迟: cli)
verbs_contract  -> config, locking, reconcile, tools, visionlog
verbs_models    -> config, locking, verbs_contract (函数内延迟: verbs)
verbs_vlm       -> config, verbs_contract          (函数内延迟: verbs)
verbs_probe     -> config, reconcile, tools, verbs_contract, verbs_models (延迟: verbs)
verbs_settings  -> config, verbs_contract
verbs_status    -> config, route_fallback          (延迟: verbs)
cli (facade)    -> cli_args, cli_commands, cli_lifecycle, reconcile, verbs
verbs (facade)  -> verbs_contract, verbs_models, verbs_vlm, verbs_probe, verbs_settings, verbs_status
```
（"函数内延迟"= 仅在函数体里 `from . import cli|verbs`，非顶层导入边，故无环。）

## CLI-6. 覆盖率前后对比

留档：`refactor/coverage-cli.txt`。

| | Stmts | Miss | Cover |
|---|---:|---:|---:|
| 基线 cli.py + verbs.py 合计 | 806 | 169 | **79.0%** |
| 重构后 11 文件合计 | 862 | 149 | **82.7%** |
| 整包 vision_relay（基线） | 4648 | 664 | 85.7% |
| 整包 vision_relay（重构后） | 4704 | 642 | **86.4%** |

> 覆盖率**上升**（CLI+verbs +3.7pt，整包 +0.7pt）。facade `verbs.py` 100%、
> `cli_args.py` 100%、`verbs_status.py` 100%；`verbs_models` 98.8%、`verbs_contract` 94.3%、
> `verbs_settings` 93.1%。未覆盖行主要为 `cmd_test_image`/`cmd_check` 的 VLM/网络分支与
> 各 OSError 兜底（与基线同性质）。

## CLI-7. 未来新增动词的改动面说明（拆分价值证明）

假设要新增一个 GUI 动词 **`theme-set`**（stdin JSON 写主题设置，返回 envelope）。
按重构后的结构，改动面**局部且明确**：

1. **`vision_relay/verbs_settings.py`**：加一个 `theme_set(cfg) -> dict` 函数
   （stdin 经 `verbs_contract._stdin_json`、落盘经 `_locked_save`、返回 `envelope(...)`）。
   —— **唯一需要写业务逻辑的文件**。
2. **`vision_relay/verbs.py`** facade：在 `from .verbs_settings import ...` 与 `__all__`
   各加一行 `theme_set`。（重导出登记）
3. **`vision_relay/cli_args.py`**：`_JSON_MAP` 加一行 `"theme-set": verbs.theme_set`；
   `parse_args` 加一行 `sub.add_parser("theme-set", parents=[common])`。（CLI 接线）

**不需要碰**：`cli.py`（main 分发是数据驱动的 `_JSON_MAP` 查表，新动词自动走 `--json` 通道）、
`verbs_contract.py`（除非改 envelope 结构=改契约，才动这里并升 contract_version）、
任何其它动词模块、gui/。

对比重构前：要在 642 行的 `verbs.py` 里**找位置插入**函数（与 20 个无关动词混杂，
diff 噪音大、易碰契约），并在 595 行 `cli.py` 的 `parse_args`/`main` 两处手工接线。
重构后改动从"跨两个热点文件的大海捞针"变为"一个领域文件加函数 + 两个登记点各一行"，
且**契约审查仍只需看 `verbs_contract.py` 一个文件**。

## CLI-8. 漂移清零（F1）

- 清除 `cli_args.py` 分发表/subparser 里残留的 `# Task 1`/`# Task 2`/`# Task 3`/`# M1`
  里程碑堆积注释（保留语义注释），消除"按行号定位改动点"的失效引用。
- `verbs.py`/`cli.py` 模块 docstring 更新为 facade 职责说明，指向子模块。
- 行为零变更：CLI 接口、输出文本、退出码、stdin JSON 协议、envelope 结构、
  交互确认流全部不变（578 既有测试 + 54 新测试 = 632 passed 回归证明）。

## CLI-9. 自审与修正（CLI 段第二轮 review）

对 CLI 拆分做严格自审，定位 3 处薄弱并已修复（提交随附）：

### 薄弱 1：verbs/cli facade 公共面无契约测试（`__all__` 漂移只在运行时暴露）

- **问题**：phase-1 给 wiring facade 加了 `TestFacadeContract`，但 CLI 重构漏了同类守护。
  `verbs.py`/`cli.py` 的 `__all__`（各 30+ 项）是手写重导出，子模块改名/删符号后
  `from verbs import X` 不在 import 期报错、只在调用时 `AttributeError`。
- **修前→修后**：新增 `TestFacadeContract`（3 例）——
  `test_verbs_original_surface_still_reachable`（原 34 符号仍可达）、
  `test_cli_original_surface_still_reachable`（原 31 符号仍可达）、
  `test_all_dunder_resolves`（两 facade 的 `__all__` 每项真实可解析）。
- **测试**：本身即测试，54 守护测试全绿。

### 薄弱 2：三处分发注册无一致性守护（新增子命令易漏注册致 main 静默 `return 1`）

- **问题**：`main()` 有三处分发——早返回 dict（stop/status/logs）、`_JSON_MAP`（--json）、
  非 json if-chain。新增子命令若漏登记 `_JSON_MAP` 或漏写 `cmd_*`，会静默落到末尾
  `return 1`，无任何报错。原无测试锁这三者一致。
- **修前→修后**：新增 `test_every_subcommand_is_dispatched`（23 子命令每个要么在
  `_JSON_MAP` 要么有 `cmd_*`）与 `test_dual_commands_have_both_paths`（8 个双路命令
  两处都在）。顺带发现解析矩阵漏了 `test-image`（23 子命令只覆盖 22），已补。
- **测试**：本身即测试。

### 薄弱 3：`models_fetch` 的网络 patch 点（`verbs.httpx`）是非显然桥接，无显式契约测试

- **问题**：`verbs.py` facade 的 `import httpx as httpx` 纯粹是为让测试
  `monkeypatch verbs.httpx.get` 而存在的重导出；`models_fetch` 用本模块顶层 httpx，
  二者靠"同一模块对象"隐式关联。新读者难以看出 patch 点，且原 guard 文件无锁定。
- **修前→修后**：新增 `test_models_fetch_intercepted_by_verbs_httpx_patch`——
  patch `verbs.httpx.get` 后断言 `models_fetch` 不发真实 HTTP、URL 拼接正确、
  回环 relay 只进 `skipped` 不进 `providers`。把隐式桥接锁成显式契约。
- **测试**：本身即测试。

> 自审后守护测试 47→**54** 例，全量 625→**632 passed**；行数校准：cli.py 158→**159**、
> cli_lifecycle 237→**243**（ruff 重排 import 后漂移，NOTES 已据实测订正）。
