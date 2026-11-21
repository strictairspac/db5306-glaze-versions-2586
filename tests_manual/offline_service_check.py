"""离线改方模块的服务层验收：SQLite 内存库，仅用 stdlib + 项目源码。

运行：PYTHONPATH=/tmp/pyenv/pkgs:/workspace/backend python3 -I tests_manual/offline_service_check.py
"""
import os
import sys
import tempfile
import threading
import traceback

sys.path.insert(0, '/tmp/pyenv/pkgs')
sys.path.insert(0, '/workspace/backend')

os.environ.setdefault('AUTH_KEY', 'test-key')

from sqlalchemy import create_engine, event
from sqlalchemy.pool import QueuePool

import src.models.tables as tables
from src.constants.workflow import ChangeOperation, OrderStatus
from src.schemas.workflow import (ChangeOrderForm, OrderItemForm, SnapshotForm,
                                  VersionForm)
from src.services import offline_orders as store

# 文件 SQLite + 独立连接池：WAL 下写事务取库级写锁互斥，
# 让多线程并发事务各持连接、在发号器上串行，接近 MySQL 多连接模型。
_db_fd, _db_path = tempfile.mkstemp(suffix='.db', prefix='offline-svc-')
os.close(_db_fd)
engine = create_engine(
    f'sqlite+pysqlite:///{_db_path}',
    poolclass=QueuePool, pool_size=8, max_overflow=0,
    connect_args={'check_same_thread': False, 'timeout': 30})


@event.listens_for(engine, 'connect')
def _pragmas(dbapi_conn, _record):
    dbapi_conn.execute('PRAGMA journal_mode=WAL')
    dbapi_conn.execute('PRAGMA busy_timeout=30000')
    # 让 DBAPI 不自动开事务，由 SQLAlchemy 显式 BEGIN IMMEDIATE 控制。
    dbapi_conn.isolation_level = None


@event.listens_for(engine, 'begin')
def _begin_immediate(conn):
    # 仅根事务（驱动尚未在事务中）发一次 IMMEDIATE，嵌套事务走 SAVEPOINT。
    if not conn.connection.dbapi_connection.in_transaction:
        conn.exec_driver_sql('BEGIN IMMEDIATE')


# 让服务模块使用本测试引擎（store 本身不持有引擎，引擎由调用方传入）
tables.schema.create_all(engine)
failures = []


def check(name, condition, detail=''):
    status = 'PASS' if condition else 'FAIL'
    print(f'[{status}] {name}' + (f' — {detail}' if detail and not condition else ''))
    if not condition:
        failures.append(name)


def item(formula_id, base_no, base_recipe, operation, recipe=None):
    return OrderItemForm(formula_id=formula_id, base_version_no=base_no,
                         base_recipe=base_recipe, operation=operation, recipe=recipe)


def order(order_id, items, title='离线批单'):
    return ChangeOrderForm(order_id=order_id, title=title, items=items)


with engine.begin() as conn:
    store.ensure_counter(conn)

# ---------------------------------------------------------------- 场景准备
# 档案初始：GL-Q01 两个版次（序号 1、2），GL-H03 一个版次（序号 3）
with engine.begin() as conn:
    store._lock_counter(conn)
    v1 = store._append_version(conn, 'GL-Q01', ChangeOperation.UPSERT,
                               '青岚釉 v1 长石62', None, store._next_commit_seq(conn))
    v2 = store._append_version(conn, 'GL-Q01', ChangeOperation.UPSERT,
                               '青岚釉 v2 长石60', None, store._next_commit_seq(conn))
    v3 = store._append_version(conn, 'GL-H03', ChangeOperation.UPSERT,
                               '褐砂釉 v1 长石66', None, store._next_commit_seq(conn))
check('初始版次号连续', (v1, v2, v3) == (1, 2, 1))
with engine.begin() as conn:
    check('初始水位=3', store.current_head(conn) == 3)
    q01 = store.current_version(conn, 'GL-Q01')
