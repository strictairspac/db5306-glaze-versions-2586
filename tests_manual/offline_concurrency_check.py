"""并发压力验收：多线程交叉登记版次与收单，校验序号/版本号严格唯一且幂等。

运行：python3 -I tests_manual/offline_concurrency_check.py
"""
import os
import sys
import tempfile
import threading

sys.path.insert(0, '/tmp/pyenv/pkgs')
sys.path.insert(0, '/workspace/backend')

from sqlalchemy import create_engine, event, select
from sqlalchemy.pool import QueuePool

from src.constants.workflow import ChangeOperation, OrderStatus
from src.models.tables import change_orders, formulas, schema
from src.schemas.workflow import ChangeOrderForm, OrderItemForm
from src.services import offline_orders as store

# 文件 SQLite + 每连接独立事务：WAL 下写操作取库级写锁互斥，
# 模拟 MySQL 多连接在发号器行锁上的串行化；连接池让并发线程各持连接。
_db_fd, _db_path = tempfile.mkstemp(suffix='.db', prefix='offline-conc-')
os.close(_db_fd)
engine = create_engine(
    f'sqlite+pysqlite:///{_db_path}',
    poolclass=QueuePool, pool_size=8, max_overflow=0,
    connect_args={'check_same_thread': False, 'timeout': 30})


@event.listens_for(engine, 'connect')
def _pragmas(dbapi_conn, _):
    dbapi_conn.execute('PRAGMA journal_mode=WAL')
    dbapi_conn.execute('PRAGMA busy_timeout=30000')
    dbapi_conn.isolation_level = None


@event.listens_for(engine, 'begin')
def _begin(conn):
    if not conn.connection.dbapi_connection.in_transaction:
        conn.exec_driver_sql('BEGIN IMMEDIATE')


schema.create_all(engine)
with engine.begin() as conn:
    store.ensure_counter(conn)

errors = []
lock = threading.Lock()


def register_many(formula_id, count):
    try:
        for i in range(count):
            with engine.begin() as conn:
                store.ensure_counter(conn)
                store._lock_counter(conn)
                seq = store._next_commit_seq(conn)
                store._append_version(conn, formula_id, ChangeOperation.UPSERT,
                                      f'{formula_id}-r{i}', None, seq)
    except Exception as exc:  # noqa: BLE001
        with lock:
            errors.append(repr(exc))


def receive_many(prefix, count):
    try:
        for i in range(count):
            order_id = f'{prefix}-{i:08d}-aaaa-aaaa-aaaaaaaaaaaa'
            form = ChangeOrderForm(order_id=order_id, title=f'并发单{i}', items=[
                OrderItemForm(formula_id=f'GL-O{prefix[-1]}-{i}', base_version_no=0,
                              operation='写入', recipe=f'离线配方{i}')])
            with engine.begin() as conn:
                store.create_order(conn, form, 'op')
            with engine.begin() as conn:
                store.receive_order(conn, order_id, 'op')
            # 立刻重试：必须幂等，不得再写版次或发号
            with engine.begin() as conn:
                detail, newly = store.receive_order(conn, order_id, 'op')
                assert newly is False and detail['status'] == OrderStatus.ACCEPTED.value
    except Exception as exc:  # noqa: BLE001
        with lock:
            errors.append(repr(exc))


threads = [
    threading.Thread(target=register_many, args=('GL-A', 20)),
    threading.Thread(target=register_many, args=('GL-B', 20)),
    threading.Thread(target=receive_many, args=('ordC', 15)),
    threading.Thread(target=receive_many, args=('ordD', 15)),
]
for t in threads:
    t.start()
for t in threads:
    t.join()

print('线程异常:', errors or '无')

with engine.begin() as conn:
    all_versions = conn.execute(
        select(formulas.c.formula_id, formulas.c.version_no, formulas.c.commit_seq)
        .order_by(formulas.c.id)).all()
    commit_seqs = [row[2] for row in all_versions]
    # 每个配方内版本号唯一且连续
    per_formula = {}
    for formula_id, version_no, _ in all_versions:
        per_formula.setdefault(formula_id, []).append(version_no)
    orders = conn.execute(select(change_orders)).mappings().all()
    head = store.current_head(conn)

print('版本总行数:', len(all_versions))
print('批单数:', len(orders), ' 水位:', head, ' 最大序号:', max(commit_seqs))

ok = True
# 1. 提交序号全局唯一
if len(commit_seqs) != len(set(commit_seqs)):
    ok = False; print('FAIL: 提交序号存在重复')
else:
    print('PASS: 提交序号全局唯一')

# 2. 序号连续无空洞（发号器每次 +1，含被接纳批单与登记版次）
if sorted(commit_seqs) != list(range(1, len(commit_seqs) + 1)):
    ok = False; print('FAIL: 提交序号不连续')
else:
    print('PASS: 提交序号从 1 连续')

# 3. 每配方版本号从 1 连续
bad_chain = [fid for fid, nos in per_formula.items()
             if sorted(nos) != list(range(1, len(nos) + 1))]
if bad_chain:
    ok = False; print('FAIL: 版本号不连续的配方', bad_chain)
else:
    print('PASS: 每个配方版本号从 1 连续，共', len(per_formula), '个配方')

# 4. 整单序号：每张批单的所有版本共用一个 commit_seq
order_seqs = {}
with engine.begin() as conn:
    for o in orders:
        seqs = {r[0] for r in conn.execute(
            select(formulas.c.commit_seq).where(
                formulas.c.source_order_id == o['order_id'])).all()}
        order_seqs[o['order_id']] = seqs
multi = {oid: s for oid, s in order_seqs.items() if len(s) != 1}
if multi:
    ok = False; print('FAIL: 存在多序号批单', multi)
else:
    print('PASS: 每张批单的版本共用一个提交序号')

# 5. 幂等：批单数 = 接纳批单数，且接纳批单都恰好产生 1 个版本
accepted = [o for o in orders if o['status'] == OrderStatus.ACCEPTED.value]
if len(accepted) != 30:
    ok = False; print('FAIL: 接纳批单数应为 30，实际', len(accepted))
else:
    print('PASS: 30 张并发批单全部接纳且重试未重复生效')

# 6. 水位 == 最大序号（每次接纳/登记恰好消耗一个序号；一批可写多行版本）
if head != max(commit_seqs):
    ok = False; print('FAIL: 水位与最大序号不一致')
else:
    print('PASS: 水位与最大提交序号一致')

# 7. 发号器下一个序号 = 水位 + 1（无重复发号、无空洞）
with engine.begin() as conn:
    next_seq = conn.execute(select(store.seq_counters.c.next_seq)
                            .where(store.seq_counters.c.name == store.COUNTER_NAME)).scalar_one()
if next_seq != head + 1:
    ok = False; print(f'FAIL: 发号器下一序号 {next_seq} 应为 {head + 1}')
else:
    print('PASS: 发号器下一序号正确，重试未多发号')

print()
print('并发验收', '全部通过。' if ok and not errors else '存在失败。')
sys.exit(0 if ok and not errors else 1)
