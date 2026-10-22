#!/usr/bin/env python3
"""workbuddy-bridge: AI（Claude、Codex 等）派活给 WorkBuddy、验收、打回返工的连接器。

每个任务的文件放在 WORKSPACE/<RUN_ID>/（WorkBuddy 里对应的工作空间）。

两种模式：
  CLI（默认）：直接调用 WorkBuddy 自带的 CLI，一条命令跑完一轮；返工续接同一个会话。
              WorkBuddy 以 acceptEdits 运行：只能读写文件，不能执行命令，测试由验收方 AI 来跑。
              执行过程实时写到 runs/<RUN_ID>.log，可用 tail -f 观看。WorkBuddy App 界面上看不到。
  界面（--gui）：用深链接在 WorkBuddy App 里新建任务，过程在 App 里可见。
              深链接只预填不发送，每轮要由验收方 AI（或用户）点「发送」，再运行 wait；
              返工是新开一个任务（带原任务和反馈）。

  wb.py dispatch [--gui] "任务描述"   派新任务（第 1 轮）
  wb.py rework RUN_ID "验收反馈"      打回返工（轮数 +1）
  wb.py wait RUN_ID                   仅界面模式：点发送后等 WorkBuddy 做完并打印回复
  wb.py approve RUN_ID                验收通过，结案
  wb.py show [RUN_ID]                 查看任务记录（不填则列出全部）

每个任务的完整往来记录存在 runs/<RUN_ID>.json。
"""
import argparse
import datetime
import json
import subprocess
import sys
import time
import urllib.parse
import uuid
from pathlib import Path

CLI = "/Applications/WorkBuddy.app/Contents/Resources/app.asar.unpacked/cli/bin/codebuddy"
WORKSPACE = Path.home() / "WorkBuddy" / "agent-bridge"
WB_PROJECTS = Path.home() / ".workbuddy" / "projects"
RUNS = Path(__file__).resolve().parent / "runs"
MAX_ROUNDS = 5
IDLE_SECONDS = 8
MAX_PREV_REPLY = 1500  # 深链接 prompt 上限 8000 字，上一轮回复截断后再带上

WORKER_PREAMBLE = (
    "你是执行者。另一个 AI 会验收你的产出，不合格会打回并给出具体反馈。"
    "完成后，最后用简短列表说明：改了哪些文件、做了什么、你自己怎么验证的。\n"
)


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def load(run_id):
    path = RUNS / f"{run_id}.json"
    if not path.exists():
        sys.exit(f"找不到任务 {run_id}")
    return json.loads(path.read_text())


def save(run):
    RUNS.mkdir(exist_ok=True)
    (RUNS / f"{run['id']}.json").write_text(json.dumps(run, ensure_ascii=False, indent=2))


def finish_round(run, reply):
    run["status"] = "待验收"
    run["history"][-1]["worker_reply"] = reply
    save(run)
    print(f"RUN_ID: {run['id']}  第 {run['round']}/{MAX_ROUNDS} 轮  状态: {run['status']}")
    print(f"任务目录: {run['dir']}")
    print("---- WorkBuddy 回复 ----")
    print(reply)


# ---------- CLI 模式 ----------

def describe_tool(block):
    args = block.get("input", {})
    for key in ("file_path", "command", "pattern", "path", "url"):
        if key in args:
            return f"{block['name']} {str(args[key])[:200]}"
    return f"{block['name']} {json.dumps(args, ensure_ascii=False)[:200]}"


def cli_command(run, prompt):
    """返回 (命令, 环境变量)。wb_full.py 会替换这个函数。"""
    if run.get("full"):
        sys.exit("这是完全权限任务，返工请用 wb_full.py")
    cmd = [CLI, "-p", prompt, "--output-format", "stream-json", "--permission-mode", "acceptEdits"]
    if run.get("session_id"):
        cmd += ["--resume", run["session_id"]]
    return cmd, None


