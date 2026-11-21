"""离线改方批单：乐观并发收单、整单原子提交与版本链快照读。

并发模型（MySQL 8.0 InnoDB，本地用 SQLite 同语义验证）：

* ``glaze_seq_counters`` 单行行锁串行化发号，整单共用一个提交序号且不重号；
* 接纳在单个事务内完成「锁单 → 复检基线 → 发号 → 整单追加版本 → 落单」，
  别的组并发写入只会让版本号前进，从而被基线复检判为冲突，不会脏写；
* 版本链只追加不覆盖，读快照按打开时固化的水位命中最后一条
  ``commit_seq <= read_seq`` 的版次，旧快照永远读到旧配方；
* ``order_id`` 是领单方生成的幂等键：收单结果（接纳或退回）只落一次，
  中断后重试同一单直接返回首次结果，不会重复发号或重复写版本。
"""

from bisect import bisect_right

from fastapi import HTTPException
from sqlalchemy import select

from src.constants.workflow import ChangeOperation, OrderStatus
from src.models.tables import (audit, change_orders, formulas, read_snapshots,
                               seq_counters)
from src.utils.time import utc_time

COUNTER_NAME = 'glaze_commit_seq'


# ---------------------------------------------------------------------------
# 只读视图：版本链与快照读
# ---------------------------------------------------------------------------
def current_head(conn) -> int:
    """已提交水位 = 版本链上最大的提交序号，没有任何提交时为 0。"""
    rows = conn.execute(select(formulas.c.commit_seq)).all()
    return max((row[0] for row in rows), default=0)


def formula_versions(conn, formula_id: str, for_update: bool = False) -> list[dict]:
    """单个配方按版本号排序的完整版本链（含已被覆盖的历史版次）。

    ``for_update=True`` 仅在 MySQL 下追加 ``FOR UPDATE``：收单复检用当前读，
    既看到并发事务已提交的最新版次（RR 下普通 SELECT 是快照读），又锁住该
    配方的版本行，阻止别的组在复检与写入之间插入新版次。
    """
    statement = (
        select(formulas.c.version_no, formulas.c.commit_seq, formulas.c.operation,
               formulas.c.recipe, formulas.c.source_order_id)
        .where(formulas.c.formula_id == formula_id)
        .order_by(formulas.c.version_no))
    if for_update and _is_mysql(conn):
        statement = statement.with_for_update()
    rows = conn.execute(statement).mappings().all()
    return [dict(row) for row in rows]


def current_version(conn, formula_id: str, for_update: bool = False) -> dict | None:
    """配方的现行版本（版本号最大的一条）。"""
    chain = formula_versions(conn, formula_id, for_update=for_update)
    return chain[-1] if chain else None


def read_at(conn, formula_id: str, read_seq: int) -> dict | None:
    """水位读：命中该配方最后一条 commit_seq <= read_seq 的版次。

    只追加的版本链保证已打开的读快照不受后续提交影响——这是
    “已打开的读快照仍读原来的配方”的落点。
    """
    chain = formula_versions(conn, formula_id)
    boundary = bisect_right([row['commit_seq'] for row in chain], read_seq)
    return chain[boundary - 1] if boundary else None


def archive_view(conn) -> dict:
    """档案页总览：现行配方、版本链与读快照，供重开核对。"""
    formula_ids = [row[0] for row in conn.execute(
        select(formulas.c.formula_id).distinct().order_by(formulas.c.formula_id))]
    head = current_head(conn)
    formulas_view = []
    for formula_id in formula_ids:
        chain = formula_versions(conn, formula_id)
        current = chain[-1]
        formulas_view.append({
            'formula_id': formula_id,
            'current_version_no': current['version_no'],
            'current_commit_seq': current['commit_seq'],
            'current_operation': current['operation'],
            'current_recipe': current['recipe'],
            'version_count': len(chain),
        })
    snapshots = [dict(row) for row in conn.execute(
        select(read_snapshots).order_by(read_snapshots.c.id)).mappings()]
    snapshot_checks = []
    for snapshot in snapshots:
        for formula_id in formula_ids:
            hit = read_at(conn, formula_id, snapshot['read_seq'])
            snapshot_checks.append({
                'snapshot_code': snapshot['snapshot_code'],
                'read_seq': snapshot['read_seq'],
                'formula_id': formula_id,
                'hit_version_no': hit['version_no'] if hit else None,
                'hit_commit_seq': hit['commit_seq'] if hit else None,
                'operation': hit['operation'] if hit else None,
                'recipe': hit['recipe'] if hit else None,
                'visibility': _visibility(hit),
            })
    return {'head_seq': head, 'formulas': formulas_view,
            'snapshots': snapshots, 'snapshot_reads': snapshot_checks}


