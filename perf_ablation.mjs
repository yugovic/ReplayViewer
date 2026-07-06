// perf_ablation.mjs — GPU cost breakdown via route-level source patching.
// Patches Vite-served modules ONLY for this measurement page (no source edits,
// no effect on other tabs). Requires dev server on :5199.
// Usage: node perf_ablation.mjs
import { chromium } from 'playwright';

const DPR = Number(process.env.DPR || 2);
const W = 1440, H = 820;

// [find, replace] pairs applied to the Vite-transformed JS of each module.
const CONFIGS = [
  { name: 'A0 baseline (current app)', fx: [], scene: [] },
  {
    name: 'A1 canvas MSAA off',
    fx: [],
    scene: [['antialias: true', 'antialias: false']],
  },
  {
    name: 'A2 SSAO half-res',
    fx: [['samples: 16,', 'resolutionScale: 0.5,\n      samples: 16,']],
    scene: [],
  },
  {
    name: 'A3 quality=low (no SSAO/vignette)',
    fx: [[
      'constructor(renderer, scene, camera, quality = "high") {',
      'constructor(renderer, scene, camera, quality = "high") { quality = "low";',
    ]],
    scene: [],
  },
  {
    name: 'A4 no post (raw render)',
    fx: [[
      'this.composer = new EffectComposer(renderer);',
      'throw new Error("fx-ablation");',
    ]],
    scene: [],
  },
  {
    name: 'A5 no post + shadows off',
    fx: [[
      'this.composer = new EffectComposer(renderer);',
      'throw new Error("fx-ablation");',
    ]],
    scene: [['this.renderer.shadowMap.enabled = true;', 'this.renderer.shadowMap.enabled = false;']],
  },
  {
    name: 'A6 candidate: noMSAA + SSAO half/8 + PCF',
    fx: [['samples: 16,', 'resolutionScale: 0.5,\n      samples: 8,']],
    scene: [
      ['antialias: true', 'antialias: false'],
      ['THREE.PCFSoftShadowMap', 'THREE.PCFShadowMap'],
    ],
  },
];

function applyPatches(body, patches, label, warnings) {
  let out = body;
  for (const [find, replace] of patches) {
    if (!out.includes(find)) {
      warnings.push(`PATCH MISS in ${label}: ${find}`);
      continue;
    }
    out = out.replace(find, replace);
  }
  return out;
}

async function measure(page, label, ms) {
  return page.evaluate(async ({ label, ms }) => {
    const frames = [];
    await new Promise((resolve) => {
      let start;
      let prev;
      const tick = (now) => {
        if (start === undefined) { start = now; prev = now; requestAnimationFrame(tick); return; }
        frames.push(now - prev);
        prev = now;
        if (now - start < ms) requestAnimationFrame(tick); else resolve();
      };
      requestAnimationFrame(tick);
    });
    const dts = frames.slice().sort((a, b) => a - b);
    const avg = dts.reduce((a, b) => a + b, 0) / dts.length;
    const q = (p) => dts[Math.min(dts.length - 1, Math.floor(p * dts.length))];
    return {
      label,
      fps: +(1000 / avg).toFixed(1),
      p50: +q(0.5).toFixed(1),
      p95: +q(0.95).toFixed(1),
      pctOver17: +(100 * dts.filter((d) => d > 17.5).length / dts.length).toFixed(0),
    };
  }, { label, ms });
}

const browser = await chromium.launch({
  channel: 'chrome',
  args: ['--disable-background-timer-throttling', '--disable-renderer-backgrounding'],
});

const allRows = [];
for (const config of CONFIGS) {
  const context = await browser.newContext({ viewport: { width: W, height: H }, deviceScaleFactor: DPR });
  const page = await context.newPage();
  const warnings = [];
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));

  if (config.fx.length) {
    await page.route('**/src/engine/Effects.ts*', async (route) => {
      const resp = await route.fetch();
      const body = applyPatches(await resp.text(), config.fx, 'Effects.ts', warnings);
      await route.fulfill({ response: resp, body });
    });
  }
  if (config.scene.length) {
    await page.route('**/src/engine/ReplayScene.ts*', async (route) => {
      const resp = await route.fetch();
      const body = applyPatches(await resp.text(), config.scene, 'ReplayScene.ts', warnings);
      await route.fulfill({ response: resp, body });
    });
  }

  await page.goto('http://localhost:5199/', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(4500);
  await page.locator('.ls-overlay button', { hasText: '1:37.007' }).click();
  await page.waitForTimeout(1500);

  const chase = await measure(page, 'chase', 5000);
  await page.keyboard.press('3');
  await page.waitForTimeout(1000);
  const top = await measure(page, 'top', 4000);
  await page.keyboard.press('1');
  await page.waitForTimeout(500);
  await page.keyboard.press(' ');
  await page.waitForTimeout(300);
  const paused = await measure(page, 'paused', 3000);

  allRows.push({
    config: config.name,
    'chase fps': chase.fps, 'chase p50': chase.p50,
    'top fps': top.fps, 'top p50': top.p50,
    'paused fps': paused.fps,
    'over17%': chase.pctOver17,
  });
  if (warnings.length) console.log(`⚠ ${config.name}:`, warnings);
  const realErrors = errors.filter((e) => !e.includes('fx-ablation'));
  if (realErrors.length) console.log(`⚠ page errors in ${config.name}:`, realErrors);
  await context.close();
}

console.log(`\ndeviceScaleFactor=${DPR}, viewport ${W}x${H}`);
console.table(allRows);
await browser.close();
