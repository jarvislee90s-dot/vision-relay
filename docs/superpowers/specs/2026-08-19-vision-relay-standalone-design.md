# vision-relay - 独立项目设计规格 (Standalone Design Spec)

**日期**: 2026-08-19  **状态**: Draft / 待评审
**上游来源**: Qwen-MM-Plugins 的 proxy capability（分支 vision-proxy）

## 1. 背景与动机

proxy 是 Qwen-MM-Plugins 中一个边界清晰、近乎纯新增的能力：它把文生模型（Text-only）变成可读图，通过本地 HTTP 服务（默认 127.0.0.1:8787）拦截 Anthropic / Responses / Chat 三类请求里的图片，交给 VLM 转述为文字，再把纯文本转发给真正的上游。

分析作者本人的 11 个 commit（b40783c..00c630e，精确按作者 jarvislee90s-dot 过滤）显示：

| 指标 | 值 |
|---|---|
| 新增代码 | +5,963 行 |
| 删除代码 | -29 行 |
| 改动文件 | 43 个 |
| 改动既有非 proxy 源码文件 | 0 个 |
| 对宿主库的运行时依赖 | 仅 shared.env（4 处 import，2 个函数） |

结论：proxy 与宿主库的业务代码耦合度极低（唯一硬依赖是 get_env / config_dir 两个函数），具备独立成库的现实可行性。本 spec 定义将其抽离为独立项目 vision-proxy 的范围、结构与发布方式。

## 2. 项目定位

- 名称: vision-relay（PyPI 包名 / 仓库名 / 可执行名；旧别名 qwen-mm-plugins-proxy 已移除——从未发布，无人依赖）
- 形态: 本地驻留 HTTP 代理服务（非 Skill + MCP server）
- 职责: 拦截图片 -> VLM 转述 -> 转发文本给上游文本模型
- Python: >=3.10，运行时依赖仅 httpx

## 3. 范围（Scope）

### 3.1 纳入独立项目的内容

| 类别 | 内容 | 说明 |
|---|---|---|
| 源码 | qwen_mm_plugins_proxy/（14 个模块，2,389 行） | 原样搬入，不改业务逻辑 |
| 配置访问 | proxy_env.py（54 行） | 替代 shared.env 的 get_env / config_dir |
| 安装接线 | install-proxy.sh（85 行） | 从宿主 install.sh 提取的 6 个 proxy 函数 |
| 加载器 | install.sh | source install-proxy.sh，兼容既有测试 |
| 测试 | tests/test_proxy_*.py（13 个）+ conftest.py | 159 用例全绿 |
| 发布 | pyproject.toml、README.md、LICENSE | 独立打包 |
| 协作规范 | .github/、CONTRIBUTING、SECURITY、ruff.toml | 抄自上游并适配 |

### 3.2 明确不带入（YAGNI / 保留上游）

- 宿主其它 capability: core / api / video-memory / video-edit / blender / freecad / search / edu-agent
- src/shared/*（除按需抽取的 get_env/config_dir 语义）；mcp_framework.py
- 上游作者（非本人）的改动: isolated_worker.py、paths.py、api_openai.py 的渲染器/API 修改
- 宿主完整 install.sh（8k+ 行）的其余部分（多 capability 选择、check-system 等）

## 4. 架构与数据流

        [Agent harness]  Claude Code / Codex / Qwen Code  (base_url 已改写)
              |  inbound
              v
        [   vision-proxy   ]   server.py(HTTP) / ir.py(协议解析) / pipeline.py(编排)
              |            |      vlm.py / capability.py / cache.py / cli / config / wiring
      images  |            |  text only
              v            v
        [ VLM ]          [ upstream text model ]   relay (chat/responses/anthropic)

### 4.2 关键模块职责

| 模块 | 职责 |
|---|---|
| ir.py | 三类协议（Anthropic / Responses / Chat）的中间表示 + 解析/序列化 |
| server.py | HTTP 服务器、路由、转发、SSE |
| pipeline.py | 请求处理流程（图片->VLM->文本 注入） |
| vlm.py | VLM 客户端抽象、重试、能力判定 |
| cache.py | Tier1/2 描述缓存 |
| capability.py | model -> vision/text 能力表 |
| config.py / wiring.py | 配置加载 / harness base_url 改写 |
| cli.py | start/stop/status/logs/check/models 等命令 |

### 4.3 与宿主的唯一依赖解耦

原代码 4 处 from shared.env import get_env/config_dir 全部替换为 from proxy_env import ...。proxy_env.py 复刻了两个函数的语义（env > 用户配置文件 > 默认），不含宿主其它逻辑。实测：替换后编译通过，159 用例全部通过。

## 5. 发布与生命周期

- 版本: 由 vision_relay.__version__（当前 0.1.0）驱动（dynamic = version）
- 打包: pyproject.toml 声明 py-modules = proxy_env，包发现 include = vision_relay*
- console script: vision-relay = vision_relay.__main__:main
- CI: 离线测试（pytest + httpx）+ ruff lint + bash 语法检查 + Windows checkout（适配后的 ci.yml）

## 6. 测试

- 离线测试 python -m pytest -q（覆盖 ir/vlm/cache/pipeline/server/routing/cli/install 接线）
- 手动验证: 三终端（Claude Code / Codex / Qwen Code）真实图片注入、SSE、中文多字节、Windows 生命周期
- 未来: 可选 live 测试（真实 VLM/上游）用 -m reachability（沿用上游标记约定，非默认）

## 7. Roadmap

- [x] 独立 git 仓库 + 首个 commit（已完成）
- [ ] 发布到 PyPI（vision-proxy / qwen-mm-plugins-proxy）
- [ ] GitHub Actions 绿色 CI + 分支保护
- [ ] 截图/演示 + 更完整的用户文档（含 troubleshooting）

## 8. 待决策 / Open Questions（2026-08-19 全部已决策）

- 包名最终用 vision-proxy 还是 qwen-mm-plugins-proxy？→ **vision-relay**（品牌切换定案；执行见 `2026-08-19-vision-relay-rebrand-docs-port-design.md`）。
- console script 名是否保留 qwen-mm-plugins-proxy 以兼容上游文档？→ **不保留**（从未发布，无人依赖）。
- 是否要同时为中文本地化维护 README.zh.md？→ **是**（双语言 README 已就位）。

## 附: 迁移清单（已验证）

1. 复制 qwen_mm_plugins_proxy/（14 模块）。
2. 新建 proxy_env.py，替换 4 处 from shared.env import。
3. 提取 install-proxy.sh（宿主 install.sh 行 1310-1389）。
4. 复制 manifest（.claude-plugin / .codex-plugin / .qoder-plugin / .mcp.json）到 src/capabilities/proxy/。
5. 复制 13 个测试 + 精简 conftest.py。
6. 运行 python -m pytest -q -> 159 passed。
7. 编写 pyproject / README / LICENSE / .github / CONTRIBUTING / SECURITY / ruff.toml。