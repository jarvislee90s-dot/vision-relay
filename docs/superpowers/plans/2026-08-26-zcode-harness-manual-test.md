# zcode Harness 手工验收清单（Windows 真机）

> 日期：2026-08-26
> 对象：`461055d`（评审修复后 HEAD）
> 依据：spec `docs/superpowers/specs/2026-08-26-zcode-harness-design.md` §11 剧本 1–10 + 评审七项 Important 的真机回归
> 记录方式：每项勾选 `[x]`/`[ ]`，实际结果栏写现象关键词；失败项记录「现象 + 当时 events/proxy.log 最后 10 行」

---

## ⚠️ 开始前的三个安全注意

1. **备份 zcode 配置**：本清单会真实改写 `C:\Users\bunny\.zcode\v2\config.json`。先复制一份：
   ```bash
   cp ~/.zcode/v2/config.json ~/zcode-config.backup-$(date +%m%d-%H%M).json
   ```
2. **「立即重启 zcode」会杀掉所有 zcode 进程**：如果你正把这个清单交给某个跑在 zcode 里的 agent 执行，点重启按钮时它的会话会被终止。建议从系统终端/另一个环境执行 GUI 操作，或接受会话中断后重开。
3. **已知 Minor 不算新 bug**：见文末 M1–M8（restart 失败无提示、「保留勾选」状态不回滚等都是已记档项），遇到对应现象直接勾 Minor 对照项，不要当回归报。

## 观察渠道（四个）

| 渠道 | 位置 | 看什么 |
|---|---|---|
| GUI | 总览/模型能力/设置/事件日志页 | 拓扑卡、提示条、弹窗、relay 列表 |
| 事件流 | `~/.vision-relay/events.jsonl` | `tail -f`，看 relay_added/removed、uncheck_restore、zcode_restart、auto_fix |
| 代理日志 | `~/.vision-relay/logs/proxy.log` | `event: proxy_request` 行的 `proto`/`model`/`upstream_status` |
| 配置文件 | `~/.zcode/v2/config.json` + `~/.vision-relay/proxy.json` | 前者看 baseURL/modalities 逐项还原；后者看 relays（指纹可见、key 打码不必担心） |

快速核对命令（Git Bash）：

```bash
# zcode 关键字段一览（不打印 key）
python -c "import json;d=json.load(open(r'C:\Users\bunny\.zcode\v2\config.json',encoding='utf-8'));[print(k,e.get('kind'),e.get('enabled'),'key-set' if e.get('options',{}).get('apiKey') else 'key-empty',e['options'].get('baseURL'),[m+':'+','.join(m2.get('modalities',{}).get('input',[])) for m,m2 in e.get('models',{}).items()]) for k,e in d['provider'].items()]"
# 事件尾部
tail -5 ~/.vision-relay/events.jsonl
# 代理请求日志（proto=anthropic 即 /v1/messages 形态、chat 即 /chat/completions 形态）
grep proxy_request ~/.vision-relay/logs/proxy.log | tail -5
```

---

## A. 主剧本（spec §11 剧本 1–10）

### R1 勾选 zcode → 开路由 → 重启 → 纯文本模型识图转写

- [ ] 步骤：设置页确认 zcode 勾选 → 总览页开路由 → **弹窗三选出现**（开启并重启 / 不开启 / 稍后重启）→ 选①
- [ ] 预期：zcode 自动重启；总览 zcode 卡显示已接管（若 zcode 重启完成则无「待重启」提示条）
- [ ] 在 zcode 里用**纯文本模型**（如 GLM-5.2）读一张图
- [ ] 预期：不报错、模型能用文字描述图片内容；识图记录页出现该次记录（三段内容可查）；proxy.log 出现 `proxy_request` 且 `injected` ≥ 1
- 实际结果：

### R2 多模态模型直通（不转写）

- [ ] 步骤：在 zcode 里用**多模态模型**（kimi-k2.7-code 类，modalities 含 image）读同一张图
- [ ] 预期：识图记录**不产生**新转写记录（或该模型标注为支持图片时不走 VLM）；模型直接看到图
- 实际结果：

### R3 空密钥预置供应商：不动、不显

