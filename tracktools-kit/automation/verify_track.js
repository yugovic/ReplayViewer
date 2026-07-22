// verify_track.js — numeric track verification (pipeline stage 3).
//
// Asserts that the exported track actually grounds the car: for several lap
// positions, the AC mesh height under the car must resolve (raycast hit) and
// the car's ground-contact Y must match it within a tolerance. Screenshots are
// saved as diagnostics, not as the pass/fail signal. Starts the Vite dev
// server itself when the URL is not already serving.
//
//   node tracktools-kit/automation/verify_track.js --track fuji \
//        [--race fuji_aim_01] [--url http://localhost:5199] \
//        [--seeks 8,40,90,150] [--tolerance 0.25]
//
// Exit code 0 = all checks passed.
import { chromium } from "playwright";
import { spawn, execSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const REPO = join(dirname(fileURLToPath(import.meta.url)), "..", "..");

function arg(name, dflt) {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : dflt;
}
const TRACK = arg("track", "fuji");
const RACE = arg("race", "");
const URL_BASE = arg("url", "http://localhost:5199"); // vite.config pins 5199 (strictPort)
const SEEKS = arg("seeks", "8,40,90,150").split(",").map(Number);
const TOL = Number(arg("tolerance", "0.25"));
const OUT = join(REPO, "tracktools-kit", TRACK, "verify");
mkdirSync(OUT, { recursive: true });

async function serverUp(url) {
  try {
    const r = await fetch(url, { signal: AbortSignal.timeout(3000) });
    return r.ok;
  } catch {
    return false;
  }
}

let devProc = null;
async function ensureServer() {
  if (await serverUp(URL_BASE)) return;
  console.log("[verify] dev server not up — starting `npm run dev` ...");
  devProc = spawn("npm", ["run", "dev"], { cwd: REPO, shell: true, stdio: "ignore" });
  for (let i = 0; i < 60; i++) {
    await new Promise((r) => setTimeout(r, 1000));
    if (await serverUp(URL_BASE)) return;
  }
  throw new Error(`dev server did not come up at ${URL_BASE} within 60 s`);
}

function stopServer() {
  if (!devProc) return;
  try {
    if (process.platform === "win32") execSync(`taskkill /pid ${devProc.pid} /T /F`, { stdio: "ignore" });
    else devProc.kill("SIGTERM");
  } catch { /* already gone */ }
}

const failures = [];
try {
  await ensureServer();
  const b = await chromium.launch({
    channel: "chrome", headless: true,
    args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"],
  });
  const p = await b.newPage({ viewport: { width: 1500, height: 950 } });
  const errs = [];
  p.on("pageerror", (e) => errs.push(e.message));
  p.on("console", (m) => { if (m.type() === "error") errs.push(m.text()); });

  const url = `${URL_BASE}/?track=${TRACK}` + (RACE ? `&race=${RACE}` : "");
  await p.goto(url, { waitUntil: "networkidle" });
  try {
    await p.waitForSelector(".ls-close", { state: "visible", timeout: 15000 });
    await p.click(".ls-close");
    await p.waitForSelector(".ls-overlay", { state: "detached", timeout: 5000 });
  } catch { /* no landing modal */ }

  // Enable drive-on-AC FIRST: the scene/collision glbs are lazy-loaded on the
  // first enable, so waiting for acSceneReady before this would deadlock.
  await p.evaluate(() => {
    const s = window.__replayStore.getState();
    if (s.playing) s.togglePlaying();
    s.setDriveOnAc(true);
  });
  // NB: waitForFunction's 2nd param is `arg`, options come 3rd.
  await p.waitForFunction(() => window.__replayScene?.acSceneReady === true, null, { timeout: 90000 });
  await p.keyboard.press("1"); // chase cam for the diagnostic shots
  await p.waitForTimeout(1500);

  console.log(`[verify] ${TRACK}: car-vs-mesh height at t = ${SEEKS.join(", ")} s (tol ±${TOL} m)`);
  for (const t of SEEKS) {
    await p.evaluate((tt) => window.__replayStore.getState().seek(tt), t);
    await p.waitForTimeout(1200);
    const r = await p.evaluate(() => {
      const sc = window.__replayScene;
      return { carY: sc.carWorldY, ground: sc.debugGroundInfo() };
    });
    await p.screenshot({ path: join(OUT, `t${t}.png`) });
    const mesh = r.ground?.mesh ?? null;
    if (mesh === null) {
      failures.push(`t=${t}s: mesh height is null — car is off the collision surface`);
      console.log(`  t=${String(t).padStart(4)}s  carY=${r.carY?.toFixed(3)}  mesh=NULL  FAIL`);
      continue;
    }
    const dy = Math.abs(r.carY - mesh);
    const ok = dy <= TOL;
    if (!ok) failures.push(`t=${t}s: |carY - mesh| = ${dy.toFixed(3)} m > ${TOL}`);
    console.log(`  t=${String(t).padStart(4)}s  carY=${r.carY.toFixed(3)}  mesh=${mesh.toFixed(3)}` +
      `  d=${dy.toFixed(3)}  recon=${r.ground.recon.toFixed(3)}  ${ok ? "ok" : "FAIL"}`);
  }
  if (errs.length) console.log("[verify] page errors:", errs.slice(0, 5));
  await b.close();
} finally {
  stopServer();
}

if (failures.length) {
  console.error(`\n[verify] FAILED (${failures.length}):\n  ` + failures.join("\n  "));
  process.exit(1);
}
console.log(`\n[verify] PASS — screenshots in ${OUT}`);
