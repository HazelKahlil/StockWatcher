// Shared StockWatcher Web client: CSRF, fetch helpers, WebSocket, notifications.
const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';

export function csrf() {
  return csrfToken;
}

export async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body && !headers['Content-Type']) {
    headers['Content-Type'] = 'application/json';
  }
  if (options.method && options.method !== 'GET' && options.method !== 'HEAD') {
    headers['X-CSRF-Token'] = csrfToken;
  }
  const response = await fetch(path, { ...options, headers });
  if (response.status === 401) {
    window.location.assign('/');
    throw new Error('unauthorized');
  }
  if (!response.ok) {
    const contentType = response.headers.get('content-type') || '';
    let payload = null;
    try {
      payload = contentType.includes('application/json')
        ? await response.json()
        : await response.text();
    } catch { /* retain null payload */ }
    const message = (payload && typeof payload === 'object' && payload.error?.message)
      || `请求失败 (${response.status})`;
    const error = new Error(message);
    error.status = response.status;
    error.code = payload && typeof payload === 'object' ? payload.error?.code : null;
    error.payload = payload;
    throw error;
  }
  return response;
}

export async function apiJson(path, options = {}) {
  const response = await api(path, options);
  return response.json();
}

let ws = null;
let lastEventId = null;
let reconnectAttempt = 0;
let reconnectTimer = null;
let reconnectStopped = false;
const listeners = new Set();

export function onEvent(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function wsState() {
  return ws?.readyState ?? WebSocket.CLOSED;
}

function notifyListeners(event) {
  for (const fn of listeners) {
    try {
      const result = fn(event);
      if (result && typeof result.catch === 'function') {
        result.catch(() => { /* async per-listener isolation */ });
      }
    } catch { /* synchronous per-listener isolation */ }
  }
}

export function connectEvents() {
  if (reconnectStopped || !navigator.onLine) return null;
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
    return ws;
  }
  const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
  const cursor = lastEventId == null ? '' : `?after_id=${lastEventId}`;
  ws = new WebSocket(`${protocol}://${window.location.host}/ws/v1/events${cursor}`);
  const stateEl = document.getElementById('ws-state');
  const setState = (label, cls) => {
    if (!stateEl) return;
    const state = cls === 'healthy' ? 'online' : cls === 'stale' ? 'stale' : 'connecting';
    stateEl.dataset.state = state;
    stateEl.setAttribute('aria-label', label);
    const labelEl = stateEl.querySelector('.status-label');
    if (labelEl) labelEl.textContent = label;
    else stateEl.textContent = label;
  };
  ws.addEventListener('open', () => {
    reconnectAttempt = 0;
    setState('实时连接在线', 'healthy');
  });
  ws.addEventListener('close', (event) => {
    ws = null;
    if (event.code === 4401 || event.code === 4403) {
      reconnectStopped = true;
      setState('登录状态已变化，请重新登录', 'stale');
      window.location.assign('/');
      return;
    }
    setState('实时连接断开，重连中…', 'stale');
    scheduleReconnect();
  });
  ws.addEventListener('message', (message) => {
    let event;
    try { event = JSON.parse(message.data); } catch { return; }
    if (event.event_type === 'server.hello' && lastEventId == null) {
      lastEventId = Number(event.payload?.latest_event_id || 0);
    } else if (event.event_type === 'server.resync_required') {
      lastEventId = Number(event.payload?.latest_event_id || 0);
    } else if (event.event_type !== 'server.hello' && event.event_id > lastEventId) {
      lastEventId = event.event_id;
    }
    notifyListeners(event);
  });
  return ws;
}

function scheduleReconnect() {
  if (reconnectStopped || reconnectTimer || !navigator.onLine) return;
  const baseDelay = Math.min(30000, 1000 * (2 ** reconnectAttempt));
  const hiddenPenalty = document.hidden ? 2 : 1;
  const jitter = Math.floor(Math.random() * 500);
  const delay = Math.min(30000, baseDelay * hiddenPenalty) + jitter;
  reconnectAttempt += 1;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connectEvents();
  }, delay);
}

window.addEventListener('online', () => {
  if (!reconnectStopped) {
    reconnectAttempt = 0;
    connectEvents();
  }
});

window.addEventListener('offline', () => {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
});

document.addEventListener('visibilitychange', () => {
  if (!document.hidden && !reconnectStopped && (!ws || ws.readyState === WebSocket.CLOSED)) {
    connectEvents();
  }
});

export function requestNotificationPermission() {
  if (!('Notification' in window)) return Promise.resolve('unsupported');
  if (Notification.permission === 'granted') return Promise.resolve('granted');
  return Notification.requestPermission();
}

export function notify(title, body) {
  if (!('Notification' in window) || Notification.permission !== 'granted') return;
  new Notification(title, { body });
}

export function esc(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

export function fmtTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleString('zh-CN', {
    timeZone: 'Asia/Shanghai',
    hour12: false,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

// Logout (CSRF protected)
document.addEventListener('DOMContentLoaded', () => {
  const button = document.getElementById('logout-btn');
  if (button) {
    button.addEventListener('click', async () => {
      try {
        await api('/api/v1/auth/logout', { method: 'POST' });
      } finally {
        window.location.assign('/');
      }
    });
  }
});
