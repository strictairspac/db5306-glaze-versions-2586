from sqlalchemy import (JSON, Column, DateTime, ForeignKey, Integer, MetaData,
                        String, Table, Text, UniqueConstraint)

schema = MetaData()
users = Table('accounts', schema,
    Column('id', Integer, primary_key=True),
    Column('username', String(40), nullable=False, unique=True),
    Column('password_hash', String(200), nullable=False),
    Column('role', String(20), nullable=False))
cases = Table('business_cases', schema,
    Column('id', Integer, primary_key=True),
    Column('slug', String(64), nullable=False, unique=True),
    Column('title', String(120), nullable=False),
    Column('payload', JSON, nullable=False),
    Column('author', String(40), nullable=False),
    Column('created_at', String(32), nullable=False))
runs = Table('business_runs', schema,
    Column('id', Integer, primary_key=True),
    Column('case_id', ForeignKey('business_cases.id'), nullable=False, index=True),
    Column('status', String(20), nullable=False, index=True),
    Column('owner', String(40), nullable=False),
    Column('attempt_token', String(36)),
    Column('started_at', String(32)),
    Column('finished_at', String(32)),
    Column('created_at', String(32), nullable=False),
    Column('error', Text))
evidence = Table('business_evidence', schema,
    Column('id', Integer, primary_key=True),
    Column('run_id', ForeignKey('business_runs.id'), nullable=False, unique=True),
    Column('report', JSON, nullable=False),
    Column('digest', String(64), nullable=False),
    Column('reviewer', String(40)),
    Column('review_note', String(500)),
    Column('reviewed_at', String(32)))
audit = Table('audit_events', schema,
    Column('id', Integer, primary_key=True),
    Column('actor', String(40), nullable=False),
    Column('action', String(32), nullable=False),
    Column('reference', String(50), nullable=False),
    Column('created_at', String(32), nullable=False))

# ---------------------------------------------------------------------------
# 离线改方模块：只追加的配方版本链、读快照与改方批单
# ---------------------------------------------------------------------------
formulas = Table('glaze_formulas', schema,
    # 同一配方的每个版次一行；永不更新、永不覆盖，旧档案可按水位重放。
    Column('id', Integer, primary_key=True),
    Column('formula_id', String(64), nullable=False),
    Column('version_no', Integer, nullable=False),
    Column('commit_seq', Integer, nullable=False, index=True),
    Column('operation', String(8), nullable=False),          # 写入 / 删除
    Column('recipe', Text),
    Column('source_order_id', String(36)),                   # 由哪张批单提交，档案录入为空
    Column('created_at', String(32), nullable=False),
    UniqueConstraint('formula_id', 'version_no', name='uq_formula_version'),
    UniqueConstraint('formula_id', 'commit_seq', name='uq_formula_commit'))

read_snapshots = Table('glaze_read_snapshots', schema,
    # 打开读快照时固化水位；之后新增的提交对它不可见。
    Column('id', Integer, primary_key=True),
    Column('snapshot_code', String(64), nullable=False, unique=True),
    Column('read_seq', Integer, nullable=False),
    Column('label', String(120), nullable=False),
    Column('opened_by', String(40), nullable=False),
    Column('opened_at', String(32), nullable=False))

change_orders = Table('glaze_change_orders', schema,
    # 离线改方批单。order_id 由领单方生成，是收单幂等键。
    Column('id', Integer, primary_key=True),
    Column('order_id', String(36), nullable=False, unique=True),
    Column('title', String(120), nullable=False),
    Column('status', String(12), nullable=False, index=True),  # 待收/已接纳/已退回
    Column('base_seq', Integer, nullable=False),               # 领单时已提交水位
    Column('commit_seq', Integer),                             # 接纳时整单共用的提交序号
    Column('conflicts', JSON),                                 # 退回时的冲突配方明细
    Column('items', JSON, nullable=False),                     # 领单内容与基线快照
    Column('created_by', String(40), nullable=False),
    Column('created_at', String(32), nullable=False),
    Column('received_at', String(32)),
    Column('received_by', String(40)))

seq_counters = Table('glaze_seq_counters', schema,
    # 提交序号发号器；单行行锁保证整单一个序号且并发不重号。
    Column('name', String(32), primary_key=True),
    Column('next_seq', Integer, nullable=False))
