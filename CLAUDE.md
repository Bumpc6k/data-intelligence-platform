# 给 AI 助手：先读 AGENTS.md

本仓库的**权威 AI 须知**在根目录 [`AGENTS.md`](AGENTS.md)；请先完整读它，再动手。
人类协作者入口：[`CONTRIBUTING.md`](CONTRIBUTING.md)。

**五条铁律（摘要，详见 AGENTS.md）**
1. 事实不许编：结论必须能指到来源（文件/端点/行号），拿不到就说"没查到"
2. 无凭证不出结论：`evidence` 为空时不许给数字/公式
3. 没有证据 = 没完成：命令 + 原始输出 + 测试（**含反例**）+ 边界声明
4. 冻结资产只调用不修改：内核 / lineage-client / dip-contracts / dip-pg
5. `main` 只进 PR；不许改 CI / 门禁 / 测试断言来绕过红灯

**任务从 Issue 来**：`gh issue list --label M1-垂直切片`（关键路径：#1 契约、#3 血缘 skill）
**提交前必跑**：`bash ops/gate.sh` → 必须 `GATE: ALL_PASS`
