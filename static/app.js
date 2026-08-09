// Seedance WebUI 用户端逻辑
let MODELS = [];
let MATERIALS = [];
let FIRST_FRAME = null;
let LAST_FRAME = null;
let MODE = 'text';
let POLLING = null;
let FEATURES = {};
let RATIOS = [];

const $ = (id) => document.getElementById(id);

function toast(msg, ok = true) {
  const t = $('toast');
  t.textContent = msg;
  t.className = 'toast show ' + (ok ? 'ok' : 'err');
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.className = 'toast', 3500);
}

window.onShowLogin = function () {
  $('login-view').style.display = 'flex';
  $('app-view').style.display = 'none';
  stopPolling();
};
window.onShowApp = function () {
  $('login-view').style.display = 'none';
  $('app-view').style.display = 'block';
  initApp();
};

$('login-btn').addEventListener('click', async () => {
  const u = $('login-username').value.trim();
  const p = $('login-password').value;
  $('login-err').textContent = '';
  $('login-btn').disabled = true;
  $('login-btn').textContent = '登录中…';
  try {
    await doLogin(u, p);
    $('login-password').value = '';
    showApp();
  } catch (e) {
    $('login-err').textContent = e.message || '登录失败';
  } finally {
    $('login-btn').disabled = false;
    $('login-btn').textContent = '登 录';
  }
});
['login-username', 'login-password'].forEach(id => {
  $(id).addEventListener('keydown', (e) => { if (e.key === 'Enter') $('login-btn').click(); });
});
$('btn-logout').addEventListener('click', () => doLogout());
$('btn-change-pass').addEventListener('click', () => $('pass-modal').classList.add('open'));
['pass-modal'].forEach(id => {
  $(id).addEventListener('click', (e) => { if (e.target === $(id)) $(id).classList.remove('open'); });
});
function closePassModal() { $('pass-modal').classList.remove('open'); $('new-pass').value = ''; $('new-pass2').value = ''; }

// ---------- 初始化 ----------
async function initApp() {
  buildDurationOptions();
  await loadMe();
  await loadFeatures();
  await loadModels();
  await loadTasks();
  await loadPointsLogs();
  startPolling();
}

function buildDurationOptions() {
  const sel = $('duration');
  if (sel.options.length > 1) return;
  for (let s = 4; s <= 30; s++) {
    const o = document.createElement('option');
    o.value = `${s}s`;
    o.textContent = `${s} 秒`;
    if (s === 5) o.selected = true;
    sel.appendChild(o);
  }
}

// ---------- 我的资料 / 积分 ----------
async function loadMe() {
  try {
    const d = await apiJson('/api/me');
    if (d.retention_days) {
      const el = $('retention-hint');
      if (el) el.textContent = '视频默认保留 ' + d.retention_days + ' 天，超期自动清理，重要视频请及时下载；平台链接长期有效。';
    }
    $('user-name').textContent = d.username;
    $('points').textContent = d.points;
    RATIOS = d.ratios || [];
    const sel = $('ratio');
    sel.innerHTML = '<option value="">默认</option>';
    RATIOS.forEach(r => {
      const o = document.createElement('option');
      o.value = r; o.textContent = r;
      sel.appendChild(o);
    });
  } catch (e) {}
}

async function loadPointsLogs() {
  try {
    const d = await apiJson('/api/points_logs');
    const box = $('points-log-list');
    const logs = d.logs || [];
    if (!logs.length) { box.innerHTML = '<div class="empty">暂无记录</div>'; return; }
    const typeMap = { freeze: '预扣', confirm: '扣费确认', refund: '退还', admin_adjust: '管理员调整' };
    const sign = (t, a) => {
      if (t === 'refund') return '+' + a;
      if (t === 'freeze' || t === 'confirm') return '-' + a;
      return (a >= 0 ? '+' : '') + a;
    };
    box.innerHTML = logs.map(l => `
      <div style="display:flex;justify-content:space-between;gap:10px;padding:8px 4px;border-bottom:1px dashed var(--line);font-size:13px">
        <span style="color:var(--muted)">${typeMap[l.type] || l.type}${l.note ? ' · ' + escapeHtml(l.note) : ''}</span>
        <span style="white-space:nowrap">${sign(l.type, l.amount)} 积分</span>
      </div>`).join('');
  } catch (e) {}
}

