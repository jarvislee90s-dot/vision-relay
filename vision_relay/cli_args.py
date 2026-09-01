"""CLI 参数解析与 --json 动词分发表（16 子命令 × 旗标）。

本模块是 CLI 的"接口面"：argparse 解析（parse_args）与 --json 动词分发表
（_JSON_MAP，命令名 → verbs 动词）。新增一个 GUI 动词时，在此登记子命令与
分发表条目即可（详见 REFACTOR_NOTES「未来新增动词的改动面」）。
"""

from __future__ import annotations

import argparse

from . import __version__, verbs

# --json 动词分发表（spec §4 通信契约：envelope + contract_version，GUI 只消费这个）。
_JSON_MAP = {
    "status": verbs.status,
    "refresh": verbs.refresh,
    "diagnose": verbs.diagnose,
    "models-scan": verbs.models_scan,
    "models-set": verbs.models_set,
    "config": verbs.config_get,
    "tools": verbs.tools,
    "events": verbs.events,
    "visionlog": verbs.visionlog,
    "vlm-set": verbs.vlm_set,  # stdin JSON 写全局/分组/自定义提示词
    "vlm-test": verbs.vlm_test,  # 与生产同一调用路径的连通测试（stdin JSON）
    "vlm-secret": verbs.vlm_secret,  # 设置页「显示」按钮按需回显明文 VLM key（config 仍打码）
    "settings-set": verbs.settings_set,  # stdin 白名单设置（unknown_default / vision_log）
    "relay-set": verbs.relay_set,  # 停用压制 / 补 key
    "zcode-restart": verbs.zcode_restart,  # zcode 待重启提示条/弹窗选项①共用
    "probe": verbs.probe_one,  # --json 探针（main 特判补 harness/provider/model）
    "models-fetch": verbs.models_fetch,  # 拉上游模型 ID 清单（spec §5）
}

# 子命令全集（真相源）：与 parse_args 的 add_parser 注册一一对应，
# 守护测试（test_refactor_guard_cli）据此构建解析矩阵并校验无静默缺口。
SUBCOMMANDS: tuple[str, ...] = (
    "start",
    "stop",
    "status",
    "logs",
    "test-image",
    "check",
    "models-scan",
    "models-set",
    "models",
    "refresh",
    "diagnose",
    "tools",
    "probe",
    "events",
    "visionlog",
    "config",
    "vlm-set",
    "vlm-test",
    "vlm-secret",
    "settings-set",
    "relay-set",
    "zcode-restart",
    "models-fetch",
)
# 按设计不挂公共 --json parent 的子命令：start（起服直连）/ test-image（直连工具）。
_NO_JSON_PARENT = frozenset({"start", "test-image"})
# 有专属旗标的子命令（其余仅公共 --json）。
_SPECIAL_FLAGS = frozenset({"start", "test-image", "probe", "events", "visionlog"})


def _add_special_flags(parser: argparse.ArgumentParser, name: str) -> None:
    """为带专属旗标的子命令挂旗标（与 parse_args 内注册逐字一致；守护测试据此比对）。"""
    if name == "start":
        parser.add_argument("--detach", action="store_true", help="分离进程启动（GUI/自动重试用）")
    elif name == "test-image":
        parser.add_argument("path")
        parser.add_argument("--question", default=None)
    elif name == "probe":
        parser.add_argument("--harness")
        parser.add_argument("--provider")
        parser.add_argument("--model")
        parser.add_argument("--all-untested", action="store_true")
    elif name == "events":
        parser.add_argument("--limit", type=int, default=50)
    elif name == "visionlog":
        parser.add_argument("--harness")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="vision-relay")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    # 公共 parent：管理动词统一可挂 --json（子命令后置 flag 也可用）。
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="machine-readable output (contract_version pinned)")
    st = sub.add_parser("start")
    st.add_argument("--detach", action="store_true", help="分离进程启动（GUI/自动重试用）")
    sub.add_parser("stop", parents=[common])
    sub.add_parser("status", parents=[common])
    sub.add_parser("logs", parents=[common])
    ti = sub.add_parser("test-image")
    ti.add_argument("path")
    ti.add_argument("--question", default=None)
    sub.add_parser("check", parents=[common])
    sub.add_parser("models-scan", parents=[common])  # 非交互打印模型能力草稿
    sub.add_parser("models-set", parents=[common])  # stdin 三元组写入（source=user；null=清除）
    sub.add_parser("models", parents=[common])  # 显式交互入口：重新确认/编辑 model_capabilities
    sub.add_parser("refresh", parents=[common])  # 手动对账（= 刷新按钮后端）
    sub.add_parser("diagnose", parents=[common])  # 观测 + 自动修复 + 报告
    sub.add_parser("tools", parents=[common])  # 工具档案探测
    pr = sub.add_parser("probe", parents=[common])  # 模态探针（--json 走 probe_one）
    pr.add_argument("--harness")
    pr.add_argument("--provider")
    pr.add_argument("--model")
    pr.add_argument("--all-untested", action="store_true")
    ev = sub.add_parser("events", parents=[common])  # 事件日志 tail（--limit 0 = 全量导出）
    ev.add_argument("--limit", type=int, default=50)
    vl = sub.add_parser("visionlog", parents=[common])  # 识图记录查询
    vl.add_argument("--harness")
    sub.add_parser("config", parents=[common])  # --json 配置读取（打码）
    sub.add_parser("vlm-set", parents=[common])  # stdin JSON 写 VLM 全局/分组/自定义提示词
    sub.add_parser("vlm-test", parents=[common])  # stdin JSON VLM 连通测试（共享生产路径）
    sub.add_parser("vlm-secret", parents=[common])  # 设置页「显示」按钮按需回显明文 VLM key
    sub.add_parser("settings-set", parents=[common])  # stdin 白名单设置（unknown_default / vision_log）
    sub.add_parser("relay-set", parents=[common])  # 停用压制 / 补 key
    sub.add_parser("zcode-restart", parents=[common])  # zcode 待重启提示条/弹窗选项①共用
    sub.add_parser("models-fetch", parents=[common])  # 拉上游模型 ID 清单（spec §5）
    return parser.parse_args(argv)
