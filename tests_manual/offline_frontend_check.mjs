// 前端离线改方页面的 jsdom 渲染与交互验收（隔离依赖，不进项目）。
// 运行：node --experimental-vm-modules tests_manual/offline_frontend_check.mjs
import {JSDOM} from '/tmp/nodeenv/node_modules/jsdom/lib/api.js';

const dom = new JSDOM('<!doctype html><html><body><div id="app"></div></body></html>',
  {url: 'http://localhost/', pretendToBeVisual: true});
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.location = dom.window.location;
try { Object.defineProperty(globalThis, 'navigator', {value: dom.window.navigator, configurable: true}); }
catch { /* Node 22 自带只读 navigator，页面未直接依赖，可忽略 */ }
globalThis.sessionStorage = dom.window.sessionStorage;
globalThis.FormData = dom.window.FormData;
globalThis.Event = dom.window.Event;
if (!globalThis.crypto) globalThis.crypto = dom.window.crypto;

const FE = '/workspace/frontend/src';
const {offlinePage, bindOffline} = await import(`${FE}/pages/offline.js`);
const {orderDetailPage, bindOrderDetail} = await import(`${FE}/pages/order-detail.js`);
const {newOrderPage, bindNewOrder} = await import(`${FE}/pages/new-order.js`);
const {routeRaw} = await import(`${FE}/router/hash.js`);

let failures = 0;
function check(name, cond, detail) {
  console.log(`[${cond ? 'PASS' : 'FAIL'}] ${name}` + (!cond && detail ? ` — ${detail}` : ''));
  if (!cond) failures++;
}
const mount = html => { document.getElementById('app').innerHTML = html; };
const esc = s => String(s).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));

const archive = {
  head_seq: 7,
  formulas: [
    {formula_id: 'GL-Q01', current_version_no: 4, current_commit_seq: 4,
     current_operation: '写入', current_recipe: '青岚釉 v4', version_count: 4},
    {formula_id: 'GL-H03', current_version_no: 2, current_commit_seq: 6,
     current_operation: '删除', current_recipe: null, version_count: 2},
  ],
  snapshots: [{snapshot_code: 'early', read_seq: 2, label: '早班'}],
  snapshot_reads: [
    {snapshot_code: 'early', read_seq: 2, formula_id: 'GL-Q01', hit_version_no: 2,
     hit_commit_seq: 2, operation: '写入', recipe: '青岚釉 v2', visibility: '可见'},
    {snapshot_code: 'early', read_seq: 2, formula_id: 'GL-H03', hit_version_no: null,
     hit_commit_seq: null, operation: null, recipe: null, visibility: '无可见版本'},
  ],
};
const orders = [
  {order_id: 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', title: '待收单', status: 'pending',
   base_seq: 7, commit_seq: null, items: [{formula_id: 'GL-Q01'}],
   created_at: '2026-11-21T08:00:00Z', received_at: null},
  {order_id: 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb', title: '退回单', status: 'rejected',
   base_seq: 5, commit_seq: null, items: [{formula_id: 'GL-H03'}],
   conflicts: [{formula_id: 'GL-H03', base_version_no: 2, current_version_no: 3,
     base_recipe: '旧', current_recipe: '新', reason: '改动并改回，版本号已前进'}],
   created_at: '2026-11-21T09:00:00Z', received_at: '2026-11-21T09:05:00Z'},
  {order_id: 'cccccccc-cccc-cccc-cccc-cccccccccccc', title: '接纳单', status: 'accepted',
   base_seq: 5, commit_seq: 8, items: [{formula_id: 'GL-Q01'}],
   created_at: '2026-11-21T10:00:00Z', received_at: '2026-11-21T10:05:00Z'},
];
const operatorSession = {role: 'operator'};
const viewerSession = {role: 'viewer'};

// 1. 经办员档案页
mount(offlinePage({archive, orders, session: operatorSession}));
check('档案页渲染水位指标', document.body.textContent.includes('已提交水位') &&
  document.body.textContent.includes('7'));
check('档案页列出现行配方', document.body.textContent.includes('GL-Q01') &&
  document.body.textContent.includes('青岚釉 v4'));
check('档案页渲染快照水位读且旧快照读旧配方', document.body.textContent.includes('青岚釉 v2'));
check('档案页列出三张批单及中文状态',
  ['待收', '已退回', '已接纳'].every(t => document.body.textContent.includes(t)));
check('经办员可见登记/快照表单与新建按钮',
  !!document.getElementById('register-form') && !!document.getElementById('snapshot-form')
  && !!document.querySelector('a[href="#offline/new"]'));
check('批单链接用完整 UUID',
  !!document.querySelector('a[href="#offline/bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"]'));

// 2. 观察员只读
mount(offlinePage({archive, orders, session: viewerSession}));
check('观察员看不到维护表单', !document.getElementById('register-form') &&
  !document.getElementById('snapshot-form'));