// ---------- 功能开关 ----------
async function loadFeatures() {
  try {
    const d = await apiJson('/api/features');
    FEATURES = d.features || {};
    applyFeatures();
  } catch (e) {}
}

function applyFeatures() {
  const show = (id, on) => { const el = $(id); if (el) el.style.display = on ? '' : 'none'; };
  show('tab-text', FEATURES.text_mode !== false);
  show('tab-multi', FEATURES.multi_mode !== false);
  show('tab-fl', FEATURES.first_last_mode !== false);
  show('task-center-card', FEATURES.task_center !== false);
  const up = FEATURES.upload_enabled !== false;
  show('mat-upload-img', up);
  show('mat-upload-video', up);
  show('mat-upload-audio', up);
  show('fl-first-up', up);
  show('fl-last-up', up);
  const order = ['text', 'multi', 'first_last'];
  if (FEATURES[MODE + '_mode'] === false) {
    const first = order.find(m => FEATURES[m + '_mode'] !== false);
    setMode(first || 'text');
  }
}

function setMode(m) {
  MODE = m;
  $('tab-text').className = 'tab' + (m === 'text' ? ' active' : '');
  $('tab-multi').className = 'tab' + (m === 'multi' ? ' active' : '');
  $('tab-fl').className = 'tab' + (m === 'first_last' ? ' active' : '');
  $('materials').style.display = m === 'multi' ? 'block' : 'none';
  $('fl-frame').style.display = m === 'first_last' ? 'flex' : 'none';
  updateCost();
}
document.querySelectorAll('.tab').forEach(t => t.addEventListener('click', () => setMode(t.dataset.mode)));

// ---------- 模型 ----------
async function loadModels(force = false) {
  $('gen-btn').disabled = true;
  const sel = $('model');
  sel.innerHTML = '<option>加载中…</option>';
  try {
    const d = await apiJson('/api/models' + (force ? '?force=1' : ''));
    MODELS = (d.models || []).filter(m => m.enabled !== false);
    sel.innerHTML = '';
    if (!MODELS.length) { sel.innerHTML = '<option value="">无可用模型</option>'; return; }
    MODELS.forEach(m => {
      const o = document.createElement('option');
      o.value = m.id;
      o.textContent = `${m.id} ｜ ${m.cost_per_second} 积分/秒`;
      o.title = m.description || '';
      sel.appendChild(o);
    });
    sel.value = MODELS[0].id;
  } catch (e) {
    toast('模型加载失败: ' + e.message, false);
  } finally {
    $('gen-btn').disabled = false;
    updateCost();
  }
}

function updateCost() {
  const model = MODELS.find(m => m.id === $('model').value);
  if (!model) { $('cost-hint').textContent = ''; return; }
  const dur = parseInt($('duration').value) || 5;
  const price = parseFloat(model.cost_per_second) || 0;
  const need = (price * dur).toFixed(1);
  const points = parseFloat($('points').textContent) || 0;
  $('cost-hint').textContent = `本次将消耗 ${need} 积分（${price}/秒 × ${dur}秒）｜ 当前剩余 ${points} 积分`;
}

$('model').addEventListener('change', () => { updateCost(); });
$('duration').addEventListener('change', updateCost);

