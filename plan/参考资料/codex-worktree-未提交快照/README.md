# codex worktree 未提交状态快照（一次性保险）

删除 `~/.codex/worktrees/{daa3,ed71,05ea}/Trip` 前留的备份，`git apply` 可还原。
三者均为本仓 worktree，detached HEAD @ `946f8c4`，分别有 83 / 59 / 48 项未提交改动。

**删除前已验证不会丢东西：**

1. `946f8c4` 已完全包含在 main 中（main 领先 69 个提交，worktree 独有 0 个提交）。
2. `daa3` 的未提交改动逐文件比对，内容全部命中 main 历史提交
   （`6387d30` 意图路由重构、`74e7cdf` Phase 2 Mock RAG），仅 `.gitignore` 例外。
3. `ed71` / `05ea` 有若干行在 main 历史中查无——**经查是旧 coordinator/slot 版聊天
   实现**（`coordinator.plan_full` / `refresh_slice` / `rewrite` /
   `answer_open_question` / `legacy_payload()`），正是意图路由那轮 Task 9
   "Remove Obsolete Slot And Coordinator Chat Logic" 有意删除的死代码，
   不是丢失的工作。
4. 文档侧另行核对过 6 处共 204 份，见上级 `plan/README.md`。

确认无遗漏后，本目录可整个删掉（约 1.7 MB）。