def _visibility(hit: dict | None) -> str:
    if hit is None:
        return '无可见版本'
    if hit['operation'] == ChangeOperation.DELETE:
        return '已删除'
    return '可见'


# ---------------------------------------------------------------------------
# 档案维护：直接登记版次与打开读快照（模拟“别的组”的并发写入来源）
# ---------------------------------------------------------------------------
def _append_version(conn, formula_id: str, operation: str, recipe: str | None,
                    source_order_id: str | None, commit_seq: int) -> int:
    """在当前事务内以指定提交序号追加一个版次，返回新版本号。

    只追加、不更新历史行：旧读快照的水位读因此永远命中旧版次。
    取现行版本号时持行锁，保证并发提交不会算出重复的版本号。
    """
    chain = formula_versions(conn, formula_id, for_update=True)
    next_version_no = (chain[-1]['version_no'] + 1) if chain else 1
    conn.execute(formulas.insert().values(
        formula_id=formula_id, version_no=next_version_no, commit_seq=commit_seq,
        operation=operation, recipe=recipe, source_order_id=source_order_id,
        created_at=utc_time()))
    return next_version_no


def open_snapshot(conn, form, actor: str) -> dict:
    code = form.snapshot_code.strip()
    exists = conn.execute(select(read_snapshots.c.id)
                          .where(read_snapshots.c.snapshot_code == code)).first()
    if exists:
        raise HTTPException(409, f'读快照 {code} 已存在')
    head = current_head(conn)
    conn.execute(read_snapshots.insert().values(
        snapshot_code=code, read_seq=head, label=form.label.strip(),
        opened_by=actor, opened_at=utc_time()))
    conn.execute(audit.insert().values(
        actor=actor, action='open_snapshot', reference=code, created_at=utc_time()))
    return _snapshot_by_code(conn, code)


def _snapshot_by_code(conn, code: str) -> dict:
    row = conn.execute(select(read_snapshots).where(
        read_snapshots.c.snapshot_code == code)).mappings().first()
    if row is None:
        raise HTTPException(404, '找不到该读快照')
    return dict(row)


# ---------------------------------------------------------------------------
# 离线批单
# ---------------------------------------------------------------------------
def create_order(conn, form, actor: str) -> dict:
    """登记一张从旧档案领走的离线批单（待收状态）。"""
    duplicate = conn.execute(select(change_orders.c.id)
                             .where(change_orders.c.order_id == form.order_id)).first()
    if duplicate:
        raise HTTPException(409, f'批单 {form.order_id} 已登记，请勿重复领单')
    items = [item.model_dump() for item in form.items]
    head = current_head(conn)
    conn.execute(change_orders.insert().values(
        order_id=form.order_id, title=form.title.strip(), status=OrderStatus.PENDING,
        base_seq=head, commit_seq=None, conflicts=None, items=items,
        created_by=actor, created_at=utc_time()))
    conn.execute(audit.insert().values(
        actor=actor, action='create_order', reference=form.order_id,
        created_at=utc_time()))
    return order_detail(conn, form.order_id)


def receive_order(conn, order_id: str, actor: str) -> dict:
    """收单：幂等 + 乐观并发复检 + 整单原子接纳/退回。

    返回的 ``status`` 为 ``accepted`` 或 ``rejected``；HTTP 层面对退回用 409，
    但批单本身已落库为「已退回」，重试只回传同一结果。
    """
    order = _order_for_update(conn, order_id)

    # 幂等：单号唯一约束 + 状态复检。中断重试同一单，直接回传首次收单结果，
    # 不再发号、不再写版本——“重试同一单不重复生效”。
    if order['status'] != OrderStatus.PENDING:
        return dict(order), False

    # 发号器行锁：与所有并发提交互斥，保证整单一个序号、并发不重号。
    # 持锁期间复检基线，因此别的组在本单领单后的任何写入都不会丢失。
    _lock_counter(conn)
    # 统一按配方编号排序后复检与写入，避免并发事务以不同顺序触碰同一索引，
    # 在 InnoDB gap lock 下交叉等待导致死锁。
    ordered_items = sorted(order['items'], key=lambda item: item['formula_id'])
    conflicts = _detect_conflicts(conn, ordered_items)
    now = utc_time()
    if conflicts:
        # 整单退回：一个配方冲突，整单都不生效，提交水位不前推。
        conn.execute(change_orders.update().where(change_orders.c.id == order['id']).values(
            status=OrderStatus.REJECTED, conflicts=conflicts,
            received_at=now, received_by=actor))
        conn.execute(audit.insert().values(
            actor=actor, action='reject_order', reference=order_id,
            created_at=now))
        return order_detail(conn, order_id), False

    commit_seq = _next_commit_seq(conn)
    for item in ordered_items:
        # 整单所有配方写同一个 commit_seq——“整单共用一个提交序号”。
        _append_version(conn, item['formula_id'], item['operation'],
                        item['recipe'], order_id, commit_seq)
    conn.execute(change_orders.update().where(change_orders.c.id == order['id']).values(
        status=OrderStatus.ACCEPTED, commit_seq=commit_seq,
        received_at=now, received_by=actor))
    conn.execute(audit.insert().values(
        actor=actor, action='accept_order', reference=order_id,
        created_at=now))
    return order_detail(conn, order_id), True


