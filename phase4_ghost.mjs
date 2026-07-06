import { chromium } from 'playwright';
const browser = await chromium.launch({ channel: 'chrome' });
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
const errors = [];
page.on('pageerror', (e) => errors.push(String(e)));
await page.goto('http://localhost:5199/', { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(4000);
const modal = page.locator('.ls-overlay');
// main = #13 L8 (BEST)
await modal.locator('button', { hasText: '1:37.007' }).click();
await page.waitForTimeout(1200);
// reopen selector -> Ghost tab
await page.click('text=[change]');
await page.waitForTimeout(600);
await modal.locator('button', { hasText: 'Ghost' }).click();
await page.waitForTimeout(600);
await modal.locator('button', { hasText: '1:37.449' }).click();
await page.waitForTimeout(1500);
if (await modal.isVisible().catch(() => false)) {
  await modal.locator('button', { hasText: 'x' }).click().catch(() => {});
}
await page.click('button:has-text("Telemetry")');
await page.waitForTimeout(5000);
await page.screenshot({ path: '/tmp/p4_ghost.png', timeout: 90000 });
console.log('ERRORS:', JSON.stringify(errors));
await browser.close();
