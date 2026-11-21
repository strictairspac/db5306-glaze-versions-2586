"""离线改方批单路由：档案维护、读快照、领单/收单/重试与核对查询。"""

from fastapi import APIRouter, Depends

from src.schemas.workflow import (ChangeOrderForm, RetryForm, SnapshotForm,
                                  VersionForm)
from src.services import offline_workflow as flow
from src.services.security import current_user, operator

router = APIRouter(prefix='/offline', dependencies=[Depends(current_user)])


@router.get('/archive')
def archive():
    """档案页：现行配方、读快照与逐快照水位读，供重开核对。"""
    return flow.archive_overview()


@router.get('/formulas/{formula_id}')
def formula_chain(formula_id: str):
    """单个配方的完整版本链（含每次提交序号与来源批单）。"""
    return flow.list_formula_chain(formula_id)


@router.post('/formulas/{formula_id}/versions', status_code=201)
def register_version(formula_id: str, form: VersionForm, actor=Depends(operator)):
    """档案页直接登记版次，模拟别组在台账上的并发写入。"""
    return flow.register_formula_version(formula_id, form, actor)


@router.get('/snapshots')
def list_snapshots():
    return flow.snapshots()


@router.post('/snapshots', status_code=201)
def open_snapshot(form: SnapshotForm, actor=Depends(operator)):
    """打开读快照，固化当前水位；之后的提交对它不可见。"""
    return flow.open_read_snapshot(form, actor)


@router.get('/orders')
def list_orders():
    return flow.list_change_orders()


@router.post('/orders', status_code=201)
def create_order(form: ChangeOrderForm, actor=Depends(operator)):
    """登记从旧档案领走的离线批单（待收）。"""
    return flow.create_change_order(form, actor)


@router.get('/orders/{order_id}')
def get_order(order_id: str):
    return flow.get_change_order(order_id)


@router.post('/orders/{order_id}/receive')
def receive_order(order_id: str, actor=Depends(operator)):
    """收单：无冲突整单接纳并共用一个提交序号；冲突整单退回（409）。

    已接纳的单重复收单按幂等回传 200；已退回的单重复收单仍回 409 与同一冲突明细。
    """
    detail, newly_accepted = flow.receive_change_order(order_id, actor)
    if detail['status'] == 'rejected':
        flow.raise_rejected(detail)
    return detail


@router.post('/orders/retry')
def retry_order(form: RetryForm, actor=Depends(operator)):
    """中断后按单号重试：只回传首次结论，不重复生效。"""
    return flow.retry_change_order(form.order_id, actor)
