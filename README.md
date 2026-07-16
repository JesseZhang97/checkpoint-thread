# Checkpoint Thread

[![skills.sh](https://skills.sh/b/JesseZhang97/checkpoint-thread)](https://skills.sh/JesseZhang97/checkpoint-thread/checkpoint-thread)

`checkpoint-thread` 用来管理一个 Codex task 中跨文件、跨目标、跨 branch，
以及偶尔跨 worktree 或多个 repo 的 Git 工作。

本仓库同时是 Codex plugin marketplace 和 skills.sh skill。plugin 包含同步 Hook，
`skill/checkpoint-thread` 则是可独立安装的轻量 skill；其余目录提供规格、测试、
验收目录和真实远端协作证据。

它解决的核心问题是：Codex task 没有内建回退点，但又不应该每个 turn 都
提交一次。这个 skill 允许多个 task 在看似混杂的 dirty branch 上工作，同时
把实际修改关联到 task 和业务目标，最后再整理成原子 commit。

## 推荐启用方式

完整 V2 使用 Codex plugin 安装。它会同时启用 skill、`PreToolUse` guard 和
`PostToolUse` settle：

```bash
codex plugin marketplace add JesseZhang97/checkpoint-thread --ref main
codex plugin add checkpoint-thread@checkpoint-thread
```

升级时运行：

```bash
codex plugin marketplace upgrade checkpoint-thread
```

只需要指令层、接受没有 Hook 硬约束时，仍可通过 skills.sh 安装：

```bash
npx skills add JesseZhang97/checkpoint-thread \
  --skill checkpoint-thread --agent codex --global --yes
```

skills.sh 版本的更新命令是：

```bash
npx skills update checkpoint-thread --global --yes
```

安装或更新后新建 Codex task，让 skill 和 Hook 配置重新加载。仓库贡献者可从
根目录执行 `npx skills add . --list`，或在隔离的 `CODEX_HOME` 中把当前目录添加
为本地 plugin marketplace，验证两种发布面。

第一次需要修改仓库时，skill 会让你确认 ledger 保存位置。推荐值是：

```text
${CODEX_HOME:-$HOME/.codex}/ledgers/checkpoint-thread/active
```

确认后只配置一次；只读分析不会触发配置。后续 task 自动复用该位置。

## 日常怎么用

安装并启用后，正常描述开发任务即可，不需要每次手动执行 `enter`：

```text
把订单详情页的操作栏调窄一点。
```

在第一次修改仓库之前，Hook 会同步执行 `guard`，内部完成 `enter`、预检和基线
ref；没有 Hook 时 skill 会直接执行一次 `enter`。只读分析不会创建 ledger 或
Git ref；继续调整同一个目标时也不需要 checkpoint 命令。

Hook 不是后台进程或文件监听器，只在 Codex tool 调用前后执行。人工保存文件不会
触发它；此时可手动运行 `enter` 或在下一次 Codex 修改前由 guard 接管。

刚执行 `git init`、尚无初始 commit 的仓库也受支持：checkpoint 可以正常
park/restore，第一次验收后的 `promote` 会创建只包含已选择路径的 root
commit。

也可以在首次请求中显式触发：

```text
$checkpoint-thread 修复订单详情页操作栏高度问题。
```

## 推荐对话方式

你只需要表达真实意图，不需要使用固定转折词。

| 你的行为 | Skill 的行为 |
|---|---|
| 让 Codex 分析代码，没有修改 | 不创建任何状态 |
| 开始修改当前 repo | Hook 自动 `guard`，必要时 `enter` |
| 继续微调同一个目标 | 不创建新 checkpoint |
| 直接开始一个不同的低风险目标 | 为前一个目标创建 provisional 私有 ref |
| 明确认可结果，或客观验收通过 | 将精确相关路径提交为本地原子 commit |
| 只保留本地 commit 并结束 task | `close` 记录本地完成状态 |
| 切换到其他 branch/worktree | 先提交或 park 当前状态，再登记目标 branch |
| task 涉及另一个 repo | 在同一 ledger 中独立登记该 repo |
| 另一个 task 同时修改同一 branch | 允许继续；记录各自 contribution，重叠路径在提交时处理 |
| 明确要求“提交并推送” | 执行 ship，并返回推送报告和合并方案 |

例如，一个自然的使用过程可以是：

```text
修复用户列表的行高。
再窄一点。
把筛选栏的重置按钮也对齐。
现在提交并推送，给我合并方案。
```

第二句仍属于同一个目标，不会产生 checkpoint。第三句即使没有“接下来”这类
转折词，也会根据对象和意图变化被识别为新目标。最后一句才授权 fetch、rebase
和 push。

## 归属账本

V2.1 只使用用户所选 ledger root 下的 `checkpoint-thread.sqlite3` 保存 provenance
ledger，不创建 per-task JSON 投影。恢复内容仍存放在仓库的私有 Git refs。
PreToolUse 暂存 before-state，PostToolUse 只在内容真实变化时写入一条 contribution；
没有变化的 Hook 调用不产生持久事件。

同一 repo branch 允许多个 task 同时登记和编辑。Contribution 保存 task、goal、
before/after `state_oid` 和 changed paths；同一路径被多个 task 修改时记录 overlap，
但不会阻止编辑。人工或外部修改保持 unattributed，直到提交阶段明确分配。SQLite
只是业务归属账本，不拥有 branch，也不复制 Git 的 commit DAG。

## 推送时会检查什么

在推送前，skill 会确认：

- ship set 只包含已归属且尚未发布的提交；
- provisional checkpoint 已被提交或明确排除；
- 验证已经通过，或者明确记录为 `not_applicable`；
- worktree 干净，没有未完成的 merge/rebase；
- remote、upstream 和 merge target 可确定；
- 远端分歧可以安全 rebase，且不会改写他人或已发布历史。

同一 remote 的多个 branch 使用原子推送。跨 repo 或跨 remote 无法保证原子性，
报告会明确列出已经推送、仍在本地、被阻断或失败的 branch。

## 你会收到什么

完成 ship 后，报告包含：

- repo、branch、goal/contribution、最终 commit SHA 和 upstream；
- verification、排除文件和恢复 ref；
- rebase/冲突处理结果与最终 push 状态；
- 每个 branch 的 source、target、依赖顺序、合并策略和合并后验证方案。

失败的 snapshot、hook、fetch、rebase 或 push 不会被报告为成功，也不会 force
push。私有 `refs/codex/checkpoint-thread/...` 永远不会推送到 remote；成功 ship
后，已经由 pushed commit 表达的 recovery refs 会被清理，只在 ledger 保留回执。

## 手动 CLI

日常使用不需要直接操作 CLI。开发、诊断或恢复时可查看命令：

```bash
python3 skill/checkpoint-thread/scripts/checkpoint_thread.py \
  --ledger-id <task-id> --help
```

手动完成首次配置：

```bash
python3 skill/checkpoint-thread/scripts/checkpoint_thread.py \
  --ledger-root "${CODEX_HOME:-$HOME/.codex}/ledgers/checkpoint-thread/active" \
  configure
```

选择结果保存在
`${CODEX_HOME:-$HOME/.codex}/checkpoint-thread/config.json`。修改已有选择需要用户确认，
并显式追加 `configure --replace`。非配置命令不能临时切换 root，避免出现多套
相互矛盾的 ledger。

设计、架构精简度与验收细节见 `SPEC.md`、
`ARCHITECTURE_ASSESSMENT.md`、`ACCEPTANCE_CRITERIA.md` 和 `FINAL_REPORT.md`。
运行完整验收：

```bash
python3 scripts/verify_acceptance.py \
  --remote-evidence acceptance/evidence/github-collaboration-final.json \
  --output acceptance/results.json
```
