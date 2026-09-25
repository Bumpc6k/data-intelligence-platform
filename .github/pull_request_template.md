## 这个 PR 做了什么

<!-- 一句话说清；关联 Issue：Closes #__ -->

## 怎么验证（必填）

```bash
# 贴出可复制的命令与关键原始输出（不要只写"已测试通过"）
bash ops/gate.sh
```

- [ ] `bash ops/gate.sh` → `GATE: ALL_PASS`
- [ ] 验收标准的每一条都逐条对过（见 Issue）
- [ ] **反例也测了**（该被拦的确实被拦）
- [ ] 没有提交密钥（`.env` 未入库）
- [ ] 冻结资产只按接口调用（内核 / lineage-client / dip-contracts / dip-pg）

## 证据

<!-- 命令输出、截图、录屏路径（存 docs/evidence/） -->

## 边界确认

- [ ] 只改了 Issue 范围内的东西，没有顺手改别的模块