check('GL-Q01 现行 v2', q01['version_no'] == 2 and q01['recipe'] == '青岚釉 v2 长石60')

# 打开读快照 S1，固化水位 3
with engine.begin() as conn:
    snap = store.open_snapshot(conn, SnapshotForm(snapshot_code='S1', label='试烧-称料'), 'operator')
check('快照水位固化为 3', snap['read_seq'] == 3)

# ------------------------------------------------ 1. 无冲突整单接纳，共用序号
form = order('11111111-1111-1111-1111-111111111111', [
    item('GL-Q01', 2, '青岚釉 v2 长石60', '写入', '青岚釉 v3 长石58'),
    item('GL-H03', 1, '褐砂釉 v1 长石66', '写入', '褐砂釉 v2 长石64'),
])
with engine.begin() as conn:
    store.create_order(conn, form, 'operator')
with engine.begin() as conn:
    detail, accepted = store.receive_order(conn, form.order_id, 'operator')
check('无冲突批单被接纳', accepted and detail['status'] == OrderStatus.ACCEPTED)
check('整单共用一个提交序号(=4)', detail['commit_seq'] == 4)
with engine.begin() as conn:
    q01_now = store.current_version(conn, 'GL-Q01')
    h03_now = store.current_version(conn, 'GL-H03')
check('GL-Q01 升到 v3 且序号 4', q01_now['version_no'] == 3 and q01_now['commit_seq'] == 4)
check('GL-H03 升到 v2 且同序号 4', h03_now['version_no'] == 2 and h03_now['commit_seq'] == 4)
with engine.begin() as conn:
    check('版本链保留历史(3+2条)',
          len(store.formula_versions(conn, 'GL-Q01')) == 3
          and len(store.formula_versions(conn, 'GL-H03')) == 2)

# ------------------------------------------------ 2. 已打开快照仍读旧配方
with engine.begin() as conn:
    old_read = store.read_at(conn, 'GL-Q01', 3)
    new_read = store.read_at(conn, 'GL-Q01', 4)
check('快照 S1(水位3) 仍读到 v2 旧配方',
      old_read['version_no'] == 2 and old_read['recipe'] == '青岚釉 v2 长石60')
check('水位4 读到新 v3', new_read['version_no'] == 3 and new_read['recipe'] == '青岚釉 v3 长石58')
with engine.begin() as conn:
    overview = store.archive_view(conn)
s1_rows = [r for r in overview['snapshot_reads'] if r['snapshot_code'] == 'S1']
check('档案总览含逐快照水位读', len(s1_rows) == 2 and
      all(r['hit_version_no'] == (2 if r['formula_id'] == 'GL-Q01' else 1) for r in s1_rows))

# ------------------------------------------------ 3. 改过又改回也算冲突
# 别组把 GL-Q01 v3 改成 v4 再改回相同文本 v5（档案登记，序号 5、6）
with engine.begin() as conn:
    store.ensure_counter(conn)
    store._lock_counter(conn)
    store._append_version(conn, 'GL-Q01', ChangeOperation.UPSERT, '临时配方', None,
                          store._next_commit_seq(conn))
    store._append_version(conn, 'GL-Q01', ChangeOperation.UPSERT, '青岚釉 v3 长石58', None,
                          store._next_commit_seq(conn))
# 离线单基线仍是 v3（文本与现行 v5 相同）
form2 = order('22222222-2222-2222-2222-222222222222', [
    item('GL-Q01', 3, '青岚釉 v3 长石58', '写入', '青岚釉 v6 长石56'),
])
with engine.begin() as conn:
    store.create_order(conn, form2, 'operator')
with engine.begin() as conn:
    detail2, accepted2 = store.receive_order(conn, form2.order_id, 'operator')
check('改过又改回判冲突并整单退回', not accepted2 and detail2['status'] == OrderStatus.REJECTED)
check('退回给出冲突配方', [c['formula_id'] for c in detail2['conflicts']] == ['GL-Q01'])
check('冲突理由点明改回', '改回' in detail2['conflicts'][0]['reason'])
with engine.begin() as conn:
    check('退回后水位不前推(仍6)', store.current_head(conn) == 6)
    check('退回不写新版本(仍5条)', len(store.formula_versions(conn, 'GL-Q01')) == 5)

