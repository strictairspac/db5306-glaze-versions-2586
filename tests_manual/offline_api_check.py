"""HTTP 级验收：FastAPI TestClient + SQLite，覆盖鉴权、收单、409 冲突与幂等。

运行：python3 -I tests_manual/offline_api_check.py
"""
import os
import sys

sys.path.insert(0, '/tmp/pyenv/pkgs')
sys.path.insert(0, '/workspace/backend')

os.environ['AUTH_KEY'] = 'test-key'
os.environ['DB_DRIVER'] = 'sqlite+pysqlite'
os.environ['DB_NAME'] = ':memory:'
os.environ['DB_USER'] = ''
os.environ['DB_PASSWORD'] = ''
os.environ['DB_HOST'] = ''
os.environ['REDIS_URL'] = 'redis://127.0.0.1:6399/0'
os.environ['SEED_DEMO'] = 'false'

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, event  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

import src.config.settings as settings  # noqa: E402

test_engine = create_engine(
    'sqlite+pysqlite:///:memory:',
    connect_args={'check_same_thread': False}, poolclass=StaticPool)


@event.listens_for(test_engine, 'connect')
def _pragmas(dbapi_conn, _record):
    dbapi_conn.isolation_level = None


@event.listens_for(test_engine, 'begin')
def _begin_immediate(conn):
    if not conn.connection.dbapi_connection.in_transaction:
        conn.exec_driver_sql('BEGIN IMMEDIATE')


settings.engine = test_engine
# cache 只在 /health 用到，本测试不调 /health
from src.main import app  # noqa: E402
from src.models.tables import schema  # noqa: E402
from src.services import offline_orders as store  # noqa: E402

schema.create_all(test_engine)
with test_engine.begin() as conn:
    store.ensure_counter(conn)

client = TestClient(app)
failures = []


def check(name, condition, detail=''):
    tag = 'PASS' if condition else 'FAIL'
    print(f'[{tag}] {name}' + (f' — {detail}' if detail and not condition else ''))
    if not condition:
        failures.append(name)


def login(role):
    # 直接造一个该角色的账号并换取 token
    from src.models.tables import users
    from src.services.security import hash_password
    from src.utils.time import utc_time
    with test_engine.begin() as conn:
        conn.execute(users.insert().values(
            username=role, password_hash=hash_password('pw123456'), role=role))
    resp = client.post('/login', json={'username': role, 'password': 'pw123456'})
    assert resp.status_code == 200, resp.text
    return {'Authorization': f"Bearer {resp.json()['token']}"}


op = login('operator')
viewer = login('viewer')

# 鉴权：未登录 401
check('未登录访问档案被拒', client.get('/offline/archive').status_code == 401)

# 观察员只读：写操作 403
r = client.post('/offline/snapshots', headers=viewer,
                json={'snapshot_code': 'S1', 'label': 'x'})
check('观察员不能打开快照(403)', r.status_code == 403)

# 经办员登记初始版次
r = client.post('/offline/formulas/GL-Q01/versions', headers=op,
                json={'operation': '写入', 'recipe': '青岚釉 v1'})
check('登记版次 201', r.status_code == 201 and r.json()['version_no'] == 1
      and r.json()['commit_seq'] == 1, r.text)
r = client.post('/offline/formulas/GL-Q01/versions', headers=op,
                json={'operation': '写入', 'recipe': '青岚釉 v2'})
check('第二版次序号 2', r.json()['commit_seq'] == 2 and r.json()['version_no'] == 2)

# 写入空配方被 Pydantic 拒绝
r = client.post('/offline/formulas/GL-Q01/versions', headers=op,
                json={'operation': '写入', 'recipe': '  '})
check('空配方 422', r.status_code == 422)
# 删除带配方被拒
r = client.post('/offline/formulas/GL-Q01/versions', headers=op,
                json={'operation': '删除', 'recipe': '不该有'})
check('删除带配方 422', r.status_code == 422)

# 打开快照固化水位 2
r = client.post('/offline/snapshots', headers=op,
                json={'snapshot_code': 'S1', 'label': '试烧-称料'})
