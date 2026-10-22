#!/usr/bin/env python3
"""wb.py 的完全权限版：WorkBuddy 能执行命令、能用 agent-browser（可自测、可操作浏览器）。

用法和 wb.py 完全相同（dispatch / rework / approve / show），只是 CLI 模式下：
  - WorkBuddy 以 bypassPermissions 运行
  - 外面套 macOS 沙盒：只能写小助理空间、临时目录、WorkBuddy 和浏览器自己的数据目录，其余一律禁止写入
  - 使用 WorkBuddy 的配置目录（能加载它装的技能），PATH 里加上 agent-browser

建议在 AI 客户端里给本文件设置「每次运行都要用户批准」的规则。
用本脚本派的任务会标记 full，返工也必须用本脚本。
"""
import os
import sys
from pathlib import Path

import wb

WB_CONFIG = Path.home() / ".workbuddy"
# WorkBuddy 自带多个 node 版本，agent-browser 只装在其中一个里，按它所在的版本找（不是取最新版）
WB_NODE_BINS = sorted((WB_CONFIG / "binaries" / "node" / "versions").glob("*/bin/agent-browser"))

SANDBOX_WRITABLE = [
    wb.WORKSPACE,
    Path("/private/tmp"),
    Path("/private/var/folders"),
    Path("/dev"),
    WB_CONFIG,
    Path.home() / ".agent-browser",
    Path.home() / "Library" / "Caches",
]
SANDBOX_PROFILE = "(version 1)(allow default)(deny file-write*)(allow file-write* {})".format(
    " ".join(f'(subpath "{p}")' for p in SANDBOX_WRITABLE)
)


def full_cli_command(run, prompt):
    run["full"] = True
    cmd = ["sandbox-exec", "-p", SANDBOX_PROFILE,
           wb.CLI, "-p", prompt, "--output-format", "stream-json", "--permission-mode", "bypassPermissions"]
    if run.get("session_id"):
        cmd += ["--resume", run["session_id"]]
    env = dict(
        os.environ,
        CODEBUDDY_CONFIG_DIR=str(WB_CONFIG),
        PATH=f"{WB_NODE_BIN}:{os.environ['PATH']}",
        # Chrome 自带的沙盒和外层 sandbox-exec 嵌套会导致浏览器卡死；外层沙盒已限制写入范围
        AGENT_BROWSER_ARGS="--no-sandbox",
        # 不带 --headed 的命令会把浏览器换成无头模式重开；固定可见窗口 + 固定资料目录，登录才不会丢
        AGENT_BROWSER_HEADED="1",
        AGENT_BROWSER_PROFILE=str(Path.home() / ".agent-browser" / "profiles" / "workbuddy"),
    )
    return cmd, env


if __name__ == "__main__":
    if not WB_NODE_BINS:
        sys.exit("找不到 WorkBuddy 自带的 agent-browser（~/.workbuddy/binaries/node/versions/*/bin/），完全权限模式需要它")
    WB_NODE_BIN = WB_NODE_BINS[-1].parent
    wb.cli_command = full_cli_command
    wb.main()