# ------------------------------------------------ 4. 一个冲突整单退回，且列出全部冲突
with engine.begin() as conn:
    store._lock_counter(conn)
    store._append_version(conn, 'GL-H03', ChangeOperation.UPSERT, '褐砂釉 v3', None,
                          store._next_commit_seq(conn))
form3 = order('33333333-3333-3333-3333-333333333333', [
    item('GL-Q01', 3, '青岚釉 v3 长石58', '写入', 'x'),   # 冲突：现行 v5
    item('GL-H03', 2, '褐砂釉 v2 长石64', '写入', 'y'),   # 冲突：现行 v3
])
with engine.begin() as conn:
    store.create_order(conn, form3, 'operator')
with engine.begin() as conn:
    detail3, accepted3 = store.receive_order(conn, form3.order_id, 'operator')
check('多冲突整单退回', not accepted3 and detail3['status'] == OrderStatus.REJECTED)
check('列出全部冲突配方',
      sorted(c['formula_id'] for c in detail3['conflicts']) == ['GL-H03', 'GL-Q01'])
with engine.begin() as conn:
    head_after_reject = store.current_head(conn)
check('退回不消耗提交序号(水位仍7)', head_after_reject == 7)

# ------------------------------------------------ 5. 重试幂等：不重复生效
# 5a 已退回单重试：仍是退回，无新版本
# 5a 已退回单重试：仍是退回，无新版本
with engine.begin() as conn:
    before = len(store.formula_versions(conn, 'GL-Q01'))
with engine.begin() as conn:
    again3, accepted3b = store.receive_order(conn, form3.order_id, 'operator')
with engine.begin() as conn:
    after = len(store.formula_versions(conn, 'GL-Q01'))
check('退回单重试保持退回', not accepted3b and again3['status'] == OrderStatus.REJECTED)
check('重试不产生新版本', before == after)

# 5b 已接纳单重试：回传接纳结论，序号不变、版本不增（本次未重复生效，布尔为 False）
with engine.begin() as conn:
    again1, accepted1b = store.receive_order(conn, form.order_id, 'operator')
check('接纳单重试回传接纳且序号不变',
      not accepted1b and again1['status'] == OrderStatus.ACCEPTED.value
      and again1['commit_seq'] == 4)
with engine.begin() as conn:
    check('接纳单重试不增版本', len(store.formula_versions(conn, 'GL-Q01')) == 5)

# 5c 重试入口（模拟中断后只拿到单号）：pending 才收，否则回传首次结论
with engine.begin() as conn:
    retry_detail = store.order_detail(conn, form.order_id)
    if retry_detail['status'] == 'pending':
        retry_detail, _ = store.receive_order(conn, form.order_id, 'operator')
check('重试接口幂等回传', retry_detail['commit_seq'] == 4 and retry_detail['status'] == 'accepted')

# ------------------------------------------------ 6. 并发收单不重号、不脏写
# 两张待收单都基于当前无冲突的不同新配方，并发收单
form_a = order('44444444-4444-4444-4444-444444444444', [
    item('GL-NEW-A', 0, None, '写入', '新配方A'),
])
form_b = order('55555555-5555-5555-5555-555555555555', [
    item('GL-NEW-B', 0, None, '写入', '新配方B'),
])
with engine.begin() as conn:
    store.create_order(conn, form_a, 'operator')
    store.create_order(conn, form_b, 'operator')
results = {}
errors = {}


def receive(order_id, key):
    try:
        with engine.begin() as conn:
            results[key] = store.receive_order(conn, order_id, 'operator')
    except Exception as exc:  # noqa: BLE001
        errors[key] = repr(exc)


