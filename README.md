# workbuddy-bridge

让 AI 当监工，WorkBuddy 当员工：Claude、Codex 等 AI 负责写任务和验收标准，把活派给 WorkBuddy 去做，再亲自跑测试验收，不合格就带着具体反馈打回返工，最多 5 轮。

```
你的需求 → AI 写任务 + 验收标准 → 你确认
  → wb.py dispatch → WorkBuddy 干活（产物在 ~/WorkBuddy/agent-bridge/<RUN_ID>/）
  → AI 亲自跑测试验收
      ├─ 不通过：wb.py rework（续接同一个会话，带上失败用例和期望值）
      └─ 通过：wb.py approve
```

> **非官方工具**：本项目与腾讯、WorkBuddy、CodeBuddy 没有任何关系。它调用的是 WorkBuddy 安装包里自带的命令行和深链接，不是官方公开接口，WorkBuddy 更新后可能失效。

## 为什么这样分工

- **执行者自己说「已验证」不算数**：WorkBuddy 在普通模式下不能执行命令，它说的验证只是读代码推演。测试必须由验收方真正跑一遍
- **验收标准先写好、先给你看**：测试放在任务目录之外，执行者改不到，没法改测试来凑通过
- **返工给的是具体反馈**：哪个用例、期望什么、实际是什么、只许改哪些文件
- **有轮数上限**：5 轮还不过就停下来交给人判断，不会无限循环

适合派出去的活：**说得清、验得了、量够大**。比如结果能客观检查的小工具、规则明确的批量整理、初稿和脚手架。很小的改动、主要靠审美判断的、直接改你重要项目的，不适合派。

## 包含什么

| 文件 | 作用 |
|---|---|
| `skills/workbuddy/SKILL.md` | 给 AI 读的流程说明：什么活适合派、怎么写任务和验收标准、怎么验收和返工、权限和安全边界。Claude Code、Codex 等支持技能的客户端都能用 |
| `wb.py` | 连接器：派活、返工、验收通过、查看记录 |
| `wb_full.py` | 完全权限版：让 WorkBuddy 能执行命令、操作浏览器，外面套一层 macOS 沙盒（见下文风险说明） |

## 环境要求

- macOS（完全权限模式用到 macOS 自带的 `sandbox-exec`）
- 已安装并登录 [WorkBuddy](https://www.codebuddy.cn/work/) 桌面版
- Python 3（只用标准库，不需要装任何包）
- 一个支持技能和终端的 AI 客户端，比如 Claude Code 或 Codex

开发环境：WorkBuddy 5.5.6（自带命令行 2.137.1）、macOS 26.6、Python 3.14。

> **测试情况**：本仓库的连接器只做了静态检查和命令行检查（语法编译、`--help`、`show`），发布前没有真实派活跑通。它和作者日常在用的连接器是同一套逻辑，只改了注释、默认产物目录、提示文字，以及完全权限模式查找 agent-browser 的方式。WorkBuddy 调用模型偶尔会中断（日志里出现 `aborted`），有时会卡住不动，遇到时可以先看 `runs/<RUN_ID>.log`，确认长时间没有新动作后结束进程，再重新派。

## 安装

1. 下载本仓库，放在任意目录，比如 `~/tools/workbuddy-bridge`
2. 把 `skills/workbuddy/` 复制到 AI 客户端的技能目录：
   - Claude Code：`~/.claude/skills/workbuddy/`
   - Codex：`~/.codex/skills/workbuddy/`
3. 第一次使用时告诉 AI 连接器在哪，比如：「让 WorkBuddy 做 xxx，连接器在 ~/tools/workbuddy-bridge/wb.py」。AI 会先读一遍连接器代码、检查环境，再给你看任务描述和验收标准

## 用法

平时直接跟 AI 说「让 WorkBuddy 做……你来验收」就行，命令由 AI 来跑。命令本身：

```bash
python3 wb.py dispatch "任务描述"          # 派新任务（第 1 轮），一直等到 WorkBuddy 做完
python3 wb.py rework RUN_ID "验收反馈"     # 打回返工，续接同一个会话
python3 wb.py approve RUN_ID               # 验收通过，结案
python3 wb.py show [RUN_ID]                # 查看记录；不填 RUN_ID 列出全部
```

- 每个任务的产物放在 `~/WorkBuddy/agent-bridge/<RUN_ID>/`
- 往来记录和实时日志放在连接器旁的 `runs/`：`tail -f runs/<RUN_ID>.log` 可以实时看 WorkBuddy 在做什么
- 想换产物目录，改 `wb.py` 顶部的 `WORKSPACE`

## 三种模式

| 模式 | 命令 | WorkBuddy 能做什么 | 什么时候用 |
|---|---|---|---|
| **普通（默认）** | `wb.py dispatch` | 只能读写文件，不能执行命令 | 绝大多数任务 |
| 完全权限 | `wb_full.py dispatch` | 能执行命令、用 agent-browser 操作浏览器；写文件被沙盒限制在指定目录 | 任务确实需要跑命令或用浏览器 |
| 界面 | `wb.py dispatch --gui` 然后 `wb.py wait` | 在 WorkBuddy App 里新建任务，过程看得见；需要有人点「发送」 | 想在 App 里看过程 |

完全权限模式派的任务，返工也必须用 `wb_full.py`，不能混用。

## ⚠️ 风险说明（完全权限模式）

完全权限模式下，WorkBuddy 以「跳过所有确认」的方式运行，唯一的约束是外层的 macOS 沙盒：

- **只限制写**：可写目录只有产物目录、临时目录、`~/.workbuddy`、`~/.agent-browser`、`~/Library/Caches`；**读不受限制**，它能读到你主目录下的文件
- **能联网**：沙盒不限制网络
- `sandbox-exec` 是 macOS 的旧机制，苹果已标记为不推荐使用

所以：默认用普通模式；需要完全权限时，只派你放心的任务，并建议在 AI 客户端里给 `wb_full.py` 设置「每次运行都要你批准」。登录类网站让 WorkBuddy 截二维码给你扫，不要让它填密码。

## 作者

Bowen · 20 年产品人，亲手用 AI 解决 100 个真实问题 · AI 实战 100 例

抖音：[Bowen的AI实战笔记](https://www.douyin.com/user/MS4wLjABAAAAsVy4x0FpjE8j4epQQZtZkg2OOU2Vb3G5UweYp5ZZwd0)（抖音号 1083328779）

其他开源项目：
- [meeting-minutes](https://github.com/ttgg20200101-claw/meeting-minutes)：跟 AI 说「开会」就录音，说「结束」就出会议纪要并核实事实说法，可以搭配本项目派 WorkBuddy 核实
- [vibe-fx](https://github.com/ttgg20200101-claw/vibe-fx)：让 AI 写代码做动效视频的 Canvas 特效库

## License

MIT
