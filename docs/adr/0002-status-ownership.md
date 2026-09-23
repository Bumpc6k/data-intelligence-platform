# ADR-0002：`status` / `version` 由平台推导，内核零改动

- 状态：**已采纳**（2026-09-23，用户拍板 1A）
- 相关：工作项 W-103；实现 `packages/dip-contracts/src/dip_contracts/status.py`

## 背景

统一结果模型要求每个结论带 `status ∈ verified/inferred/candidate/unresolved/stale` 与 `version`。
实测内核**没有**这两个字段：口径命中只有 `confidence`（如 0.9）、`chinese_source`（`exact_glossary`/`builtin`/`rule`/`pending`）、
`source_script`（文件路径），知识库层面只有 `schema_version: 1.0.0` 与 `built_at`。

## 决策

1. `status` 由平台按固定判定表推导（优先级顺序写在 `derive_status()` 里，可测）。
2. `version` 取内核知识库快照：`kb:{schema_version}@{built_at 日期}`；P2 引入平台口径审核表后换成人审版本号。
3. **不改内核契约**（内核处于 Phase 0 冻结）。
4. 诚实说明：P1 的 `verified` 含义是"来自人工维护的词表 / 脚本推导且来源明确"，
   **不等于"平台人工审核通过"**；界面上要写清楚，避免误导。

## 后果

- 好：P1 立即可用；判定逻辑集中在契约层，前后端不会各判一套。
- 代价：`verified` 语义暂时偏弱；等 P2 有审核表后升级为 `reviewed`。
- 撤回条件：若内核将来直接返回 `status`/`version`，本 ADR 作废并删掉推导逻辑（测试里有断言盯着）。