t1 = threading.Thread(target=receive, args=(form_a.order_id, 'A'))
t2 = threading.Thread(target=receive, args=(form_b.order_id, 'B'))
t1.start(); t2.start(); t1.join(); t2.join()
seqs = sorted(detail['commit_seq'] for detail, _ in results.values())
check('并发收单均成功', len(results) == 2 and not errors, str(errors))
check('并发提交序号连续不重号', seqs == [8, 9], str(seqs))
with engine.begin() as conn:
    check('并发后水位=9', store.current_head(conn) == 9)
    check('新配方各一版次',
          store.current_version(conn, 'GL-NEW-A')['version_no'] == 1
          and store.current_version(conn, 'GL-NEW-B')['version_no'] == 1)

# ------------------------------------------------ 7. 同配方并发：一单接纳后另一单必冲突
form_c = order('66666666-6666-6666-6666-666666666666', [
    item('GL-RACE', 0, None, '写入', 'C 先写'),
])
form_d = order('77777777-7777-7777-7777-777777777777', [
    item('GL-RACE', 0, None, '写入', 'D 也写'),
])
with engine.begin() as conn:
    store.create_order(conn, form_c, 'operator')
    store.create_order(conn, form_d, 'operator')
r = {}


def receive2(order_id, key):
    with engine.begin() as conn:
        r[key] = store.receive_order(conn, order_id, 'operator')


t1 = threading.Thread(target=receive2, args=(form_c.order_id, 'C'))
t2 = threading.Thread(target=receive2, args=(form_d.order_id, 'D'))
t1.start(); t2.start(); t1.join(); t2.join()
statuses = {k: v[1] for k, v in r.items()}
check('同配方并发恰好一单接纳', sorted(statuses.values()) == [False, True], str(statuses))
loser = next(k for k, ok in statuses.items() if not ok)
check('落败单整单退回并点名冲突配方',
      r[loser][0]['conflicts'][0]['formula_id'] == 'GL-RACE')

# ------------------------------------------------ 8. 旧档案可重开核对
with engine.begin() as conn:
    chain = store.formula_versions(conn, 'GL-Q01')
    check('GL-Q01 全量历史可查(5版次)', len(chain) == 5)
    check('历史版次提交序号完整', [row['commit_seq'] for row in chain] == [1, 2, 4, 5, 6])
    check('可按任意历史水位回放',
          store.read_at(conn, 'GL-Q01', 1)['recipe'] == '青岚釉 v1 长石62'
          and store.read_at(conn, 'GL-Q01', 2)['version_no'] == 2
          and store.read_at(conn, 'GL-Q01', 3)['version_no'] == 2)  # 序号3属于别的配方

# 删除标记的快照读
with engine.begin() as conn:
    store._lock_counter(conn)
    del_seq = store._next_commit_seq(conn)
    store._append_version(conn, 'GL-H03', ChangeOperation.DELETE, None, None, del_seq)
    hit_del = store.read_at(conn, 'GL-H03', del_seq)
    hit_before = store.read_at(conn, 'GL-H03', del_seq - 1)
check('删除标记水位读命中已删除', hit_del['operation'] == '删除' and hit_del['recipe'] is None)
check('删除前水位仍读到旧配方', hit_before['recipe'] is not None)

# 新建配方（基线0）被别人抢先创建 → 冲突
# GL-NEW-A 在并发场景已被建为 v1；离线单若仍以基线 0 领单，收单必判冲突
form_e = order('88888888-8888-8888-8888-888888888888', [
    item('GL-NEW-A', 0, None, '写入', '离线改 A'),
])
with engine.begin() as conn:
    store.create_order(conn, form_e, 'operator')
with engine.begin() as conn:
    detail_e, accepted_e = store.receive_order(conn, form_e.order_id, 'operator')
check('基线0配方被抢先新建判冲突并点名',
      not accepted_e and detail_e['status'] == OrderStatus.REJECTED.value
      and detail_e['conflicts'][0]['formula_id'] == 'GL-NEW-A'
      and detail_e['conflicts'][0]['current_version_no'] == 1)

print()
if failures:
    print(f'共 {len(failures)} 项失败：{failures}')
    sys.exit(1)
print('全部验收通过。')
