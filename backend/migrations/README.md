# 初始结构与离线改方模块

`src/models/tables.py` 定义关系结构。API 启动时显式创建演示库的表；本地演示与新库由 `schema.create_all` 一次性建齐。

## 初始 schema

`accounts`、`business_cases`、`business_runs`、`business_evidence`、`audit_events` 服务于回收预检的档案、执行与证据流程。

## 离线改方模块新增表

| 表 | 作用 | 关键约束 |
| --- | --- | --- |
| `glaze_formulas` | 只追加的配方版本链 | `(formula_id, version_no)` 与 `(formula_id, commit_seq)` 双唯一 |
| `glaze_read_snapshots` | 读快照及其固化水位 | `snapshot_code` 唯一 |
| `glaze_change_orders` | 离线批单与收单结论、冲突明细 | `order_id` 唯一（收单幂等键） |
| `glaze_seq_counters` | 提交序号发号器 | 单行行锁串行发号 |

## 已有部署升级

本仓库的演示环境可直接重启建表。对已存在数据的部署，`create_all` 不会改已有表，需要按版本化迁移单独执行等价的 `CREATE TABLE`（仅新增表、不改动初始五张表），并向 `glaze_seq_counters` 插入起始行 `('glaze_commit_seq', 1)`；应用启动时的 `ensure_counter` 也会在缺失时补插。若新表需要承接历史台账，迁移脚本须把既有配方版本按原提交序号回填，并把发号器起始序号设为「历史最大提交序号 + 1」。
