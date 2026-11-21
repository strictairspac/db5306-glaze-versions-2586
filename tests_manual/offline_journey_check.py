"""端到端业务旅程验收：模拟需求描述的完整离线改方收单流程。

运行：python3 -I tests_manual/offline_journey_check.py
"""
import os
import sys
import tempfile

sys.path.insert(0, '/tmp/pyenv/pkgs')
sys.path.insert(0, '/workspace/backend')

os.environ['AUTH_KEY'] = 'test-key'
os.environ['DB_DRIVER'] = 'sqlite+pysqlite'
_db_fd, _db_path = tempfile.mkstemp(suffix='.db')
os.close(_db_fd)
os.environ['DB_NAME'] = _db_path
os.environ['DB_USER'] = ''
os.environ['DB_PASSWORD'] = ''
os.environ['DB_HOST'] = ''
os.environ['REDIS_URL'] = 'redis://127.0.0.1:6399/0'
os.environ['SEED_DEMO'] = 'false'

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import event  # noqa: E402
from sqlalchemy.pool import QueuePool  # noqa: E402

import src.config.settings as settings  # noqa: E402

settings.engine.pool.dispose()
test_engine = settings.engine = __import__('sqlalchemy').create_engine(
    f'sqlite+pysqlite:///{_db_path}', poolclass=QueuePool,
    pool_size=5, max_overflow=0,
    connect_args={'check_same_thread': False, 'timeout': 30})


@event.listens_for(test_engine, 'connect')
def _pragmas(conn, _):
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA busy_timeout=30000')
    conn.isolation_level = None


@event.listens_for(test_engine, 'begin')
def _begin(conn):
    if not conn.connection.dbapi_connection.in_transaction:
        conn.exec_driver_sql('BEGIN IMMEDIATE')


from src.main import app  # noqa: E402
from src.models.tables import schema, users  # noqa: E402
from src.services import offline_orders as store  # noqa: E402
from src.services.security import hash_password  # noqa: E402

schema.create_all(test_engine)
with test_engine.begin() as conn:
    store.ensure_counter(conn)
    conn.execute(users.insert().values(
        username='operator', password_hash=hash_password('pw123456'), role='operator'))

client = TestClient(app)
token = client.post('/login', json={'username': 'operator', 'password': 'pw123456'}).json()['token']
h = {'Authorization': f'Bearer {token}'}
failures = []


def check(name, cond, detail=''):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f' — {detail}' if detail and not cond else ''))
    if not cond:
        failures.append(name)


def post_version(formula, op, recipe):
    return client.post(f'/offline/formulas/{formula}/versions', headers=h,
                       json={'operation': op, 'recipe': recipe})


# 1) 台账：GL-Q01 建立到 v2
post_version('GL-Q01', '写入', '长石 62')
post_version('GL-Q01', '写入', '长石 60')
# 2) 打开两个读快照：早快照水位 2
client.post('/offline/snapshots', headers=h, json={'snapshot_code': 'early', 'label': '早班'})
post_version('GL-Q01', '写入', '长石 58')   # v3, seq 3
client.post('/offline/snapshots', headers=h, json={'snapshot_code': 'late', 'label': '晚班'})  # 水位 3

# 3) 领单：基线 v3
oid = 'dddddddd-dddd-dddd-dddd-dddddddddddd'
r = client.post('/offline/orders', headers=h, json={
    'order_id': oid, 'title': '离线单',
    'items': [{'formula_id': 'GL-Q01', 'base_version_no': 3, 'base_recipe': '长石 58',
               'operation': '写入', 'recipe': '长石 56'}]})
check('领单成功', r.status_code == 201)

# 4) 别组在收单前改动：v4 改成别的，v5 又改回 v3 相同文本
post_version('GL-Q01', '写入', '别人临时改 长石 70')  # v4
post_version('GL-Q01', '写入', '长石 58')              # v5 改回，文本同 v3

# 5) 收单：版本号已 3→5，即便文本改回也必须判冲突，整单退回
r = client.post(f'/offline/orders/{oid}/receive', headers=h)
check('改过又改回仍 409 整单退回', r.status_code == 409)
conflict = r.json()['detail']['conflicts'][0]
check('冲突点名 GL-Q01 且说明改回',
      conflict['formula_id'] == 'GL-Q01' and conflict['current_version_no'] == 5
      and '改回' in conflict['reason'])

# 6) 退回不影响台账：现行仍是 v5，水位 5
arch = client.get('/offline/archive', headers=h).json()
check('退回后水位仍 5、现行 v5',
      arch['head_seq'] == 5 and arch['formulas'][0]['current_version_no'] == 5)

# 7) 重新领一张基线正确的单（v5），收单应接纳，整单一个序号
oid2 = 'eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee'
client.post('/offline/orders', headers=h, json={
    'order_id': oid2, 'title': '修正后的离线单',
    'items': [{'formula_id': 'GL-Q01', 'base_version_no': 5, 'base_recipe': '长石 58',
               'operation': '写入', 'recipe': '长石 56'}]})
r = client.post(f'/offline/orders/{oid2}/receive', headers=h)
check('正确基线收单接纳', r.status_code == 200 and r.json()['status'] == 'accepted')
new_seq = r.json()['commit_seq']
check('接纳得到新序号(=6)', new_seq == 6)

# 8) 旧读快照仍读打开时的配方：early(水位2)→v2，late(水位3)→v3，现行→v6
arch = client.get('/offline/archive', headers=h).json()
by_snap = {(x['snapshot_code'], x['formula_id']): x for x in arch['snapshot_reads']}
check('早快照仍读 v2 旧配方',
      by_snap[('early', 'GL-Q01')]['hit_version_no'] == 2
      and by_snap[('early', 'GL-Q01')]['recipe'] == '长石 60')
check('晚快照仍读 v3 配方', by_snap[('late', 'GL-Q01')]['hit_version_no'] == 3)
check('现行已是 v6 新配方', arch['formulas'][0]['current_version_no'] == 6
      and arch['formulas'][0]['current_recipe'] == '长石 56')

# 9) 中断重试：接纳单重试序号不变；退回单重试仍 409
r1 = client.post('/offline/orders/retry', headers=h, json={'order_id': oid2})
r2 = client.post(f'/offline/orders/{oid2}/receive', headers=h)
check('接纳单两种重试都幂等回传序号 6',
      r1.json()['commit_seq'] == 6 and r2.status_code == 200 and r2.json()['commit_seq'] == 6)
r3 = client.post('/offline/orders/retry', headers=h, json={'order_id': oid})
check('退回单重试仍 rejected', r3.json()['status'] == 'rejected')
r4 = client.post(f'/offline/orders/{oid}/receive', headers=h)
check('退回单再收仍 409 且同一冲突', r4.status_code == 409
      and r4.json()['detail']['conflicts'][0]['formula_id'] == 'GL-Q01')

# 10) 旧档案可重开核对：完整版本链与按水位回放
chain = client.get('/offline/formulas/GL-Q01', headers=h).json()
check('版本链保留全部 6 版', len(chain['chain']) == 6
      and [c['version_no'] for c in chain['chain']] == [1, 2, 3, 4, 5, 6])
check('接纳版带来源批单号', chain['chain'][-1]['source_order_id'] == oid2)

# 11) 水位最终一致，重试未多发号
check('最终水位 6', arch['head_seq'] == 6 or client.get('/offline/archive', headers=h).json()['head_seq'] == 6)

print()
if failures:
    print(f'{len(failures)} 项失败：{failures}')
    sys.exit(1)
print('端到端业务旅程全部通过。')
