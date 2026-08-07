import { api, apiJson, esc, fmtTime } from './app.js';

async function loadDiagnostics() {
  const wrap = document.getElementById('diagnostics');
  try {
    const payload = await apiJson('/api/v1/admin/diagnostics');
    const worker = payload.worker || {};
    const db = payload.database || {};
    wrap.innerHTML = `
    <table>
      <tbody>
        <tr><th>Worker lease</th><td>${worker.lease ? `holder ${esc(worker.lease.holder_id)} · heartbeat ${Math.round(worker.heartbeat_age_seconds ?? -1)}s 前` : '无'}</td></tr>
        <tr><th>Schema</th><td>v${db.schema_version}</td></tr>
        <tr><th>数据库大小</th><td>${(db.size_bytes / 1024).toFixed(1)} KiB</td></tr>
        <tr><th>事件游标</th><td>${db.events_minimum_id} .. ${db.events_latest_id}</td></tr>
      </tbody>
    </table>`;
  } catch {
    wrap.innerHTML = '<p class="error">诊断加载失败。</p>';
  }
}

async function loadScanRuns() {
  const wrap = document.getElementById('scan-runs');
  try {
    const payload = await apiJson('/api/v1/admin/scan-runs?limit=20');
    const rows = payload.items || [];
    wrap.innerHTML = rows.length
      ? `
      <table>
        <thead><tr><th>#</th><th>时间</th><th>触发</th><th>健康</th><th>覆盖</th><th>耗时</th></tr></thead>
        <tbody>
          ${rows.map((row) => `
            <tr>
              <td>${row.id}</td>
              <td>${fmtTime(row.completed_at)}</td>
              <td>${esc(row.trigger_type)}</td>
              <td>${esc(row.health)}</td>
              <td>${row.coverage_ratio != null ? (row.coverage_ratio * 100).toFixed(1) + '%' : '—'}</td>
              <td>${row.elapsed_seconds != null ? row.elapsed_seconds.toFixed(1) + 's' : '—'}</td>
            </tr>`).join('')}
        </tbody>
      </table>`
      : '<p class="muted">暂无扫描记录。</p>';
  } catch {
    wrap.innerHTML = '<p class="error">扫描记录加载失败。</p>';
  }
}

async function loadUsers() {
  const wrap = document.getElementById('users');
  try {
    const payload = await apiJson('/api/v1/admin/users');
    const rows = payload.items || [];
    wrap.innerHTML = rows.length
      ? `
      <table>
        <thead><tr><th>ID</th><th>用户名</th><th>角色</th><th>状态</th><th>最后登录</th></tr></thead>
        <tbody>
          ${rows.map((row) => `
            <tr>
              <td>${row.user_id}</td>
              <td>${esc(row.username)}</td>
              <td>${esc(row.role)}</td>
              <td>${row.active ? '启用' : '停用'}</td>
              <td>${row.last_login_at ? fmtTime(row.last_login_at) : '—'}</td>
            </tr>`).join('')}
        </tbody>
      </table>`
      : '<p class="muted">暂无用户。</p>';
  } catch {
    wrap.innerHTML = '<p class="error">用户列表加载失败。</p>';
  }
}

document.addEventListener('DOMContentLoaded', () => {
  loadDiagnostics();
  loadScanRuns();
  loadUsers();
  const tokenForm = document.getElementById('token-form');
  const result = document.getElementById('token-result');
  tokenForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    result.hidden = true;
    const token = document.getElementById('token-input').value;
    if (!token) return;
    try {
      const response = await api('/api/v1/admin/token', {
        method: 'PUT',
        body: JSON.stringify({ token }),
      });
      const payload = await response.json();
      result.textContent = `已排队测试并激活：command ${payload.command_id}（fingerprint ${payload.fingerprint}）。先测后激活，失败保留旧 Token。`;
      result.hidden = false;
      document.getElementById('token-input').value = '';
    } catch (error) {
      result.textContent = error.message;
      result.hidden = false;
    }
  });
  document.getElementById('cache-refresh').addEventListener('click', async () => {
    try {
      await api('/api/v1/admin/cache/refresh', { method: 'POST', body: '{}' });
      alert('缓存刷新命令已排队。');
    } catch (error) {
      alert(error.message);
    }
  });
  const userForm = document.getElementById('user-form');
  userForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const body = JSON.stringify({
      username: document.getElementById('new-username').value,
      password: document.getElementById('new-password').value,
      role: document.getElementById('new-role').value,
    });
    try {
      await api('/api/v1/admin/users', { method: 'POST', body });
      userForm.reset();
      await loadUsers();
    } catch (error) {
      alert(error.message);
    }
  });
});
