const form = document.getElementById('login-form');
const errorBox = document.getElementById('login-error');

form?.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!errorBox) return;

  errorBox.hidden = true;
  const body = {
    username: document.getElementById('username')?.value ?? '',
    password: document.getElementById('password')?.value ?? '',
  };

  try {
    const response = await fetch('/api/v1/auth/login', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    if (response.ok) {
      window.location.assign('/');
      return;
    }
    const payload = await response.json().catch(() => ({}));
    errorBox.textContent = payload.error?.message || '登录失败，请重试。';
    errorBox.hidden = false;
  } catch {
    errorBox.textContent = '网络错误，请重试。';
    errorBox.hidden = false;
  }
});
