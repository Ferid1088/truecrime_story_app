import { chromium } from '@playwright/test';
const EXE = process.env.HOME + '/Library/Caches/ms-playwright/chromium_headless_shell-1234/chrome-headless-shell-mac-arm64/chrome-headless-shell';
const PAGES = ['/', '/discover', '/cases', '/cases/1', '/research', '/studio?case=1', '/database', '/settings'];
const browser = await chromium.launch({ executablePath: EXE });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
for (const path of PAGES) {
  const page = await ctx.newPage();
  const logs = [];
  page.on('console', m => { if (m.type() !== 'info' && m.type() !== 'log') logs.push(`[${m.type()}] ${m.text()}`); });
  page.on('pageerror', e => logs.push(`[pageerror] ${e.message}`));
  page.on('requestfailed', r => logs.push(`[reqfail] ${r.url()} ${r.failure()?.errorText}`));
  page.on('response', r => { if (r.status() >= 400) logs.push(`[http ${r.status()}] ${r.url()}`); });
  const t0 = Date.now();
  await page.goto('http://localhost:3000' + path, { waitUntil: 'networkidle', timeout: 30000 }).catch(e => logs.push('[goto] ' + e.message));
  await page.waitForTimeout(2500);
  // detect remaining skeletons (elements with animate-pulse or skeleton class)
  const skeletons = await page.locator('.animate-pulse, [class*="skeleton"], .animate-spin').count().catch(() => -1);
  const text = await page.locator('body').innerText().catch(() => '(no body)');
  console.log(`\n########## ${path} (${Date.now() - t0}ms) skeletons=${skeletons} ##########`);
  console.log('--- console/network ---');
  console.log(logs.join('\n') || '(clean)');
  console.log('--- body (first 1500 chars) ---');
  console.log(text.slice(0, 1500));
  await page.close();
}
await browser.close();