def run_cli(run, prompt):
    cmd, env = cli_command(run, prompt)
    run["status"] = "进行中"
    run["history"].append({"round": run["round"], "time": now(), "to_worker": prompt})
    save(run)
    log_path = RUNS / f"{run['id']}.log"
    print(f"实时过程: tail -f '{log_path}'", flush=True)

    result = {}
    with open(log_path, "a") as log:
        log.write(f"\n===== 第 {run['round']} 轮  {now()} =====\n>>> 发给 WorkBuddy:\n{prompt}\n\n")
        log.flush()
        proc = subprocess.Popen(cmd, cwd=run["dir"], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for line in proc.stdout:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "assistant":
                for block in event["message"].get("content", []):
                    if block.get("type") == "tool_use":
                        log.write(f"🔧 {describe_tool(block)}\n")
                    elif block.get("type") == "text" and block["text"].strip():
                        log.write(f"💬 {block['text']}\n")
            elif event.get("type") == "result":
                result = event
            log.flush()
        proc.wait()
        log.write(f"===== 第 {run['round']} 轮结束 =====\n")

    if not result:
        sys.exit(f"WorkBuddy 没有返回结果（exit {proc.returncode}）:\n{proc.stderr.read()[-2000:]}")
    run["session_id"] = result.get("session_id")
    if result.get("is_error"):
        print("⚠️ WorkBuddy 报错")
    if result.get("permission_denials"):
        print(f"⚠️ 被拒绝的操作: {json.dumps(result['permission_denials'], ensure_ascii=False)}")
    finish_round(run, result.get("result", ""))


# ---------- 界面模式 ----------

def tag(run):
    return f"[RUN {run['id']} 第{run['round']}轮]"


def send_task_gui(run, body):
    """用深链接在 WorkBuddy 的小助理空间新建任务并预填，记下本轮的发送时间。"""
    prompt = (
        f"{tag(run)}\n{WORKER_PREAMBLE}"
        f"本任务目录：./{run['id']}/（所有文件都放在这里，不要动其它目录）\n\n{body}"
    )
    query = urllib.parse.urlencode(
        {"action": "start", "prompt": prompt, "cwd": str(WORKSPACE)}, quote_via=urllib.parse.quote
    )
    run["status"] = "待发送"
    run["sent_after"] = time.time()
    run["transcript"] = None
    run["history"].append({"round": run["round"], "time": now(), "to_worker": prompt})
    save(run)
    subprocess.run(["open", "workbuddy://task?" + query], check=True)
    print(f"RUN_ID: {run['id']}  第 {run['round']} 轮已在 WorkBuddy「{WORKSPACE.name}」空间新建并预填。")
    print("下一步：在 WorkBuddy 窗口点「发送」，然后运行 wb.py wait " + run["id"])


def read_records(path):
    records = []
    for line in path.read_text().splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            pass  # 正在写入的半行
    return records


def find_session(run):
    """找到本轮发送后新建、首条消息带本轮标记的 WorkBuddy 会话记录文件。"""
    for path in sorted(WB_PROJECTS.glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
        if path.stat().st_mtime < run["sent_after"]:
            break
        records = read_records(path)
        first = records[0] if records else {}
        if first.get("cwd") == str(WORKSPACE) and tag(run) in json.dumps(first, ensure_ascii=False):
            return path


def assistant_text(record):
    return "".join(c.get("text", "") for c in record.get("content", []) if c.get("type") == "output_text")


def wait(args):
    run = load(args.run_id)
    if run["mode"] != "gui":
        sys.exit("wait 只用于界面模式；CLI 模式的 dispatch/rework 会自己等到做完")
    deadline = time.time() + args.timeout
    while not run["transcript"]:
        path = find_session(run)
        if path:
            run["transcript"] = str(path)
            run["history"][-1]["session_id"] = path.stem
            save(run)
        elif time.time() > deadline:
            sys.exit("超时：没看到 WorkBuddy 新建会话，确认是否已点发送")
        else:
            time.sleep(2)

    path = Path(run["transcript"])
    while True:
        records = read_records(path)
        last = records[-1] if records else {}
        idle = time.time() - path.stat().st_mtime
        if last.get("role") == "assistant" and last.get("status") == "completed" and idle >= IDLE_SECONDS:
            break
        if time.time() > deadline:
            sys.exit("超时：WorkBuddy 还没做完（可再次运行 wait 继续等）")
        time.sleep(2)
    finish_round(run, assistant_text(last))


# ---------- 命令 ----------

def dispatch(args):
    run = {
        "id": uuid.uuid4().hex[:8],
        "mode": "gui" if args.gui else "cli",
        "task": args.task,
        "round": 1,
        "history": [],
    }
    (WORKSPACE / run["id"]).mkdir(parents=True, exist_ok=True)
    run["dir"] = str(WORKSPACE / run["id"])
    if run["mode"] == "gui":
        send_task_gui(run, args.task)
    else:
        run_cli(run, WORKER_PREAMBLE + "\n" + args.task)


def rework(args):
    run = load(args.run_id)
    if run["status"] == "已通过":
        sys.exit("该任务已验收通过，不能再打回")
    if run["status"] != "待验收":
        sys.exit(f"当前状态是「{run['status']}」，要等上一轮做完才能打回")
    if run["round"] >= MAX_ROUNDS:
        run["status"] = "超出轮数，需人工介入"
        save(run)
        sys.exit(f"已达 {MAX_ROUNDS} 轮上限，停止自动返工，请交给用户判断")
    run["round"] += 1
    if run["mode"] == "gui":
        prev_reply = run["history"][-1].get("worker_reply", "")[:MAX_PREV_REPLY]
        send_task_gui(run, (
            "这是返工任务。上一轮的产出没有通过验收，请在本任务目录里现有文件的基础上修改。\n\n"
            f"【原任务】\n{run['task']}\n\n"
            f"【上一轮你的说明】\n{prev_reply}\n\n"
            f"【验收反馈】\n{args.feedback}"
        ))
    else:
        run_cli(run, "验收未通过，请按以下反馈返工：\n\n" + args.feedback)


def approve(args):
    run = load(args.run_id)
    run["status"] = "已通过"
    run["approved_at"] = now()
    save(run)
    print(f"RUN_ID: {run['id']} 验收通过，共 {run['round']} 轮")


def show(args):
    if args.run_id:
        print(json.dumps(load(args.run_id), ensure_ascii=False, indent=2))
        return
    for path in sorted(RUNS.glob("*.json")) if RUNS.exists() else []:
        run = json.loads(path.read_text())
        print(f"{run['id']}  {run.get('mode', 'gui')}  {run['status']}  第{run['round']}轮  {run['task'][:40]}")


def main():
    parser = argparse.ArgumentParser(description="AI ↔ WorkBuddy 派活/验收连接器")
    sub = parser.add_subparsers(required=True)

    p = sub.add_parser("dispatch", help="派新任务")
    p.add_argument("--gui", action="store_true", help="走 WorkBuddy App 界面（默认走 CLI）")
    p.add_argument("task")
    p.set_defaults(func=dispatch)

    p = sub.add_parser("rework", help="打回返工")
    p.add_argument("run_id")
    p.add_argument("feedback")
    p.set_defaults(func=rework)

    p = sub.add_parser("wait", help="仅界面模式：等 WorkBuddy 做完并打印回复")
    p.add_argument("run_id")
    p.add_argument("--timeout", type=int, default=900, help="秒")
    p.set_defaults(func=wait)

    p = sub.add_parser("approve", help="验收通过")
    p.add_argument("run_id")
    p.set_defaults(func=approve)

    p = sub.add_parser("show", help="查看任务")
    p.add_argument("run_id", nargs="?")
    p.set_defaults(func=show)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
