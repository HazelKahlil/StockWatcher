// Browser E2E: login -> dashboard -> manual refresh -> alerts -> history -> summary -> admin
import { chromium } from 'playwright';
const BASE = 'http://127.0.0.1:8000';
const results = [];
function log(name, ok, extra = '') { results.push({ name, ok, extra }); console.log(`${ok ? 'PASS' : 'FAIL'} ${name} ${extra}`); }

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });

// 1. login page
await page.goto(BASE + '/');
log('login page loads', (await page.title()).includes('登录'));
await page.fill('#username', 'admin1');
await page.fill('#password', 'admin-pass-2026test');
await page.click('button[type=submit]');
await page.waitForURL(BASE + '/', { timeout: 10000 });
await page.waitForSelector('#manual-refresh', { timeout: 10000 });
log('login redirects to dashboard', page.url() === BASE + '/');

// 2. dashboard state
await page.waitForFunction(() => document.querySelector('#svc-state')?.textContent.includes('预热') || document.querySelector('#svc-state')?.textContent.includes('健康') || document.querySelector('#svc-state')?.textContent.includes('启动'), { timeout: 15000 });
const svc = await page.textContent('#svc-state');
log('dashboard status bar renders', svc.includes('预热') || svc.includes('健康') || svc.includes('启动'), svc.trim());

// 3. WS connection
await page.waitForFunction(() => document.querySelector('#ws-state')?.textContent.includes('在线') || document.querySelector('#ws-state')?.textContent.includes('断开'), { timeout: 15000 });
log('websocket connected', (await page.textContent('#ws-state')).includes('在线'));

// 4. manual refresh button flow
await page.click('#manual-refresh');
await page.waitForFunction(() => document.querySelector('#command-state')?.textContent.includes('命令'), { timeout: 10000 });
log('manual refresh queued with command id', (await page.textContent('#command-state')).includes('命令'));

// 5. alerts page
await page.goto(BASE + '/alerts');
await page.waitForSelector('#alerts-history', { timeout: 10000 });
log('alerts page renders history table', await page.locator('#alerts-history table').count() > 0 || (await page.textContent('#alerts-history')).includes('暂无'));

// 6. history page
await page.goto(BASE + '/history');
await page.waitForSelector('#history', { timeout: 10000 });
log('history page renders', await page.locator('#history').count() > 0);

// 7. summary page
await page.goto(BASE + '/summary');
await page.waitForSelector('#summaries', { timeout: 10000 });
log('summary page renders', (await page.textContent('#summaries')).includes('盘后') || (await page.textContent('#summaries')).includes('暂无'));

// 8. admin page (admin role)
await page.goto(BASE + '/admin');
await page.waitForSelector('#token-form', { timeout: 10000 });
log('admin page renders token form', await page.locator('#token-form').count() > 0);
await page.waitForSelector('#diagnostics', { timeout: 15000 });
const diag = await page.textContent('#diagnostics');
log('admin diagnostics renders', diag.includes('Worker lease') || diag.includes('Schema'));
log('token input never pre-filled', (await page.inputValue('#token-input')) === '');

// 9. mobile width 360px
await page.setViewportSize({ width: 360, height: 700 });
await page.goto(BASE + '/');
await page.waitForSelector('#manual-refresh', { timeout: 10000 });
const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 2);
log('360px no horizontal overflow', !overflow);

// 10. logout
await page.setViewportSize({ width: 1280, height: 800 });
await page.click('#logout-btn');
await page.waitForSelector('#login-form', { timeout: 10000 });
log('logout returns to login', await page.locator('#login-form').count() > 0);

await browser.close();
const failed = results.filter(r => !r.ok);
console.log(`\nE2E RESULT: ${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
