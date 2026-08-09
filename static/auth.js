// 通用认证逻辑：用户端与管理端共用
const AUTH = window.AUTH || { tokenKey: 'seedance_token', loginPath: '/api/login', sessionPath: '/api/session' };

function getToken() { return localStorage.getItem(AUTH.tokenKey) || ''; }
function setToken(t) { localStorage.setItem(AUTH.tokenKey, t); }
function clearToken() { localStorage.removeItem(AUTH.tokenKey); }

async function apiFetch(path, opts = {}) {
  opts = opts || {};
  opts.headers = opts.headers || {};
  const token = getToken();
  if (token) opts.headers['Authorization'] = 'Bearer ' + token;
  if (opts.body && !(opts.body instanceof FormData) && typeof opts.body !== 'string') {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(opts.body);
  }
  const r = await fetch(path, opts);
  const skip401 = path === AUTH.loginPath || path === AUTH.sessionPath || path.indexOf('/api/admin/login') === 0;
  if (r.status === 401 && !skip401) {
    clearToken();
    if (window.onShowLogin) window.onShowLogin();
    const e = new Error('登录已过期，请重新登录');
    e.status = 401;
    throw e;
  }
  return r;
}

async function apiJson(path, opts = {}) {
  const r = await apiFetch(path, opts);
  let d = {};
  try { d = await r.json(); } catch (e) {}
  if (!r.ok) {
    const err = new Error(d.error || d.detail || ('请求失败 ' + r.status));
    err.status = r.status;
    throw err;
  }
  return d;
}

async function checkSession() {
  try {
    const r = await fetch(AUTH.sessionPath, { headers: getToken() ? { Authorization: 'Bearer ' + getToken() } : {} });
    const d = await r.json();
    if (d.logged_in) { if (window.onShowApp) window.onShowApp(); return d; }
    if (window.onShowLogin) window.onShowLogin();
    return d;
  } catch (e) {
    if (window.onShowLogin) window.onShowLogin();
    return { logged_in: false };
  }
}

async function doLogin(username, password) {
  const fd = new FormData();
  fd.append('username', username);
  fd.append('password', password);
  const d = await apiJson(AUTH.loginPath, { method: 'POST', body: fd });
  setToken(d.token);
  return d;
}

async function doLogout() {
  const logoutPath = AUTH.loginPath === '/api/admin/login' ? '/api/admin/logout' : '/api/logout';
  try { await apiFetch(logoutPath, { method: 'POST' }); } catch (e) {}
  clearToken();
  if (window.onShowLogin) window.onShowLogin();
}

function showLogin() { if (window.onShowLogin) window.onShowLogin(); }
function showApp() { if (window.onShowApp) window.onShowApp(); }

