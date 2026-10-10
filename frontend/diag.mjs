import { chromium } from '@playwright/test';
const EXE = process.env.PW_CHROMIUM_EXE || undefined; // set PW_CHROMIUM_EXE to use a specific browser binary
const browser = await chromium.launch({ executablePath: EXE });
const page = await browser.newPage();
const logs = [];
page.on('console', m => logs.push(`[${m.type()}] ${m.text()}`));
page.on('pageerror', e => logs.push(`[pageerror] ${e.message}`));
page.on('requestfailed', r => logs.push(`[reqfail] ${r.url()} ${r.failure()?.errorText}`));
page.on('response', r => { if (r.status() >= 400) logs.push(`[http ${r.status()}] ${r.url()}`); });
const url = process.argv[2] || 'http://localhost:3000/';
await page.goto(url, { waitUntil: 'networkidle', timeout: 30000 }).catch(e => logs.push('[goto] ' + e.message));
await page.waitForTimeout(4000);
const text = await page.locator('body').innerText().catch(() => '(no body)');
console.log('=== CONSOLE/NETWORK ===');
console.log(logs.join('\n') || '(clean)');
console.log('=== BODY TEXT ===');
console.log(text.slice(0, 3000));
await browser.close();
