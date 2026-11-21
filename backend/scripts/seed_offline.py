"""离线改方模块的演示台账：配方版本链、活跃读快照与一张待收批单。

由 ``seed_demo`` 在账号与既有演示档案之后幂等写入；重复执行不产生重复版次。
"""
from sqlalchemy import select

from src.constants.workflow import ChangeOperation, OrderStatus
from src.models.tables import change_orders, formulas, read_snapshots
from src.services import offline_orders as store
from src.utils.time import utc_time

# (配方, 操作, 配方文本)；写入顺序即提交序号 1..7。
DEMO_VERSIONS = [
    ('GL-Q01', ChangeOperation.UPSERT, '青岚釉｜长石 62 g、石英 28 g、高岭土 10 g'),  # seq 1
    ('GL-Q01', ChangeOperation.UPSERT, '青岚釉｜长石 60 g、石英 30 g、高岭土 10 g'),  # seq 2
    ('GL-Q01', ChangeOperation.DELETE, None),                                          # seq 3
    ('GL-Q01', ChangeOperation.UPSERT, '青岚釉｜长石 58 g、石英 32 g、高岭土 10 g'),  # seq 4
    ('GL-H03', ChangeOperation.UPSERT, '褐砂釉｜长石 66 g、石英 22 g、高岭土 12 g'),  # seq 5
    ('GL-H03', ChangeOperation.UPSERT, '褐砂釉｜长石 64 g、石英 24 g、高岭土 12 g'),  # seq 6
    ('GL-M04', ChangeOperation.UPSERT, '蜜灰釉｜长石 55 g、石英 25 g、高岭土 20 g'),  # seq 7
]

# 快照在对应提交之后打开，固化该水位；旧快照之后续提交不可见。
DEMO_SNAPSHOTS = [
    ('snap-shiyan-30', '试烧-称料', 2),    # 读到 GL-Q01 的第二版
    ('snap-zhijian-45', '质检-复核', 3),   # 命中 GL-Q01 删除标记
    ('snap-yaowu-65', '窑务-入窑', 7),     # 读到全部现行版本
]

DEMO_ORDER = {
    'order_id': 'demo-offline-order-0001',
    'title': '窑红试样册·离线改方批单',
    'items': [
        {'formula_id': 'GL-Q01', 'base_version_no': 4,
         'base_recipe': '青岚釉｜长石 58 g、石英 32 g、高岭土 10 g',
         'operation': '写入', 'recipe': '青岚釉｜长石 56 g、石英 34 g、高岭土 10 g'},
        {'formula_id': 'GL-H03', 'base_version_no': 2,
         'base_recipe': '褐砂釉｜长石 64 g、石英 24 g、高岭土 12 g',
         'operation': '删除', 'recipe': None},
    ],
}


def seed_offline(conn):
    """幂等填充离线改方演示数据；台账已有版次则原样保留。"""
    store.ensure_counter(conn)
    if conn.execute(select(formulas.c.id).limit(1)).first():
        return False

    store._lock_counter(conn)
    for formula_id, operation, recipe in DEMO_VERSIONS:
        commit_seq = store._next_commit_seq(conn)
        store._append_version(conn, formula_id, operation, recipe, None, commit_seq)

    for code, label, read_seq in DEMO_SNAPSHOTS:
        conn.execute(read_snapshots.insert().values(
            snapshot_code=code, read_seq=read_seq, label=label,
            opened_by='demo', opened_at=utc_time()))

    head = store.current_head(conn)
    conn.execute(change_orders.insert().values(
        order_id=DEMO_ORDER['order_id'], title=DEMO_ORDER['title'],
        status=OrderStatus.PENDING, base_seq=head, commit_seq=None,
        conflicts=None, items=DEMO_ORDER['items'],
        created_by='demo', created_at=utc_time()))
    return True
