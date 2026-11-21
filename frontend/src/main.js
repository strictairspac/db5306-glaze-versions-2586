import {request} from './api/client.js';
import {state, setSession} from './stores/session.js';
import {roleLabels} from './constants/workflow.js';
import {topic} from './constants/project.js';
import {loginPage} from './pages/login.js';
import {casesPage} from './pages/cases.js';
import {bindEditor, editorPage} from './pages/editor.js';
import {runsPage} from './pages/runs.js';
import {bindEvidence, evidencePage} from './pages/evidence.js';
import {bindOffline, offlinePage} from './pages/offline.js';
import {bindOrderDetail, orderDetailPage} from './pages/order-detail.js';
import {bindNewOrder, newOrderPage} from './pages/new-order.js';
import {go, route, routeRaw} from './router/hash.js';
import {submit} from './hooks/submit.js';
import {esc} from './utils/dom.js';

const app = document.getElementById('app');
document.title = topic.name;
let timer;
let revision = 0;
function fail(error) {
  if (error.status===401 && state.session) { setSession(null); void refresh(); return; }
  const message = document.getElementById('message');
  if (message) { message.textContent=error.message; message.className='error'; }
}
const api = (path, options={})=>request(path, {...options, token:state.session?.token});
const pages = {cases:casesPage, editor:editorPage, runs:runsPage, evidence:evidencePage};
async function refresh() {
  clearTimeout(timer);
  const key = ++revision;
  if (!state.session) {
    app.innerHTML=loginPage();
    submit('login-form', async data=>{
      setSession(await api('/login', {method:'POST', data:Object.fromEntries(data)}));
      if (location.hash!=='#cases') go('cases');
      else await refresh();
    }, fail);
    return;
  }
  const {key: page,id} = route();
  // 离线改方模块走独立数据装配，避免拖慢原有四个模块的首屏。
  if (page==='offline') {
    const orderId = routeRaw().id;  // UUID 字符串单号，保持原样
    try {
      const [archive, orders] = await Promise.all([api('/offline/archive'), api('/offline/orders')]);
      if (revision !== key) return;
      state.archive = archive;
      state.orders = orders;
    } catch (error) {
      if (revision !== key) return;
      fail(error);
      return;
    }
    renderShell(page, offlineShell(orderId));
    if (orderId === 'new') bindNewOrder(state, api, refresh, fail);
    else if (orderId) bindOrderDetail(state, api, orderId, refresh, fail);
    else bindOffline(state, api, refresh, fail);
    return;
  }
  if (!pages[page]) { go('cases'); return; }
  try {
    const result = await Promise.all([state.spec || api('/spec'),api('/cases'),api('/runs'),api('/evidence'),api('/audit')]);
    if (revision !== key) return;
    [state.spec,state.cases,state.runs,state.evidence,state.audit]=result;
  } catch (error) {
    if (revision !== key) return;
    if (!document.getElementById('message')) app.innerHTML='<main><div id="message" role="alert"></div><button id="retry">重试</button></main>';
    document.getElementById('retry')?.addEventListener('click', refresh);
    fail(error);
    return;
  }
  renderShell(page, pages[page](state,id));
  if (page==='editor') bindEditor(state, api, fail);
  if (page==='evidence') bindEvidence(api, refresh, fail);
  if (page==='runs') {
    document.getElementById('refresh').addEventListener('click', refresh);
    if (state.runs.some(row=>['queued','running'].includes(row.status))) timer=setTimeout(refresh,1000);
  }
}

function offlineShell(page, id) {
  if (id === 'new') return newOrderPage(state);
  if (id) return orderDetailPage(state, id);
  return offlinePage(state);
}

function renderShell(page, content) {
  const navigation = topic.modules.map((label,index)=>{
    const key = index < pages.length ? Object.keys(pages)[index] : 'offline';
    const hidden = key==='editor' && state.session.role!=='operator';
    return hidden ? '' : `<a href="#${key}" ${key===page?'aria-current="page"':''}>${esc(label)}</a>`;
  }).join('');
  app.innerHTML=`<header class="masthead"><a class="identity" href="#cases">${esc(topic.name)}</a><div class="user"><span>${esc(roleLabels[state.session.role])}</span><button id="logout" class="secondary">退出</button></div></header>
    <div class="workspace"><nav aria-label="业务模块">${navigation}</nav><main><div id="message" role="alert"></div>${content}</main></div>`;
  document.getElementById('logout').addEventListener('click',()=>{setSession(null); void refresh();});
}
window.addEventListener('hashchange', refresh);
void refresh();
