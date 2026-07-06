import { chromium } from 'playwright';
const browser = await chromium.launch({ channel: 'chrome' });
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
const errors = [];
page.on('pageerror', (e) => errors.push(String(e)));
await page.goto('http://localhost:5199/', { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(4500);
await page.locator('.ls-overlay').locator('button', { hasText: '1:37.007' }).click();
await page.waitForTimeout(1500);
await page.keyboard.press('3'); // top view full circuit
await page.waitForTimeout(1500);
await page.screenshot({ path: '/tmp/al_top.png', timeout: 90000 });
await page.keyboard.press('1'); // chase
// seek ~25%
const bar = page.locator('input[type="range"]').first();
if (await bar.count()) {
  await bar.fill('25').catch(() => {});
}
await page.waitForTimeout(3000);
await page.screenshot({ path: '/tmp/al_chase1.png', timeout: 90000 });
await page.waitForTimeout(12000);
await page.screenshot({ path: '/tmp/al_chase2.png', timeout: 90000 });
await page.waitForTimeout(15000);
await page.screenshot({ path: '/tmp/al_chase3.png', timeout: 90000 });
console.log('ERRORS:', JSON.stringify(errors));
await browser.close();
