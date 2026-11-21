import {heading} from '../components/common/heading.js';
import {esc} from '../utils/dom.js';
import {topic} from '../constants/project.js';
import {submit} from '../hooks/submit.js';
import {go} from '../router/hash.js';

function uuid() {
  if (crypto.randomUUID) return crypto.randomUUID();
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
    const v = Math.random() * 16 | 0;
    return (c === 'x' ? v : (v & 0x3 | 0x8)).toString(16);
  });
}

export function newOrderPage(state) {
  if (state.session.role !== 'operator') {
    return heading(topic.modules[4], '仅经办员可登记离线批单。');
  }
  const current = Object.fromEntries(state.archive.formulas.map(f => [f.formula_id, f]));
  // 预填一行：默认取台账第一个配方的现行版本作为领单基线。
  const first = state.archive.formulas[0];
  const preset = first ? {
    formula_id: first.formula_id,
    base_version_no: first.current_version_no,
  } : {formula_id: '', base_version_no: 0};

  return `${heading('登记离线改方批单',
    '基线版本与基线配方按领单当时的现行版本固化；收单时版本号一旦前进（含改过又改回）即判冲突。')}
    <form id="new-order-form" class="order-form">
      <label class="field">批单标题<input name="title" required maxlength="120"
        value="窑红试样册·离线改方批单"></label>
      <label class="field">批单编号（幂等键）<input name="order_id" required minlength="8" maxlength="36"
        value="${esc(uuid())}"></label>

      <h3>改方条目 <button type="button" class="secondary" id="add-item">增加一行</button></h3>
      <div id="items"></div>

      <div class="actions form-actions">
        <button>登记批单（待收）</button>
        <a class="secondary" href="#offline">返回</a>
      </div>
    </form>
    <script type="application/json" id="formula-current">${safeJson(current)}</script>
    <script type="application/json" id="item-preset">${safeJson(preset)}</script>`;
}

// <script> 内的 JSON 只需防 </script> 闭合，不能做 HTML 属性式转义（会破坏引号）。
function safeJson(value) {
  return JSON.stringify(value).replace(/</g, '\\u003c');
}

function itemRow(preset, index) {
  return `<fieldset class="item-row" data-index="${index}">
    <legend>配方 ${index + 1}</legend>
    <label>配方编号<input name="formula_id" required maxlength="64" value="${esc(preset.formula_id)}"></label>
    <label>基线版本号<input name="base_version_no" type="number" min="0" required value="${esc(preset.base_version_no)}"></label>
    <label>基线配方（可空）<textarea name="base_recipe" rows="2" placeholder="删除/不存在时留空"></textarea></label>
    <label>离线操作<select name="operation"><option value="写入">写入</option><option value="删除">删除</option></select></label>
    <label>离线配方<textarea name="recipe" rows="2" placeholder="写入必填，删除留空"></textarea></label>
    <button type="button" class="secondary remove-item">移除</button>
  </fieldset>`;
}

export function bindNewOrder(state, api, refresh, fail) {
  if (state.session.role !== 'operator') return;
  const preset = JSON.parse(document.getElementById('item-preset').textContent);
  const itemsBox = document.getElementById('items');
  let count = 0;
  const addRow = () => {
    itemsBox.insertAdjacentHTML('beforeend', itemRow(preset, count++));
  };
  addRow();

  document.getElementById('add-item').addEventListener('click', addRow);
  itemsBox.addEventListener('click', event => {
    if (event.target.classList.contains('remove-item')) {
      event.target.closest('.item-row').remove();
    }
  });

  submit('new-order-form', async data => {
    const rows = [...document.querySelectorAll('.item-row')];
    if (!rows.length) throw new Error('批单至少要有一个改方条目。');
    const items = rows.map(row => {
      const get = name => row.querySelector(`[name="${name}"]`).value;
      return {
        formula_id: get('formula_id').trim(),
        base_version_no: Number(get('base_version_no')),
        base_recipe: get('base_recipe').trim() || null,
        operation: get('operation'),
        recipe: get('recipe').trim() || null,
      };
    });
    const payload = {
      order_id: data.get('order_id').trim(),
      title: data.get('title').trim(),
      items,
    };
    const created = await api('/offline/orders', {method: 'POST', data: payload});
    await refresh();
    go(`offline/${created.order_id}`);
  }, fail);
}