def _detect_conflicts(conn, items: list[dict]) -> list[dict]:
    """逐配方比对领单基线与现行版本。

    判定口径：现行版本号 != 基线版本号即冲突。版本号只增不减，
    “改过又改回”虽然配方文本可能相同，版本号已经前进，照样判冲突。
    基线版本号为 0 表示领单时配方不存在，此时现行版本仍不存在才算未动。
    """
    conflicts = []
    for item in items:
        # 锁读当前版本：MySQL 下为当前读并持行锁，复检与写入之间不会被插队。
        current = current_version(conn, item['formula_id'], for_update=True)
        current_no = current['version_no'] if current else 0
        if current_no != item['base_version_no']:
            conflicts.append({
                'formula_id': item['formula_id'],
                'base_version_no': item['base_version_no'],
                'base_recipe': item['base_recipe'],
                'current_version_no': current_no,
                'current_recipe': current['recipe'] if current else None,
                'current_operation': current['operation'] if current else None,
                'reason': _conflict_reason(item, current),
            })
    return conflicts


def _conflict_reason(item: dict, current: dict | None) -> str:
    if item['base_version_no'] == 0:
        return '领单后该配方被其他组新建，离线单未包含这一变化'
    if current is None:
        return '领单基线版本已不存在'
    if current['recipe'] == item['base_recipe']:
        # 版本号前进但文本相同：典型的“改过又改回”，仍按冲突整单退回。
        return ('领单后版本已由其他组改动并改回，配方文本虽与基线一致，'
                f'版本号已从 {item["base_version_no"]} 前进到 {current["version_no"]}')
    return (f'领单后配方已被其他组改动，版本号由 {item["base_version_no"]} '
            f'前进到 {current["version_no"]}')


def list_orders(conn) -> list[dict]:
    return [dict(row) for row in conn.execute(
        select(change_orders).order_by(change_orders.c.id.desc())
        .limit(100)).mappings()]


def order_detail(conn, order_id: str) -> dict:
    row = conn.execute(select(change_orders).where(
        change_orders.c.order_id == order_id)).mappings().first()
    if row is None:
        raise HTTPException(404, '找不到该批单')
    return dict(row)


# ---------------------------------------------------------------------------
# 发号器：方言兼容的行锁与取号
# ---------------------------------------------------------------------------
def ensure_counter(conn):
    exists = conn.execute(select(seq_counters.c.name)
                          .where(seq_counters.c.name == COUNTER_NAME)).first()
    if not exists:
        conn.execute(seq_counters.insert().values(name=COUNTER_NAME, next_seq=1))


def _is_mysql(conn) -> bool:
    return conn.dialect.name == 'mysql'


def _lock_counter(conn):
    ensure_counter(conn)
    if _is_mysql(conn):
        # InnoDB 行锁：同一时刻只有一个事务能持有发号器行。
        conn.execute(select(seq_counters.c.next_seq)
                     .where(seq_counters.c.name == COUNTER_NAME)
                     .with_for_update())
    # SQLite 仅单写者，BEGIN IMMEDIATE 已在连接层互斥，无需显式行锁。


def _next_commit_seq(conn) -> int:
    row = conn.execute(select(seq_counters.c.next_seq)
                       .where(seq_counters.c.name == COUNTER_NAME)).one()
    value = row[0]
    conn.execute(seq_counters.update().where(seq_counters.c.name == COUNTER_NAME)
                 .values(next_seq=value + 1))
    return value


def _order_for_update(conn, order_id: str) -> dict:
    stmt = select(change_orders).where(change_orders.c.order_id == order_id)
    if _is_mysql(conn):
        stmt = stmt.with_for_update()
    row = conn.execute(stmt).mappings().first()
    if row is None:
        raise HTTPException(404, '找不到该批单')
    return dict(row)