// ---------- 多模态素材 ----------
function addMaterialUrl() {
  const url = $('mat-url').value.trim();
  if (!url) return;
  if (!/^https?:\/\//i.test(url)) { toast('请输入以 http(s):// 开头的 URL', false); return; }
  MATERIALS.push({ url, type: $('mat-type').value || 'image', kind: 'url', name: url });
  $('mat-url').value = '';
  renderMaterials();
}

async function uploadMaterial(input, type) {
  const file = input.files && input.files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append('file', file);
  try {
    const d = await apiJson('/api/upload', { method: 'POST', body: fd });
    MATERIALS.push({ url: d.url, type: d.type || type || 'image', kind: 'upload', name: file.name });
    toast('已上传素材');
    renderMaterials();
  } catch (e) {
    toast('上传失败: ' + e.message, false);
  }
  input.value = '';
}

function removeMaterial(i) { MATERIALS.splice(i, 1); renderMaterials(); }

function renderMaterials() {
  const box = $('mat-list');
  box.innerHTML = '';
  MATERIALS.forEach((m, i) => {
    const div = document.createElement('div');
    div.className = 'mat-item';
    const TYPE_ICON = { image: '🖼️', video: '🎞️', audio: '🔊' };
    const ic = TYPE_ICON[m.type] || '📎';
    const isImg = m.type === 'image';
    div.innerHTML = `<span class="idx">${i + 1}</span>${isImg ? `<img src="${m.url}" alt="">` : `<span>${ic}</span>`}<span style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${m.name}</span><button title="在提示词中引用 @${i + 1}" onclick="insertMaterialRef(${i})">@</button><button onclick="removeMaterial(${i})">✕</button>`;
    box.appendChild(div);
  });
  if (!MATERIALS.length) box.innerHTML = '<span class="hint">尚未添加素材</span>';
}

// ---------- 首尾帧 ----------
function setFirstFrameUrl() {
  const url = $('fl-first-url').value.trim();
  if (!/^https?:\/\//i.test(url)) { toast('请输入以 http(s):// 开头的 URL', false); return; }
  FIRST_FRAME = url;
  $('fl-first-url').value = '';
  renderFrames();
}
function setLastFrameUrl() {
  const url = $('fl-last-url').value.trim();
  if (!/^https?:\/\//i.test(url)) { toast('请输入以 http(s):// 开头的 URL', false); return; }
  LAST_FRAME = url;
  $('fl-last-url').value = '';
  renderFrames();
}
async function uploadFirstFrame(input) {
  const file = input.files && input.files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append('file', file);
  try {
    const d = await apiJson('/api/upload', { method: 'POST', body: fd });
    FIRST_FRAME = d.url;
    toast('首帧已上传');
    renderFrames();
  } catch (e) { toast('上传失败: ' + e.message, false); }
  input.value = '';
}
async function uploadLastFrame(input) {
  const file = input.files && input.files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append('file', file);
  try {
    const d = await apiJson('/api/upload', { method: 'POST', body: fd });
    LAST_FRAME = d.url;
    toast('尾帧已上传');
    renderFrames();
  } catch (e) { toast('上传失败: ' + e.message, false); }
  input.value = '';
}
function clearFirstFrame() { FIRST_FRAME = null; renderFrames(); }
function clearLastFrame() { LAST_FRAME = null; renderFrames(); }

function renderFrames() {
  const f = $('fl-first-list');
  const l = $('fl-last-list');
  f.innerHTML = FIRST_FRAME
    ? `<div class="mat-item"><img src="${FIRST_FRAME}" alt=""><span style="max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">首帧</span><button onclick="clearFirstFrame()">✕</button></div>`
    : '<span class="hint">未设置</span>';
  l.innerHTML = LAST_FRAME
    ? `<div class="mat-item"><img src="${LAST_FRAME}" alt=""><span style="max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">尾帧</span><button onclick="clearLastFrame()">✕</button></div>`
    : '<span class="hint">未设置</span>';
}

// ---------- 生成 ----------
function expandPrompt() {
  // @N 标记原样保留，由后端解析为平台 assets（确保素材被模型真正参考）
  return $('prompt').value;
}

async function generate() {
  const model = $('model').value;
  if (!model) { toast('请先选择模型', false); return; }
  if (MODE === 'first_last' && !FIRST_FRAME && !LAST_FRAME) {
    toast('首尾帧模式请至少设置一张图片', false);
    return;
  }
  if (MODE === 'multi' && !MATERIALS.length) {
    if (!confirm('当前为多模态模式但没有素材，将按文生视频生成，确定？')) return;
  }
  const prompt = expandPrompt().trim();
  if (!prompt) { toast('请输入提示词', false); return; }
  const modelObj = MODELS.find(m => m.id === model);
  const dur = parseInt($('duration').value) || 5;
  const need = ((parseFloat(modelObj ? modelObj.cost_per_second : 0) || 0) * dur);
  const points = parseFloat($('points').textContent) || 0;
  if (need > points) {
    toast(`积分不足：本次需要 ${need.toFixed(1)} 积分，当前剩余 ${points} 积分`, false);
    return;
  }
  const fd = new FormData();
  fd.append('model', model);
  fd.append('prompt', prompt);
  fd.append('duration', $('duration').value);
  fd.append('ratio', $('ratio').value);
  fd.append('mode', MODE);
  fd.append('materials', JSON.stringify(MATERIALS.map(m => ({ url: m.url, type: m.type, name: m.name }))));
  fd.append('first_frame', FIRST_FRAME || '');
  fd.append('last_frame', LAST_FRAME || '');
  $('gen-btn').disabled = true;
  $('gen-btn').textContent = '提交中…';
  try {
    const d = await apiJson('/api/generate', { method: 'POST', body: fd });
    toast('任务已提交 ✅');
    $('prompt').value = '';
    MATERIALS = []; renderMaterials();
    FIRST_FRAME = null; LAST_FRAME = null; renderFrames();
    await loadMe();
    await loadTasks();
    await loadPointsLogs();
  } catch (e) {
    toast('生成失败: ' + e.message, false);
  } finally {
    $('gen-btn').disabled = false;
    $('gen-btn').textContent = '🚀 生成视频';
  }
}

// ---------- 任务 ----------
async function loadTasks() {
  try {
    const d = await apiJson('/api/tasks');
    renderTasks(d.tasks || []);
  } catch (e) {}
}

function statusLabel(s) {
  const map = { pending: '排队中', running: '生成中', succeeded: '已完成', failed: '失败', error: '错误' };
  return map[s] || s;
}

function withToken(url) {
  return url.startsWith('/') ? url + '?token=' + encodeURIComponent(getToken()) : url;
}

function playVideo(url, taskId) {
  const modal = $('video-modal');
  if (!modal) return;
  const player = $('video-modal-player');
  const links = $('video-modal-links');
  links.innerHTML = '';
  if (taskId) {
    links.innerHTML = `<a class="btn" href="${withToken('/api/download/' + taskId)}" download>⬇ 下载视频</a>
      <button class="btn" onclick="closeVideoModal()">✕ 关闭</button>`;
  }
  player.src = url;
  modal.classList.add('open');
  player.play().catch(() => {});
}

function closeVideoModal() {
  const modal = $('video-modal');
  const player = $('video-modal-player');
  if (player) { player.pause(); player.removeAttribute('src'); }
  if (modal) modal.classList.remove('open');
}

function renderTasks(tasks) {
  const box = $('task-list');
  $('task-count').textContent = tasks.length ? `${tasks.length} 个任务` : '';
  if (!tasks.length) { box.innerHTML = '<div class="empty">暂无任务，先去生成一个吧 🎬</div>'; return; }
  box.innerHTML = '';
  tasks.forEach(t => {
    const div = document.createElement('div');
    div.className = 'task';
    const st = t.status || 'pending';
    let resultHtml = '';
    if (st === 'succeeded' && t.video_play_url) {
      const url = withToken(t.video_play_url);
      resultHtml = `<div class="task-result">
        <video controls src="${url}"></video>
        <button class="zoom-btn" onclick="playVideo('${url}', '${t.id}')">⛶ 放大</button>
      </div>`;
    } else if (t.coverUrl) {
      resultHtml = `<div class="task-result"><img class="cover" src="${t.coverUrl}" alt="cover"></div>`;
    }
    const errHtml = (st === 'failed' || st === 'error') && t.error_message
      ? `<div class="task-err">❌ ${escapeHtml(t.error_message)}</div>` : '';
    const meta = [];
    if (t.duration) meta.push(t.duration);
    if (t.resolution) meta.push(t.resolution);
    if (t.ratio) meta.push(t.ratio);
    if (t.cost) meta.push(t.cost + ' 积分');
    const actions = [];
    if (t.video_play_url) actions.push(`<a class="btn" href="${withToken(t.video_play_url)}" target="_blank">▶ 预览</a>`);
    if (t.local_video) actions.push(`<a class="btn" href="${withToken('/api/download/' + t.id)}" download>⬇ 下载</a>`);
    if (t.video_url) actions.push(`<a class="btn" href="${t.video_url}" target="_blank">🌐 平台链接</a>`);
    div.innerHTML = `
      <div class="task-top">
        <span class="task-model">${t.model || ''}${meta.length ? ' · ' + meta.join(' · ') : ''}</span>
        <span class="status st-${st}">${statusLabel(st)}</span>
      </div>
      <div class="task-prompt">${escapeHtml(t.prompt || '')}</div>
      ${resultHtml}
      ${errHtml}
      <div class="task-meta">
        <span>创建：${t.createdAt || ''}</span>
        ${actions.join('')}
        ${st === 'succeeded' && !t.video_play_url ? '<span style="color:var(--muted)">（视频文件已过期清理）</span>' : ''}
      </div>`;
    box.appendChild(div);
  });
}


// ---------- 提示词 @ 引用素材 ----------
function insertMaterialRef(i) {
  const ta = $('prompt');
  ta.value = (ta.value + ' @' + (i + 1)).trim();
  ta.focus();
  toast('已插入 @' + (i + 1) + '，发送时将引用该素材');
}

$('prompt').addEventListener('keyup', (e) => {
  const pos = e.target.selectionStart;
  const val = e.target.value;
  if (val[pos - 1] === '@' && MATERIALS.length) showAtPicker(pos, val);
  else hideAtPicker();
});
$('prompt').addEventListener('click', () => { hideAtPicker(); });
$('prompt').addEventListener('blur', () => setTimeout(hideAtPicker, 200));

function showAtPicker(pos, val) {
  const box = $('at-picker');
  box.innerHTML = '';
  MATERIALS.forEach((m, i) => {
    const isImg = /\.(png|jpe?g|webp|gif|bmp)(\?|$)/i.test(m.url);
    const item = document.createElement('div');
    item.className = 'ap-item';
    item.innerHTML = `<span class="idx">${i + 1}</span>${isImg ? `<img src="${m.url}" alt="">` : '<span>📎</span>'}<span>引用这张素材</span>`;
    item.addEventListener('click', () => {
      const ta = $('prompt');
      ta.value = val.substring(0, pos - 1) + '@' + (i + 1) + val.substring(pos);
      ta.focus();
      hideAtPicker();
    });
    box.appendChild(item);
  });
  box.style.display = 'block';
}
function hideAtPicker() { $('at-picker').style.display = 'none'; }
function escapeHtml(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ---------- 轮询 ----------
function startPolling() {
  stopPolling();
  POLLING = setInterval(() => { if (getToken()) { loadTasks(); loadMe(); } }, 8000);
}
function stopPolling() {
  if (POLLING) { clearInterval(POLLING); POLLING = null; }
}

async function savePass() {
  const p1 = $('new-pass').value;
  const p2 = $('new-pass2').value;
  if (!p1 || p1.length < 4) { toast('密码至少 4 位', false); return; }
  if (p1 !== p2) { toast('两次输入的密码不一致', false); return; }
  const fd = new FormData();
  fd.append('new_password', p1);
  try {
    await apiJson('/api/password', { method: 'POST', body: fd });
    toast('密码已修改，请重新登录 ✅');
    closePassModal();
    doLogout();
  } catch (e) {
    toast('修改失败: ' + e.message, false);
  }
}

checkSession();




