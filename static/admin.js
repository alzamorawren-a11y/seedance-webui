// Seedance WebUI 管理后台逻辑
let ALL_MODELS = [];
let USERS = [];
let POINTS_TARGET_ID = null;

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
  $('admin-view').style.display = 'none';
};
window.onShowApp = function () {
  $('login-view').style.display = 'none';
  $('admin-view').style.display = 'block';
  loadAll();
};

$('login-btn').addEventListener('click', async () => {
  const u = $('login-username').value.trim();
  const p = $('login-password').value;
  $('login-err').textContent = '';
  $('login-btn').disabled = true;
  $('login-btn').textContent = '登录中…';
  try {
    const d = await doLogin(u, p);
    $('login-password').value = '';
    $('admin-name').textContent = d.username;
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

// tabs
document.querySelectorAll('.tab[data-panel]').forEach(t => {
  t.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
    document.querySelectorAll('.panel').forEach(x => x.classList.remove('active'));
    t.classList.add('active');
    $(t.dataset.panel).classList.add('active');
    if (t.dataset.panel === 'panel-tasks') loadAdminTasks();
    if (t.dataset.panel === 'panel-users') loadUsers();
    if (t.dataset.panel === 'panel-models') { loadFeatures(); loadModels(true); }
    if (t.dataset.panel === 'panel-settings') loadSettings();
    if (t.dataset.panel === 'panel-logs') loadLogs();
  });
});

async function loadAll() {
  await Promise.all([loadStats(), loadUsers(), loadFeatures(), loadModels(false), loadAdminTasks(), loadSettings(), loadLogs()]);
}

async function loadStats() {
  try {
    const d = await apiJson('/api/admin/stats');
    $('stats').innerHTML = `
      <div class="stat"><div class="n">${d.users}</div><div class="l">用户数</div></div>
      <div class="stat"><div class="n">${d.tasks}</div><div class="l">任务总数</div></div>
      <div class="stat"><div class="n">${d.total_cost}</div><div class="l">总消耗积分</div></div>
      <div class="stat"><div class="n">${d.banned}</div><div class="l">已拉黑</div></div>`;
  } catch (e) {}
}

// ---------- 用户管理 ----------
$('btn-create-user').addEventListener('click', async () => {
  const username = $('nu-username').value.trim();
  const password = $('nu-password').value.trim();
  const points = $('nu-points').value || '0';
  if (!username || !password) { toast('请填写账号和密码', false); return; }
  const fd = new FormData();
  fd.append('username', username);
  fd.append('password', password);
  fd.append('points', points);
  try {
    await apiJson('/api/admin/users', { method: 'POST', body: fd });
    toast('账号已创建 ✅');
    $('nu-username').value = ''; $('nu-password').value = '';
    await loadUsers(); await loadStats();
  } catch (e) {
    toast('创建失败: ' + e.message, false);
  }
});

async function loadUsers() {
  try {
    const d = await apiJson('/api/admin/users');
    USERS = d.users || [];
    renderUsers();
    // 更新筛选下拉
    const sel = $('task-user-filter');
    const cur = sel.value;
    sel.innerHTML = '<option value="0">全部用户</option>' + USERS.map(u => `<option value="${u.id}">${escapeHtml(u.username)}</option>`).join('');
    if (cur) sel.value = cur;
  } catch (e) {}
}

function renderUsers() {
  const box = $('user-list');
  if (!USERS.length) { box.innerHTML = '<div class="empty">暂无用户</div>'; return; }
  let html = '<table><thead><tr><th>账号</th><th>积分</th><th>状态</th><th>创建时间</th><th style="min-width:220px">操作</th></tr></thead><tbody>';
  USERS.forEach(u => {
    const st = u.status === 'banned'
      ? '<span class="badge err">已拉黑</span>'
      : '<span class="badge ok">正常</span>';
    html += `<tr class="model-row">
      <td><b>${escapeHtml(u.username)}</b></td>
      <td>${u.points} 积分</td>
      <td>${st}</td>
      <td style="color:var(--muted);font-size:12px">${u.created_at || ''}</td>
      <td>
                <button class="btn" data-act="points" data-id="${u.id}">积分</button>
        <button class="btn" data-act="tasks" data-id="${u.id}">任务</button>
        ${u.status === 'banned'
          ? `<button class="btn" data-act="unban" data-id="${u.id}">解封</button>`
          : `<button class="btn" data-act="ban" data-id="${u.id}">拉黑</button>`}
        <button class="btn btn-danger" data-act="del" data-id="${u.id}">删除</button>
      </td>
    </tr>`;
  });
  html += '</tbody></table>';
  box.innerHTML = html;
  box.querySelectorAll('button[data-act]').forEach(b => {
    b.addEventListener('click', () => userAction(b.dataset.act, b.dataset.id));
  });
}

async function userAction(act, id) {

  if (act === 'points') {
    POINTS_TARGET_ID = id;
    const u = USERS.find(x => x.id == id);
    $('points-modal-title').textContent = `积分调整 · ${u ? u.username : ''}（当前 ${u ? u.points : 0} 积分）`;
    $('points-modal-amount').value = '';
    $('points-modal').classList.add('open');
    return;
  }
  if (act === 'tasks') {
    $('task-user-filter').value = id;
    document.querySelector('.tab[data-panel="panel-tasks"]').click();
    return;
  }
  if (act === 'ban') {
    if (!confirm('确定拉黑该账号？拉黑后立即无法登录。')) return;
    try { await apiJson(`/api/admin/users/${id}/ban`, { method: 'POST' }); toast('已拉黑 ✅'); }
    catch (e) { toast(e.message, false); }
  }
  if (act === 'unban') {
    try { await apiJson(`/api/admin/users/${id}/unban`, { method: 'POST' }); toast('已解封 ✅'); }
    catch (e) { toast(e.message, false); }
  }
  if (act === 'del') {
    if (!confirm('确定删除该账号？其所有任务记录和积分数据将一并删除！')) return;
    try { await apiJson(`/api/admin/users/${id}/delete`, { method: 'POST' }); toast('已删除 ✅'); }
    catch (e) { toast(e.message, false); }
  }
  await Promise.all([loadUsers(), loadStats()]);
}

$('btn-points-save').addEventListener('click', async () => {
  const amt = $('points-modal-amount').value;
  if (amt === '' || isNaN(parseFloat(amt))) { toast('请输入积分数值', false); return; }
  const fd = new FormData();
  fd.append('amount', amt);
  try {
    await apiJson(`/api/admin/users/${POINTS_TARGET_ID}/points`, { method: 'POST', body: fd });
    toast('积分已调整 ✅');
    $('points-modal').classList.remove('open');
    await Promise.all([loadUsers(), loadStats()]);
  } catch (e) {
    toast('调整失败: ' + e.message, false);
  }
});

// ---------- 任务总览 ----------
$('btn-refresh-tasks').addEventListener('click', loadAdminTasks);
$('task-user-filter').addEventListener('change', loadAdminTasks);

async function loadAdminTasks() {
  const uid = $('task-user-filter').value || '0';
  try {
    const d = await apiJson('/api/admin/tasks?user_id=' + uid);
    renderAdminTasks(d.tasks || []);
  } catch (e) {
    $('admin-task-list').innerHTML = '<div class="empty">任务加载失败</div>';
  }
}

function statusLabel(s) {
  const map = { pending: '排队中', running: '生成中', succeeded: '已完成', failed: '失败', error: '错误' };
  return map[s] || s;
}

function renderAdminTasks(tasks) {
  const box = $('admin-task-list');
  if (!tasks.length) { box.innerHTML = '<div class="empty">暂无任务</div>'; return; }
  box.innerHTML = '';
  tasks.forEach(t => {
    const div = document.createElement('div');
    div.className = 'task';
    const st = t.status || 'pending';
    const meta = [t.duration, t.resolution, t.ratio].filter(Boolean).join(' · ');
    let video = '';
    if (st === 'succeeded') {
      const src = t.local_video ? '/api/admin/download/' + t.id : (t.video_url || '');
      if (src) video = `<video controls src="${src}"></video>`;
    }
    const keepLabel = t.keep_forever ? '取消永久保留' : '永久保留';
    div.innerHTML = `
      <div class="t-top">
        <span class="t-user">${escapeHtml(t.username || '')}</span>
        <span class="task-model">${t.model || ''}${meta ? ' · ' + meta : ''}${t.cost ? ' · ' + t.cost + ' 积分' : ''}</span>
        <span class="status st-${st}">${statusLabel(st)}</span>
      </div>
      <div class="t-sub">${escapeHtml(t.prompt || '')}</div>
      ${t.error_message && (st === 'failed' || st === 'error') ? `<div class="t-sub" style="color:var(--err)">${escapeHtml(t.error_message)}</div>` : ''}
      ${video}
      <div class="t-actions">
        <span style="font-size:12px;color:var(--muted)">创建：${t.createdAt || ''}</span>
        ${t.local_video ? `<a class="btn" href="/api/admin/download/${t.id}" download>⬇ 下载</a>` : ''}
        <button class="btn" data-keep="${t.id}" data-v="${t.keep_forever ? 0 : 1}">${keepLabel}</button>
        <button class="btn btn-danger" data-del="${t.id}">删除</button>
      </div>`;
    box.appendChild(div);
  });
  box.querySelectorAll('[data-del]').forEach(b => b.addEventListener('click', async () => {
    if (!confirm('确定删除该任务记录及本地视频？')) return;
    const fd = new FormData();
    fd.append('task_id', b.dataset.del);
    try { await apiJson('/api/admin/tasks/delete', { method: 'POST', body: fd }); toast('已删除 ✅'); loadAdminTasks(); loadStats(); }
    catch (e) { toast(e.message, false); }
  }));
  box.querySelectorAll('[data-keep]').forEach(b => b.addEventListener('click', async () => {
    const fd = new FormData();
    fd.append('keep', b.dataset.v);
    try { await apiJson('/api/admin/tasks/' + b.dataset.keep + '/keep', { method: 'POST', body: fd }); toast('已更新 ✅'); loadAdminTasks(); }
    catch (e) { toast(e.message, false); }
  }));
}

// ---------- 功能与模型 ----------
async function loadFeatures() {
  try {
    const d = await apiJson('/api/admin/features');
    document.querySelectorAll('.switch input[data-key]').forEach(el => {
      el.checked = d.features[el.dataset.key] !== false;
    });
  } catch (e) {}
}

$('btn-save-features').addEventListener('click', async () => {
  const fd = new FormData();
  document.querySelectorAll('.switch input[data-key]').forEach(el => fd.append(el.dataset.key, el.checked ? '1' : '0'));
  fd.append('enabled_models', '');
  try {
    await apiJson('/api/admin/features', { method: 'POST', body: fd });
    toast('功能设置已保存 ✅');
  } catch (e) { toast(e.message, false); }
});

$('btn-refresh-models').addEventListener('click', () => loadModels(true));

async function loadModels(force = false) {
  $('model-load-state').textContent = force ? '拉取中…' : '';
  try {
    const d = await apiJson('/api/admin/models' + (force ? '?force=1' : ''));
    ALL_MODELS = d.models || [];
    renderModelTable();
    $('model-load-state').textContent = `共 ${ALL_MODELS.length} 个模型`;
  } catch (e) {
    $('model-table-wrap').innerHTML = `<div class="empty">模型加载失败：${escapeHtml(e.message)}</div>`;
  }
}

function renderModelTable() {
  const wrap = $('model-table-wrap');
  if (!ALL_MODELS.length) { wrap.innerHTML = '<div class="empty">暂无模型</div>'; return; }
  let html = '<table><thead><tr><th>启用</th><th>模型</th><th>积分/秒</th><th>状态</th></tr></thead><tbody>';
  ALL_MODELS.forEach((m, i) => {
    html += `<tr class="model-row">
      <td><label class="switch"><input type="checkbox" class="m-enable" data-idx="${i}" ${m.enabled !== false ? 'checked' : ''}><span class="slider"></span></label></td>
      <td><b>${escapeHtml(m.id)}</b><div style="color:var(--muted);font-size:12px">${escapeHtml(m.description || '')}</div></td>
      <td><input type="number" class="m-cost" data-idx="${i}" min="0" step="0.1" value="${m.configured ? m.cost_per_second : ''}" placeholder="默认 ${m.platform_price || 0}"></td>
      <td>${m.configured ? '<span class="badge ok">自定义积分</span>' : '<span class="badge">平台默认</span>'}</td>
    </tr>`;
  });
  html += '</tbody></table>';
  wrap.innerHTML = html;
}

$('btn-save-models').addEventListener('click', async () => {
  const fd = new FormData();
  document.querySelectorAll('.switch input[data-key]').forEach(el => fd.append(el.dataset.key, el.checked ? '1' : '0'));
  const enabled = ALL_MODELS.filter((m, i) => {
    const cb = document.querySelector(`.m-enable[data-idx="${i}"]`);
    return cb && cb.checked;
  }).map(m => m.id);
  fd.append('enabled_models', enabled.join(','));
  const costUpdates = [];
  ALL_MODELS.forEach((m, i) => {
    const inp = document.querySelector(`.m-cost[data-idx="${i}"]`);
    if (!inp || inp.value.trim() === '') return;
    costUpdates.push({ id: m.id, cost: inp.value.trim() });
  });
  try {
    await apiJson('/api/admin/features', { method: 'POST', body: fd });
    for (const u of costUpdates) {
      const cfd = new FormData();
      cfd.append('model_id', u.id);
      cfd.append('cost_per_second', u.cost);
      await apiJson('/api/admin/pricing', { method: 'POST', body: cfd });
    }
    toast('模型设置与积分已保存 ✅');
    await loadModels(true);
  } catch (e) {
    toast('保存失败: ' + e.message, false);
  }
});

$('btn-reset-pricing').addEventListener('click', async () => {
  if (!confirm('确定重置全部模型积分为平台默认？')) return;
  try {
    await apiJson('/api/admin/pricing/reset', { method: 'POST' });
    toast('已重置 ✅');
    await loadModels(true);
  } catch (e) { toast(e.message, false); }
});

// ---------- 系统设置 ----------
async function loadSettings() {
  try {
    const d = await apiJson('/api/admin/settings');
    const s = d.settings || {};
    $('set-retention').value = s.retention_days || '30';
    $('set-concurrent').value = s.max_concurrent || '3';
    $('set-baseurl').value = '';
    $('set-baseurl').placeholder = s.base_url ? '已配置（如需修改请填写完整接入地址）' : 'https://';
    $('set-platform-key-masked').textContent = s.platform_key_masked ? ('当前：' + s.platform_key_masked) : '（未配置）';
  } catch (e) {}
}

$('btn-save-settings').addEventListener('click', async () => {
  const fd = new FormData();
  fd.append('retention_days', $('set-retention').value);
  fd.append('max_concurrent', $('set-concurrent').value);
  fd.append('base_url', $('set-baseurl').value);
  const pk = $('set-platform-key').value.trim();
  if (pk) fd.append('platform_key', pk);
  try {
    await apiJson('/api/admin/settings', { method: 'POST', body: fd });
    toast('设置已保存 ✅');
    $('set-platform-key').value = '';
    await loadSettings();
  } catch (e) { toast(e.message, false); }
});

$('btn-admin-pass').addEventListener('click', async () => {
  const p1 = $('admin-new-pass').value;
  const p2 = $('admin-new-pass2').value;
  if (!p1 || p1.length < 4) { toast('密码至少 4 位', false); return; }
  if (p1 !== p2) { toast('两次输入的密码不一致', false); return; }
  const fd = new FormData();
  fd.append('new_password', p1);
  try {
    await apiJson('/api/admin/password', { method: 'POST', body: fd });
    toast('密码已修改，请重新登录 ✅');
    $('admin-new-pass').value = ''; $('admin-new-pass2').value = '';
    doLogout();
  } catch (e) { toast(e.message, false); }
});

// ---------- 日志 ----------
async function loadLogs() {
  try {
    const d = await apiJson('/api/admin/logs');
    const box = $('audit-log-list');
    const logs = d.logs || [];
    if (!logs.length) { box.innerHTML = '<div class="empty">暂无记录</div>'; return; }
    box.innerHTML = logs.map(l => `
      <div class="log-row">
        <span>${escapeHtml(l.action)} · ${escapeHtml(l.detail)}</span>
        <span class="t">${l.admin_name} · ${l.created_at}</span>
      </div>`).join('');
  } catch (e) {}
  try {
    const d = await apiJson('/api/admin/password_logs');
    const box = $('pw-log-list');
    const logs = d.logs || [];
    if (!logs.length) { box.innerHTML = '<div class="empty">暂无记录</div>'; return; }
    box.innerHTML = logs.map(l => `
      <div class="log-row">
        <span>${escapeHtml(l.username)} 修改了密码（${l.changed_by === 'self' ? '用户本人' : '管理员'}）</span>
        <span class="t">${l.created_at}</span>
      </div>`).join('');
  } catch (e) {}
}

function escapeHtml(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

checkSession();






