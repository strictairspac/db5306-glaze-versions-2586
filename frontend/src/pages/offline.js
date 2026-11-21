import {heading, panel} from '../components/common/heading.js';
import {records} from '../components/common/records.js';
import {date, esc} from '../utils/dom.js';
import {topic} from '../constants/project.js';
import {submit} from '../hooks/submit.js';

const statusText = {pending: '待收', accepted: '已接纳', rejected: '已退回'};
const opText = {'写入': '写入', '删除': '删除'};

export function offlinePage(state) {
  const {archive, orders, session} = state;
  const isOperator = session.role === 'operator';
  if (!archive) return heading(topic.modules[4], '加载离线改方档案中…');

  const formulaRows = archive.formulas.map(f => [
    esc(f.formula_id), esc(f.current_version_no), esc(f.current_commit_seq),
    esc(opText[f.current_operation] || f.current_operation),
    f.current_recipe ? esc(f.current_recipe) : '<span class="muted">（空）</span>',
    esc(f.version_count),
  ]);

  const snapshotRows = archive.snapshot_reads.map(r => [
    esc(r.snapshot_code), esc(r.read_seq), esc(r.formula_id),
    r.hit_version_no == null ? '—' : esc(r.hit_version_no),
    r.hit_commit_seq == null ? '—' : esc(r.hit_commit_seq),
    esc(r.visibility),
    r.recipe ? esc(r.recipe) : '<span class="muted">（空）</span>',
  ]);

  const orderRows = orders.map(o => [
    `<a href="#offline/${esc(o.order_id)}" data-order="${esc(o.order_id)}">${esc(o.title)}</a>`,
    esc(o.order_id), esc(statusText[o.status] || o.status),
    esc(o.base_seq), o.commit_seq == null ? '—' : esc(o.commit_seq),
    esc(o.items.length), esc(date(o.received_at || o.created_at)),
  ]);

  return `${heading(topic.modules[4],
    '离线改方收单：按领单基线复检冲突，整单接纳共用一个提交序号；旧读快照仍读打开时的配方。',
    isOperator ? '<a class="button" href="#offline/new">登记离线批单</a>' : '')}
    <div class="metrics-bar"><span>已提交水位 <strong>${esc(archive.head_seq)}</strong></span>
      <span>配方 <strong>${archive.formulas.length}</strong></span>
      <span>活跃读快照 <strong>${archive.snapshots.length}</strong></span>
      <span>批单 <strong>${orders.length}</strong></span></div>

    ${panel('现行配方与版本链（旧档案可重开核对）',
      records(['配方编号', '现行版本', '提交序号', '操作', '现行配方', '历史版次数'], formulaRows, '台账暂无配方。'))}

    ${panel('读快照水位读（已打开的快照仍读原来的配方）',
      records(['快照', '读取水位', '配方', '命中版本', '命中序号', '可见性', '命中配方'], snapshotRows, '暂无读快照。'))}

    ${panel('离线改方批单',
      records(['批单', '单号', '状态', '领单水位', '接纳序号', '配方数', '收单/登记时间'], orderRows, '暂无批单。'))}

    ${isOperator ? panel('档案维护：登记别组写入 / 打开读快照', `
      <div class="field-grid two-col">
      <form id="register-form" class="mini-form">
        <h3>登记配方版次（模拟别组并发写入）</h3>
        <label>配方编号<input name="formula_id" required maxlength="64" placeholder="GL-Q01"></label>
        <label>操作<select name="operation"><option value="写入">写入</option><option value="删除">删除</option></select></label>
        <label>釉料配方<textarea name="recipe" rows="2" placeholder="写入时必填，删除留空"></textarea></label>
        <button>登记版次</button>
      </form>
      <form id="snapshot-form" class="mini-form">
        <h3>打开读快照（固化当前水位）</h3>
        <label>快照编号<input name="snapshot_code" required maxlength="64" placeholder="snap-xxx"></label>
        <label>名称<input name="label" required maxlength="120" placeholder="烧成-称料"></label>
        <button>打开快照</button>
      </form></div>`) : ''}`;
}

export function bindOffline(state, api, refresh, fail) {
  const isOperator = state.session.role === 'operator';
  if (!isOperator) return;

  submit('register-form', async data => {
    const formulaId = data.get('formula_id').trim();
    const payload = {operation: data.get('operation'), recipe: data.get('recipe')};
    await api(`/offline/formulas/${encodeURIComponent(formulaId)}/versions`,
      {method: 'POST', data: payload});
    await refresh();
  }, fail);

  submit('snapshot-form', async data => {
    await api('/offline/snapshots', {method: 'POST', data: {
      snapshot_code: data.get('snapshot_code').trim(), label: data.get('label').trim()}});
    await refresh();
  }, fail);
}
