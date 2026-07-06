// perf_fps.mjs — FPS / frame-time / draw-call measurement harness.
// Usage: node perf_fps.mjs            (dpr=2, real-world Retina)
//        DPR=1 node perf_fps.mjs      (fill-rate A/B)
//        W=1440 H=820 node perf_fps.mjs
// Dev server must be running on port 5199 (npm run dev).
import { chromium } from 'playwright';

const DPR = Number(process.env.DPR || 2);
const W = Number(process.env.W || 1440);
const H = Number(process.env.H || 820);

const browser = await chromium.launch({
  channel: 'chrome',
  args: ['--disable-background-timer-throttling', '--disable-renderer-backgrounding'],
});
const context = await browser.newContext({ viewport: { width: W, height: H }, deviceScaleFactor: DPR });
const page = await context.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push(String(e)));

// Count WebGL draw calls + triangles per frame (injected before app scripts run)
await page.addInitScript(() => {
  const stats = { draws: 0, tris: 0, programSwitches: 0 };
  window.__glStats = stats;
  const wrap = (proto) => {
    if (!proto) return;
    const de = proto.drawElements;
    proto.drawElements = function (mode, count, type, offset) {
      stats.draws++; if (mode === 4) stats.tris += count / 3;
      return de.call(this, mode, count, type, offset);
    };
    const da = proto.drawArrays;
    proto.drawArrays = function (mode, first, count) {
      stats.draws++; if (mode === 4) stats.tris += count / 3;
      return da.call(this, mode, first, count);
    };
    const up = proto.useProgram;
    proto.useProgram = function (p) { stats.programSwitches++; return up.call(this, p); };
  };
  wrap(window.WebGLRenderingContext && WebGLRenderingContext.prototype);
  wrap(window.WebGL2RenderingContext && WebGL2RenderingContext.prototype);
});

async function measure(label, ms) {
  return page.evaluate(async ({ label, ms }) => {
    const stats = window.__glStats;
    const frames = [];
    const longTasks = [];
    let po = null;
    try {
      po = new PerformanceObserver((list) => {
        for (const e of list.getEntries()) longTasks.push(e.duration);
      });
      po.observe({ entryTypes: ['longtask'] });
    } catch { /* longtask unsupported */ }
    const heap0 = performance.memory ? performance.memory.usedJSHeapSize : 0;

    await new Promise((resolve) => {
      let start;
      let prev;
      let lastDraws = 0;
      let lastTris = 0;
      const tick = (now) => {
        if (start === undefined) {
          start = now; prev = now;
          lastDraws = stats.draws; lastTris = stats.tris;
          requestAnimationFrame(tick);
          return;
        }
        frames.push({ dt: now - prev, draws: stats.draws - lastDraws, tris: stats.tris - lastTris });
        prev = now; lastDraws = stats.draws; lastTris = stats.tris;
        if (now - start < ms) requestAnimationFrame(tick);
        else resolve();
      };
      requestAnimationFrame(tick);
    });

    if (po) po.disconnect();
    const heap1 = performance.memory ? performance.memory.usedJSHeapSize : 0;
    const dts = frames.map((f) => f.dt).sort((a, b) => a - b);
    const q = (p) => dts[Math.min(dts.length - 1, Math.floor(p * dts.length))];
    const avg = dts.reduce((a, b) => a + b, 0) / dts.length;
    const canvas = document.querySelector('.viewer-canvas canvas');
    return {
      label,
      frames: frames.length,
      fps: +(1000 / avg).toFixed(1),
      p50: +q(0.5).toFixed(1),
      p95: +q(0.95).toFixed(1),
      p99: +q(0.99).toFixed(1),
      worst: +dts[dts.length - 1].toFixed(1),
      pctOver17: +(100 * dts.filter((d) => d > 17.5).length / dts.length).toFixed(1),
      draws: Math.round(frames.reduce((a, f) => a + f.draws, 0) / frames.length),
      ktris: Math.round(frames.reduce((a, f) => a + f.tris, 0) / frames.length / 1000),
      longTasks: longTasks.length,
      heapMB: +((heap1 - heap0) / 1048576).toFixed(1),
      dpr: devicePixelRatio,
      canvasPx: canvas ? `${canvas.width}x${canvas.height}` : 'n/a',
    };
  }, { label, ms });
}

const results = [];

// 0. rAF ceiling on a blank page (display refresh cap for this environment)
await page.goto('about:blank');
results.push(await measure('blank-page rAF ceiling', 2000));

// 1. Load app and pick the reference lap
await page.goto('http://localhost:5199/', { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(4500); // GLB + satellite + terrain async loads
const modal = page.locator('.ls-overlay');
await modal.locator('button', { hasText: '1:37.007' }).click();
await page.waitForTimeout(1500);

// GPU renderer string
const gpu = await page.evaluate(() => {
  const c = document.createElement('canvas');
  const gl = c.getContext('webgl2') || c.getContext('webgl');
  if (!gl) return 'n/a';
  const ext = gl.getExtension('WEBGL_debug_renderer_info');
  return ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : 'masked';
});

// 2. Camera scenarios (playing)
results.push(await measure('chase (default)', 6000));
for (const [key, name] of [['5', 'tv'], ['6', 'cinematic'], ['2', 'cockpit'], ['3', 'top']]) {
  await page.keyboard.press(key);
  await page.waitForTimeout(1200);
  results.push(await measure(name, 5000));
}
await page.keyboard.press('1');
await page.waitForTimeout(800);

// 3. Telemetry panel open (uPlot cursor updates every frame)
await page.locator('.telemetry-toggle').click();
await page.waitForTimeout(800);
results.push(await measure('chase + telemetry panel', 5000));
await page.locator('.telemetry-close').click();
await page.waitForTimeout(400);

// 4. Ghost lap enabled
await page.click('text=[change]');
await page.waitForTimeout(600);
await modal.locator('button', { hasText: 'Ghost' }).click();
await page.waitForTimeout(400);
await modal.locator('button', { hasText: '1:37.449' }).click();
await page.waitForTimeout(2000);
results.push(await measure('chase + ghost', 5000));

// 5. Ghost + telemetry panel (worst realistic case)
await page.locator('.telemetry-toggle').click();
await page.waitForTimeout(800);
results.push(await measure('chase + ghost + telemetry', 5000));

// 6. Paused (static scene control)
await page.keyboard.press(' ');
await page.waitForTimeout(400);
results.push(await measure('paused (static)', 3000));

console.log(`\nGPU: ${gpu}`);
console.log(`viewport ${W}x${H} css, deviceScaleFactor=${DPR}`);
console.table(results);
console.log('page errors:', errors.length ? errors : 'none');
await browser.close();
