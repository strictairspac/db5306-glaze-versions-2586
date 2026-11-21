import {heading, panel} from '../components/common/heading.js';
import {records} from '../components/common/records.js';
import {date, esc} from '../utils/dom.js';
import {topic} from '../constants/project.js';
import {submit} from '../hooks/submit.js';

const statusText = {pending: '待收', accepted: '已接纳', rejected: '已退回'};

export function orderDetailPage(state, orderId) {
  const order = state.orders.find(o => o.order_id === orderId);
  if (!order) return heading(topic.modules[4], '找不到该批单。') +
    '<p class="empty"><a href="#offline">返回离线档案</a></p>';
  const isOperator = state.session.role === 'operator';

  const itemRows = order.items.map(it => [
    esc(it.formula_id), esc(it.base_version_no),
    it.base_recipe ? esc(it.base_recipe) : '<span class="muted">（空/不存在）</span>',
    esc(it.operation), it.recipe ? esc(it.recipe) : '<span class="muted">（删除留空）</span>',
  ]);

  let conflictBlock = '';
  if (order.status === 'rejected' && order.conflicts && order.conflicts.length) {
    const conflictRows = order.conflicts.map(c => [
      esc(c.formula_id),
      `基线 v${esc(c.base_version_no)} → 现行 v${esc(c.current_version_no)}`,
      c.base_recipe ? esc(c.base_recipe) : '—',
      c.current_recipe ? esc(c.current_recipe) : '—',
      esc(c.reason),
    ]);
    conflictBlock = panel('冲突配方（整单已退回，未写入任何版次）',
      records(['配方', '版本变化', '基线配方', '现行配方', '冲突原因'], conflictRows));
  }

  const acceptedNote = order.status === 'accepted'
    ? `<p class="ok" data-testid="accepted">整单已接纳，共用提交序号 <strong>${esc(order.commit_seq)}</strong>。</p>` : '';
  const rejectedNote = order.status === 'rejected'
    ? '<p class="error" data-testid="rejected">批单存在冲突，已整单退回。修正台账或重新领单后再收。</p>' : '';

  const actions = isOperator && order.status === 'pending'
    ? `<form id="receive-form"><button data-testid="receive">收单（复检基线并整单接纳/退回）</button>
       <a class="secondary" href="#offline">返回</a></form>`
    : isOperator ? `<form id="retry-form"><button class="secondary" data-testid="retry">中断重试（幂等回传）</button>
       <a class="secondary" href="#offline">返回</a></form>`
    : '<a href="#offline">返回</a>';

  return `${heading(`批单 · ${order.title}`, `单号 ${esc(order.order_id)}`)}
    <div class="brief order-head">
      <dl>
        <dt>状态</dt><dd data-testid="order-status">${esc(statusText[order.status] || order.status)}</dd>
        <dt>领单时水位</dt><dd>${esc(order.base_seq)}</dd>
        <dt>接纳提交序号</dt><dd>${order.commit_seq == null ? '—' : esc(order.commit_seq)}</dd>
        <dt>登记人/时间</dt><dd>${esc(order.created_by)} · ${esc(date(order.created_at))}</dd>
        <dt>收单人/时间</dt><dd>${order.received_by ? `${esc(order.received_by)} · ${esc(date(order.received_at))}` : '—'}</dd>
      </dl>
      ${acceptedNote}${rejectedNote}${actions}
    </div>
    ${panel('领单内容与领单基线',
      records(['配方编号', '基线版本', '基线配方', '离线操作', '离线配方'], itemRows))}
    ${conflictBlock}`;
}

export function bindOrderDetail(state, api, orderId, refresh, fail) {
  if (state.session.role !== 'operator') return;
  const order = state.orders.find(o => o.order_id === orderId);
  if (!order) return;

  const doReceive = async () => {
    try {
      const detail = await api(`/offline/orders/${encodeURIComponent(orderId)}/receive`,
        {method: 'POST'});
      await refresh();
      return detail;
    } catch (error) {
      // 409 整单退回：刷新让冲突明细渲染，同时把消息留在页面提示区。
      if (error.status === 409) { await refresh(); }
      throw error;
    }
  };

  submit('receive-form', doReceive, fail);
  submit('retry-form', async () => {
    await api('/offline/orders/retry', {method: 'POST', data: {order_id: orderId}});
    await refresh();
  }, fail);
}