- [ ] 步骤：开路由前后各跑一次上面「zcode 关键字段一览」，对比 `key-empty` 的供应商（如 builtin:zai）
- [ ] 预期：其 baseURL 与模型 modalities **完全未变**；GUI 模型能力页不出现其模型行；总览 zcode 详情统计里 skipped_nokey 计数 ≥ 1
- 实际结果：

### R4 切换供应商 → 刷新 → 指纹选路

- [ ] 步骤：zcode 客户端内把激活供应商从 A 切到 B（选①类，有 key 的）→ GUI 点刷新
- [ ] 预期：总览激活供应商显示更新；用 B 家模型发请求正常（不 401）；proxy.json 里 B 家 relay 排到 zcode relay 块最前
- 实际结果：

### R5 运行期 zcode 内保存 → 冲补丁 → 对账重打

- [ ] 步骤：路由开启状态下，在 zcode 客户端里随便改一个设置并保存（触发文件回写）→ 跑「字段一览」确认 baseURL 被冲回直连 → GUI 点刷新
- [ ] 预期：刷新后字段一览恢复接管态（URL=8787、门重开）；事件流出现 `provider_absorb`；zcode 卡短暂显示「等 zcode 重启生效」中间态 + 提示条
- 实际结果：

### R6 关路由 → 逐项还原比对

- [ ] 步骤：关路由 → 弹窗三选（关闭并重启 / 不关闭 / 稍后）→ 选①
- [ ] 预期：`字段一览` 与**开路由前的备份**逐项一致：每家 baseURL 原值、纯文本模型 `input` 回 `["text"]`、`modalitiesConfigured` 回原值/消失（注：模型级 `zcode:{}` 空壳残留属已知 Minor M5）；zcode 自动重启；提示条消失
- 实际结果：

### R7 取消勾选 zcode：立即还原 + 隐身 + relay 清理

- [ ] 步骤：路由**开启**状态 → 设置页取消勾选 zcode → 保存 → 弹窗三选 → 选①
- [ ] 预期：字段一览立即回直连；总览/模型页 zcode 相关内容整体消失；`proxy.json` 的 relays 里**不再有任何 `zcode-` 条目**；事件流有 `uncheck_restore` + `relay_removed`
- [ ] 重新勾选保存 → zcode 卡/模型行恢复
- 实际结果：

### R8 弹窗三选各选项 + zcode 未运行时直接执行

- [ ] 开路由选②（不开启）：什么都不发生，开关回弹
- [ ] 开/关路由选③（稍后）：动作执行 + 总览出现常驻「zcode 待重启」提示条（含立即重启按钮）；手动重启 zcode 后 ≤10 秒（轮询周期）提示条消失
- [ ] 完全退出 zcode 后开关路由：无弹窗、直接执行
- 实际结果：

### R9 入站路径与模型名形态实证（P2-2 沉淀，重点）

- [ ] 步骤：路由开启下分别用 anthropic 类供应商模型（GLM-5.3）和 openai 类供应商模型（ark 家 deepseek）各发一次带图请求 → `grep proxy_request ~/.vision-relay/logs/proxy.log | tail -5`
- [ ] 记录：GLM-5.3 → `proto: anthropic`？（即 zcode 对 anthropic 类 baseURL 拼 `/v1/messages`）；deepseek → `proto: chat`？（即拼 `/chat/completions`）
- [ ] 记录：`model:` 字段是显示名（`GLM-5-Turbo`）还是 API 名（`glm-5-turbo`）？——双名收录都应命中，此处只为沉淀实证；**若出现第三种形态（带前后缀/大小写变换）记下来，需回补 relay.models 收录规则**
- 实际结果：

### R10 三工具回归

- [ ] claude / codex / qwen-code 各发一次带图请求：转写正常、无 401/502
- [ ] GUI 三工具拓扑卡、模型页、事件页无异常
- 实际结果：

---

## B. 评审修复专项（七项 Important 的真机确认）

### F1 跨工具同名模型不截胡（修复①）

- [ ] 构造：qwen（或任一工具）与 zcode **同协议**且模型同名（如两家都有 `GLM-5.3` 的 chat 线）——你的 ark 家（openai）与 qwen 家若同名即可；否则临时在 qwen settings.json 加一个同名模型条目
- [ ] 验证：qwen 侧发同名模型请求 → 正常返回（走 qwen 家），**不是 401**（401 = 被 zcode 家截胡，修复失效）
- 实际结果：