check('观察员看不到新建批单按钮', !document.querySelector('a[href="#offline/new"]'));
check('观察员仍可查看批单与快照', document.body.textContent.includes('GL-Q01'));

// 3. 待收批单详情
const pendingId = orders[0].order_id;
mount(orderDetailPage({orders, session: operatorSession}, pendingId));
check('待收单展示收单按钮', !!document.getElementById('receive-form'));
check('待收单显示基线条目', document.body.textContent.includes('GL-Q01'));

// 4. 退回批单详情含冲突明细
mount(orderDetailPage({orders, session: operatorSession}, orders[1].order_id));
check('退回单渲染冲突区块与点名配方',
  document.body.textContent.includes('冲突配方') && document.body.textContent.includes('GL-H03')
  && document.body.textContent.includes('改动并改回'));
check('退回单无收单按钮、显示重试', !!document.getElementById('retry-form') &&
  !document.getElementById('receive-form'));

// 5. 接纳批单详情显示共用序号
mount(orderDetailPage({orders, session: operatorSession}, orders[2].order_id));
check('接纳单展示共用提交序号 8', document.body.textContent.includes('8') &&
  document.querySelector('[data-testid="accepted"]'));

// 6. 找不到批单
mount(orderDetailPage({orders, session: operatorSession}, 'nope-nope'));
check('未知单号给出提示', document.body.textContent.includes('找不到该批单'));

// 7. 收单交互：成功路径
let calls = [];
const api = async (path, opts) => { calls.push({path, opts}); return {order_id: pendingId}; };
let refreshes = 0;
const refresh = async () => { refreshes++; };
const fail = e => { throw e; };
mount(orderDetailPage({orders, session: operatorSession}, pendingId));
bindOrderDetail({orders, session: operatorSession}, api, pendingId, refresh, fail);
document.getElementById('receive-form').dispatchEvent(
  new dom.window.Event('submit', {cancelable: true, bubbles: true}));
await new Promise(r => setTimeout(r, 10));
check('收单调用 receive 接口', calls.some(c => c.path.includes('/receive') && c.opts.method === 'POST'));
check('收单成功后刷新页面', refreshes >= 1);

// 8. 收单 409：刷新并把错误继续抛给全局提示
calls = []; let refreshes2 = 0; let caught = null;
const api409 = async () => { const e = new Error('批单存在冲突，已整单退回'); e.status = 409; throw e; };
mount(orderDetailPage({orders, session: operatorSession}, pendingId));
bindOrderDetail({orders, session: operatorSession}, api409, pendingId,
  async () => { refreshes2++; }, e => { caught = e; });
document.getElementById('receive-form').dispatchEvent(
  new dom.window.Event('submit', {cancelable: true, bubbles: true}));
await new Promise(r => setTimeout(r, 10));
check('409 时仍刷新以展示冲突明细', refreshes2 >= 1);
check('409 错误上抛到全局提示', caught && caught.status === 409);

// 9. 新建批单页与多行提交
mount(newOrderPage({archive, orders, session: operatorSession}));
check('新建页渲染表单且预填单号',
  !!document.getElementById('new-order-form') &&
  document.querySelector('[name="order_id"]').value.length >= 8);
const posted = [];
const apiPost = async (path, opts) => { posted.push({path, data: opts.data}); return {order_id: opts.data.order_id}; };
dom.window.location.hash = '#offline/new';
bindNewOrder({archive, session: operatorSession}, apiPost, refresh, fail);
check('新建页预填首行基线版本号',
  Number(document.querySelector('[name="base_version_no"]').value) === 4);
// 再加一行并填写
document.getElementById('add-item').click();
const rows = document.querySelectorAll('.item-row');
check('可增加改方条目到 2 行', rows.length === 2);
const second = rows[1];
second.querySelector('[name="formula_id"]').value = 'GL-H03';
second.querySelector('[name="base_version_no"]').value = '2';
second.querySelector('[name="operation"]').value = '删除';
second.querySelector('[name="recipe"]').value = '';
document.getElementById('new-order-form').dispatchEvent(
  new dom.window.Event('submit', {cancelable: true, bubbles: true}));
await new Promise(r => setTimeout(r, 10));
check('新建批单 POST 到 /offline/orders 且含两条目',
  posted.length === 1 && posted[0].path === '/offline/orders' && posted[0].data.items.length === 2);
check('删除条目配方归一为 null',
  posted[0].data.items[1].operation === '删除' && posted[0].data.items[1].recipe === null);
check('提交后跳转到批单详情',
  dom.window.location.hash === `#offline/${posted[0].data.order_id}`);

// 10. 路由保留 UUID 字符串
dom.window.location.hash = '#offline/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa';
check('routeRaw 保留完整 UUID',
  routeRaw().id === 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa');

console.log(failures ? `\n${failures} 项前端验收失败。` : '\n前端验收全部通过。');
process.exit(failures ? 1 : 0);
