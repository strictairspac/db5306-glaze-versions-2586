"""离线改方模块的事务边界：每个写操作一个数据库事务。"""

from fastapi import HTTPException

from src.config.settings import engine
from src.models.tables import read_snapshots
from src.schemas.workflow import ChangeOrderForm, SnapshotForm, VersionForm
from src.services import offline_orders as store
from src.services.workflow import log


def archive_overview():
    with engine.connect() as conn:
        return store.archive_view(conn)


def list_formula_chain(formula_id: str):
    with engine.connect() as conn:
        chain = store.formula_versions(conn, formula_id)
        if not chain:
            raise HTTPException(404, '找不到该配方')
        head = store.current_head(conn)
        current = store.read_at(conn, formula_id, head)
        return {'formula_id': formula_id, 'head_seq': head,
                'current': current, 'chain': chain}


def register_formula_version(formula_id: str, form: VersionForm, actor: str):
    """档案页直接登记一个配方版次（模拟别组在台账上的并发写入来源）。"""
    formula_id = formula_id.strip()
    if not formula_id:
        raise HTTPException(422, '配方编号不能为空')
    with engine.begin() as conn:
        # 与批单收单共用同一把发号器行锁，保证全局提交序号连续不重号。
        store.ensure_counter(conn)
        store._lock_counter(conn)
        commit_seq = store._next_commit_seq(conn)
        version_no = store._append_version(
            conn, formula_id, form.operation, form.recipe, None, commit_seq)
        log(conn, actor, 'register_version', f'{formula_id}#v{version_no}')
        return {'formula_id': formula_id, 'version_no': version_no,
                'commit_seq': commit_seq}


def open_read_snapshot(form: SnapshotForm, actor: str):
    with engine.begin() as conn:
        return store.open_snapshot(conn, form, actor)


def snapshots():
    with engine.connect() as conn:
        return [dict(row) for row in conn.execute(
            read_snapshots.select().order_by(read_snapshots.c.id)).mappings()]


def create_change_order(form: ChangeOrderForm, actor: str):
    with engine.begin() as conn:
        return store.create_order(conn, form, actor)


def receive_change_order(order_id: str, actor: str):
    """整单收单在一个事务里：冲突则整单退回，无冲突则整单共用一个提交序号。

    返回 ``(detail, newly_accepted)``。已收过的单重试时 ``newly_accepted=False``，
    路由据此对「首次退回」给 409，对「已接纳单重试」幂等回传 200。
    """
    with engine.begin() as conn:
        return store.receive_order(conn, order_id.strip(), actor)


def raise_rejected(detail):
    # 409 表达“整单退回”，响应体带完整冲突明细；批单状态已落为 rejected。
    raise HTTPException(409, {
        'message': '批单存在冲突，已整单退回',
        'order': detail,
        'conflicts': detail['conflicts'],
    })


def retry_change_order(order_id: str, actor: str):
    """中断重试：同一单号只回传首次收单结论，绝不重复发号或重复写版本。"""
    with engine.begin() as conn:
        order = store.order_detail(conn, order_id.strip())
        if order['status'] == 'pending':
            order, _ = store.receive_order(conn, order_id.strip(), actor)
    return order


def list_change_orders():
    with engine.connect() as conn:
        return store.list_orders(conn)


def get_change_order(order_id: str):
    with engine.connect() as conn:
        return store.order_detail(conn, order_id.strip())