### F2 未列名模型 → 401 自愈而非断流（修复②）

- [ ] 构造：在 zcode 某供应商 models 里手动加一个古怪名模型（如 `my-test-model`，不在任何 relay 收录范围）→ 选它发请求
- [ ] 预期：得到**上游返回的错误（典型 401/404）**，代理透传可见——**不是** 502 `proxy internal error`、不是连接拒绝
- 实际结果：

### F3 指纹不进 GUI（修复③）

- [ ] 命令：`vision-relay config --json | python -c "import json,sys;d=json.load(sys.stdin);print(any('auth_hints' in r for r in d['data']['relays']))"`
- [ ] 预期：输出 `False`（status 通道本就不含，无需测）
- 实际结果：

### F4 取消勾选后 relay 清理（修复④）

- [ ] （与 R7 同场）取消勾选后：`vision-relay status --json | python -c "import json,sys;d=json.load(sys.stdin);print([r['name'] for r in d['data']['relays'] if r['name'].startswith('zcode-')])"` → 预期 `[]`
- 实际结果：

### F5 密钥轮换后指纹跟随（修复⑤）

- [ ] 步骤：路由开启下在 zcode 客户端里给激活供应商**换一个 key**（保存即回写文件）→ GUI 刷新 → `python -c "import json;d=json.load(open(r'C:\Users\bunny\.vision-relay\proxy.json',encoding='utf-8'));print([ (r['name'], r.get('auth_hints')) for r in d['relays'] if r.get('provider_id')])"` 前后对比
- [ ] 预期：该家 relay 的 `auth_hints` 指纹变化；换 key 后请求仍正常（指纹选路跟上）
- 实际结果：

### F6 僵尸接线：代理被杀后的自动修复（修复⑥）

- [ ] 步骤：路由开启下，任务管理器**直接杀 python 进程**（不是 GUI 关闭）→ GUI 点刷新
- [ ] 预期（路由开=崩溃前意图）：自动重启服务并**保持 zcode 接管**，事件流 `auto_fix(restart)`；zcode 不需要重接线
- [ ] 可选加测（覆盖修复⑥的原始场景）：把激活供应商切成一个**空 key 预设家**再杀代理 → 刷新 → 仍能触发修复（不因 owner≠ours 漏修）
- 实际结果：

### F7 status 轮询性能（修复⑦）

- [ ] 观察：GUI 挂着 1–2 分钟（5s 轮询），总览刷新**无明显卡顿**；任务管理器里每次轮询至多闪现**一个** powershell.exe（批量单次）
- 实际结果：

---

## C. 已知 Minor 对照（遇到即勾，不算新 bug）

- [ ] M1 zcode-restart 的 kill 成功但重启失败时无 UI 反馈（提示条可能消失）
- [ ] M2 T1 提交点单独 checkout 时测试红（bisect 中途态，不影响 HEAD）
- [ ] M3 无 baseURL 的供应商在统计里隐身
- [ ] M4 Settings 弹窗选「保留勾选」后复选框状态不回滚（再点保存会再弹窗）
- [ ] M5 模态门还原后模型级 `zcode:{}` 空壳残留
- [ ] M6 server.py 指纹层注释引错 spec 章节
- [ ] M7 zcode 探测无目标时的 reason 文案不准确
- [ ] M8 `.gitignore` 未含 `tmp_icons/`（全仓 ruff 恒红的根因）

---

## 通过判据与收尾

- **全部 R1–R10 + F1–F7 通过** → 进入收尾：CHANGELOG 补条目（Unreleased）、spec 状态行改「已实现（M-zcode）」、R9 的实证结论回写 spec §10 P2-2（路径与模型名形态记档）。
- **任何 R/F 失败** → 记录「现象 + 当时 `events.jsonl` / `proxy.log` 尾部 10 行 + 字段一览输出」，回到修复循环；R9 若发现第三种模型名形态，需回补 relay.models 收录规则后再复测 R4/F1。
- 全程**不要**把任何 key 明文贴进记录；需要引用时用「key-set/指纹形态」描述。
