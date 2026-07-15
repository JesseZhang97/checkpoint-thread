# Checkpoint Thread

[![skills.sh](https://skills.sh/b/JesseZhang97/checkpoint-thread)](https://skills.sh/JesseZhang97/checkpoint-thread/checkpoint-thread)

`checkpoint-thread` 用来管理一个 Codex task 中跨文件、跨目标、跨 branch，
以及偶尔跨 worktree 或多个 repo 的 Git 工作。

本仓库是该 skill 的主仓库；`skill/checkpoint-thread` 是可安装的 skill 本体，
其余目录提供规格、测试、验收目录和真实远端协作证据。

它解决的核心问题是：Codex task 没有内建回退点，但又不应该每个 turn 都
提交一次。这个 skill 以 task 为所有权范围、以目标边界为 checkpoint、以
branch 为交付通道，只推送当前 task 真正拥有的提交。

## 启用

使用 skills CLI 全局安装到 Codex：

```bash
npx skills add JesseZhang97/checkpoint-thread \
  --skill checkpoint-thread --agent codex --global --yes
```

然后新建一个 Codex task，让 skill 列表重新加载。更新已安装版本时运行：

```bash
npx skills update checkpoint-thread --global --yes
```

仓库贡献者也可以从仓库根目录执行 `npx skills add . --list`，验证 CLI 能发现
`skill/checkpoint-thread`。

## 日常怎么用

安装并启用 skill 后，正常描述开发任务即可，不需要每次手动执行 `begin`：

```text
把订单详情页的操作栏调窄一点。
```

在第一次修改仓库之前，skill 会懒执行 `begin`。只读分析不会创建 ledger 或
Git ref；继续调整同一个目标时也不会执行 checkpoint 命令。

也可以在首次请求中显式触发：

```text
$checkpoint-thread 修复订单详情页操作栏高度问题。
```

## 推荐对话方式

你只需要表达真实意图，不需要使用固定转折词。

| 你的行为 | Skill 的行为 |
|---|---|
| 让 Codex 分析代码，没有修改 | 不创建任何状态 |
| 开始修改当前 repo | 懒执行一次 `begin` |
| 继续微调同一个目标 | 不创建新 checkpoint |
| 直接开始一个不同的低风险目标 | 为前一个目标创建 provisional 私有 ref |
| 明确认可结果，或客观验收通过 | 将精确相关路径提交为本地原子 commit |
| 切换到其他 branch/worktree | 先提交或 park 当前状态，再登记目标 branch |
| task 涉及另一个 repo | 在同一 ledger 中独立登记该 repo |
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

## 推送时会检查什么

在推送前，skill 会确认：

- ship set 只包含当前 task 拥有且尚未发布的提交；
- provisional checkpoint 已被提交或明确排除；
- 验证已经通过，或者明确记录为 `not_applicable`；
- worktree 干净，没有未完成的 merge/rebase；
- remote、upstream 和 merge target 可确定；
- 远端分歧可以安全 rebase，且不会改写他人或已发布历史。

同一 remote 的多个 branch 使用原子推送。跨 repo 或跨 remote 无法保证原子性，
报告会明确列出已经推送、仍在本地、被阻断或失败的 branch。

## 你会收到什么

完成 ship 后，报告包含：

- repo、branch、最终 commit SHA 和 upstream；
- verification、排除文件和恢复 ref；
- rebase/冲突处理结果与最终 push 状态；
- 每个 branch 的 source、target、依赖顺序、合并策略和合并后验证方案。

失败的 snapshot、hook、fetch、rebase 或 push 不会被报告为成功，也不会 force
push。私有 `refs/codex/checkpoint-thread/...` 永远不会推送到 remote。

## 手动 CLI

日常使用不需要直接操作 CLI。开发、诊断或恢复时可查看命令：

```bash
python3 skill/checkpoint-thread/scripts/checkpoint_thread.py \
  --ledger-id <task-id> --help
```

默认 ledger 位于：

```text
/Users/daydreamer/Developer/.codex-ledgers/checkpoint-thread/active
```

设计与验收细节见 `SPEC.md`、`ACCEPTANCE_CRITERIA.md` 和
`FINAL_REPORT.md`。运行完整验收：

```bash
python3 scripts/verify_acceptance.py \
  --remote-evidence acceptance/evidence/github-collaboration-final.json \
  --output acceptance/results.json
```