check('打开快照水位=2', r.status_code == 201 and r.json()['read_seq'] == 2)

# 领单
order_id = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'
r = client.post('/offline/orders', headers=op, json={
    'order_id': order_id, 'title': '离线改方单',
    'items': [{'formula_id': 'GL-Q01', 'base_version_no': 2,
               'base_recipe': '青岚釉 v2', 'operation': '写入', 'recipe': '青岚釉 v3'}]})
check('领单 201 待收', r.status_code == 201 and r.json()['status'] == 'pending')

# 重复领同一单号 409
r = client.post('/offline/orders', headers=op, json={
    'order_id': order_id, 'title': '重复',
    'items': [{'formula_id': 'GL-Q01', 'base_version_no': 2,
               'operation': '写入', 'recipe': 'x'}]})
check('重复单号 409', r.status_code == 409)

# 收单：无冲突接纳，整单一个序号
r = client.post(f'/offline/orders/{order_id}/receive', headers=op)
check('收单接纳 200 且序号 3', r.status_code == 200 and r.json()['commit_seq'] == 3)

# 中断重试：回传接纳，序号仍是 3，不重复生效
r = client.post('/offline/orders/retry', headers=op, json={'order_id': order_id})
check('重试幂等回传序号 3', r.status_code == 200 and r.json()['commit_seq'] == 3
      and r.json()['status'] == 'accepted')
r = client.post(f'/offline/orders/{order_id}/receive', headers=op)
check('直接再收单仍幂等 200', r.status_code == 200 and r.json()['commit_seq'] == 3)

# 旧快照仍读旧配方
r = client.get('/offline/archive', headers=op)
data = r.json()
s1 = [x for x in data['snapshot_reads'] if x['snapshot_code'] == 'S1'
      and x['formula_id'] == 'GL-Q01'][0]
check('快照 S1 仍读 v2 旧配方', s1['hit_version_no'] == 2 and s1['recipe'] == '青岚釉 v2')
check('档案现行已是 v3', data['head_seq'] == 3
      and data['formulas'][0]['current_version_no'] == 3)

# 版本链可重开核对
r = client.get('/offline/formulas/GL-Q01', headers=op)
chain = r.json()['chain']
check('版本链完整 3 版', len(chain) == 3
      and [c['commit_seq'] for c in chain] == [1, 2, 3]
      and chain[2]['source_order_id'] == order_id)

# 冲突场景：别组先改（v4），离线单基线仍是 v3
client.post('/offline/formulas/GL-Q01/versions', headers=op,
            json={'operation': '写入', 'recipe': '别组改动 v4'})
conflict_order = 'cccccccc-cccc-cccc-cccc-cccccccccccc'
client.post('/offline/orders', headers=op, json={
    'order_id': conflict_order, 'title': '冲突单',
    'items': [{'formula_id': 'GL-Q01', 'base_version_no': 3,
               'base_recipe': '青岚釉 v3', 'operation': '写入', 'recipe': '离线 v5'}]})
r = client.post(f'/offline/orders/{conflict_order}/receive', headers=op)
check('冲突整单退回 409', r.status_code == 409)
body = r.json()['detail']
check('409 指出冲突配方', body['conflicts'][0]['formula_id'] == 'GL-Q01'
      and body['order']['status'] == 'rejected')
# 退回单可查询、重试仍退回
r = client.post('/offline/orders/retry', headers=op, json={'order_id': conflict_order})
check('退回单重试仍 rejected 且不发号', r.json()['status'] == 'rejected'
      and r.json()['commit_seq'] is None)
r = client.get('/offline/archive', headers=op)
check('退回后水位仍 4', r.json()['head_seq'] == 4)

# 批单列表
r = client.get('/offline/orders', headers=op)
statuses = {o['order_id']: o['status'] for o in r.json()}
check('批单列表状态正确',
      statuses[order_id] == 'accepted' and statuses[conflict_order] == 'rejected')

print()
if failures:
    print(f'{len(failures)} 项失败：{failures}')
    sys.exit(1)
print('HTTP 验收全部通过。')
