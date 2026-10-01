// Runtime audit of the track-limit GPS registration in the real viewer.
// Measures all four tyres against the traced CG road for every 0.1 s of each
// distributed lap, raw vs registered, through the normal sampleReplay path and
// the live HUD toggle (no reload). Usage: node scripts/quality/verify-fuji-gps-registration.mjs
import { chromium } from 'playwright';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import assert from 'node:assert/strict';

const out = process.env.GPS_RUNTIME_OUT || 'artifacts/gps-accuracy-2026-09-22/runtime';
await mkdir(out, { recursive: true });
const base = process.env.GPS_RUNTIME_BASE || 'http://127.0.0.1:5199/';
const races = [
  { race: 'fuji_aim_01', surface: 'wet', shots: { 3: [18, 22, 55, 76, 84, 85, 97, 107.6, 122, 132] } },
  { race: 'fuji_aim_2020_07_30', surface: 'dry', shots: { 5: [17.2, 33.3, 52.6, 72, 79.6, 98.4] } },
];
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
const errors = [];
page.on('pageerror', (e) => errors.push(e.message));
const report = { generated: new Date().toISOString(), definition: 'Tyre bottom outer section point of the displayed rig, including steering/pitch/roll; signed lateral gap to the rendered road world borders. Nominal tyre width is not a measured contact patch.', races: {}, errors };
try {

for (const { race, surface, shots } of races) {
  const file = JSON.parse(await readFile(`public/data/races/${race}/gps_registration.json`, 'utf8'));
  const index = JSON.parse(await readFile(`public/data/races/${race}/laps.json`, 'utf8'));
  await page.goto(`${base}?track=fuji&race=${race}&look=cg`);
  await page.waitForFunction(() => window.__replayScene?.gltfModel &&
    window.__replayScene.scene.getObjectByName('fuji-cg-study')?.userData.status === 'ready', null, { timeout: 120000 });
  await page.evaluate(() => { const s = window.__replayStore.getState(); s.setPlaying(false); s.setPlaybackWindow(null); s.setShowLapSelector(false); });
  report.races[race] = { surface, laps: {} };

  for (const record of index.selected) {
    const entry = file.laps.find((l) => l.lap === record.lap);
    const result = await page.evaluate(async ({ race, record, surface }) => {
      const scene = window.__replayScene, store = window.__replayStore;
      const { loadLapFile } = await import('/src/replay/dataLoader.ts');
      const { lapDuration } = await import('/src/replay/interpolation.ts');
      const lap = await loadLapFile(record.data_file);
      const rec = store.getState().lapsIndex.laps.find((l) => l.lap === record.lap && l.vehicle_id === record.vehicle_id);
      store.getState().setGpsRegistrationEnabled(true);
      store.getState().setActiveLap(rec, lap);
      store.getState().setDuration(lapDuration(lap));
      scene.setLap(store.getState().activeLap);
      const geometry = await (await fetch('/data/tracks/fuji/cg_study/geometry.json')).json();
      const step = geometry.stationStep, road = geometry.road;
      // Rendered world borders, with the source curb outer line substituted
      // only at stations where that source observation exists.
      const edges = {
        left: road.map((r) => [r[7], r[8]]),
        right: road.map((r) => [r[9], r[10]]),
      };
      const dryEdges = { left: edges.left.map((p) => p.slice()), right: edges.right.map((p) => p.slice()) };
      const outerWidths = { left: new Float64Array(road.length), right: new Float64Array(road.length) };
      for (const curb of geometry.curbs) for (const r of curb.rows) {
        const i = Math.round(r[0] / step);
        if (i < 0 || i >= road.length) continue;
        const base = edges[curb.side][i], outer = [r[3], r[4]];
        const normal = [road[i][3], road[i][4]];
        const centre = [road[i][1], road[i][2]];
        const projected = (p) => (p[0] - centre[0]) * normal[0] + (p[1] - centre[1]) * normal[1];
        const width = curb.side === 'left' ? projected(base) - projected(outer) : projected(outer) - projected(base);
        if (width > outerWidths[curb.side][i]) {
          outerWidths[curb.side][i] = width;
          dryEdges[curb.side][i] = outer;
        }
      }
      const useCurb = surface === 'dry' ? 1 : 0;
      let hint = 0;
      const nearest = (x, z) => { // station hint only, never the distance result
        let best = -1, bestD = Infinity;
        const scan = (a, b) => { for (let k = a; k <= b; k++) { const i = (k + road.length) % road.length, d = (road[i][1] - x) ** 2 + (road[i][2] - z) ** 2; if (d < bestD) { bestD = d; best = i; } } };
        scan(hint - 200, hint + 200); if (bestD > 900) scan(0, road.length - 1);
        hint = best; return best;
      };
      const signedGap = (points, x, z, insideSign, near) => {
        let best = Infinity, cross = 0, station = 0, bestIndex = -1;
        const scan = (lo, hi) => {
          for (let i = Math.max(0, lo); i <= Math.min(points.length - 2, hi); i++) {
            const [ax, az] = points[i], [bx, bz] = points[i + 1];
            const vx = bx - ax, vz = bz - az, len2 = vx * vx + vz * vz;
            const t = len2 ? Math.max(0, Math.min(1, ((x - ax) * vx + (z - az) * vz) / len2)) : 0;
            const dx = x - ax - t * vx, dz = z - az - t * vz, d2 = dx * dx + dz * dz;
            if (d2 < best) { best = d2; cross = vx * dz - vz * dx; station = road[i][0] + t * (road[i + 1][0] - road[i][0]); bestIndex = i; }
          }
        };
        scan(near - 40, near + 40);
        // A pit point or a boundary folded relative to the centre hint must
        // search globally. This changes only measurement speed, never meaning.
        if (best > 900 || bestIndex <= near - 39 || bestIndex >= near + 39) scan(0, points.length - 2);
        return { gap: Math.sqrt(best) * Math.sign(cross * insideSign), station };
      };
      const render = scene.doRender, camera = scene.updateCamera; scene.doRender = () => {}; scene.updateCamera = () => {};
      const THREE_V = scene.carRig.root.position.constructor;
      const measure = (t) => {
        scene.update(t, null, 'chase', 0); scene.update(t, null, 'chase', 0); // second call freezes the pose smoothing
        scene.carRig.root.updateMatrixWorld(true);
        const pos = scene.carRig.root.position, f = scene.carRig.forward, right = [-f.z, f.x];
        let worst = Infinity; const gaps = {};
        for (const [name, w] of Object.entries(scene.carRig.wheels)) {
          const centre = w.pivot.getWorldPosition(new THREE_V());
          const side = (centre.x - pos.x) * right[0] + (centre.z - pos.z) * right[1] > 0 ? 'right' : 'left';
          const sign = side === 'right' ? 1 : -1;
          const p = [-w.tyreWidthMeters / 2, w.tyreWidthMeters / 2].map((x) => w.pivot.localToWorld(new THREE_V(x, -0.33, 0)))
            .reduce((a, b) => (sign * (a.x * right[0] + a.z * right[1]) > sign * (b.x * right[0] + b.z * right[1]) ? a : b));
          const i = nearest(p.x, p.z);
          const roadLeft = signedGap(edges.left, p.x, p.z, +1, i);
          const roadRight = signedGap(edges.right, p.x, p.z, -1, i);
          const allowedLeft = useCurb ? signedGap(dryEdges.left, p.x, p.z, +1, i) : roadLeft;
          const allowedRight = useCurb ? signedGap(dryEdges.right, p.x, p.z, -1, i) : roadRight;
          const roadGap = side === 'left' ? roadLeft.gap : roadRight.gap;
          const boundary = side === 'left' ? allowedLeft : allowedRight;
          const limitGap = Math.min(allowedLeft.gap, allowedRight.gap);
          gaps[name] = { side, roadGap, limitGap, station: boundary.station, point: [p.x, p.z] };
          worst = Math.min(worst, limitGap);
        }
        const ci = nearest(pos.x, pos.z);
        const centreBeyond = Math.max(0, -Math.min(
          signedGap(dryEdges.left, pos.x, pos.z, +1, ci).gap,
          signedGap(dryEdges.right, pos.x, pos.z, -1, ci).gap));
        return { x: pos.x, z: pos.z, worst, centreBeyond, gaps, telemetry: JSON.stringify(scene.lastSample.telemetry) };
      };
      const dimensions = () => {
        const rig = scene.carRig, w = rig.wheels;
        rig.root.updateMatrixWorld(true);
        const world = (wheel) => wheel.pivot.getWorldPosition(new THREE_V());
        const front = world(w.frontLeft).add(world(w.frontRight)).multiplyScalar(0.5);
        const rear = world(w.rearLeft).add(world(w.rearRight)).multiplyScalar(0.5);
        const tyre = (wheel) => wheel.wheel.localToWorld(new THREE_V(-0.125, 0, 0))
          .distanceTo(wheel.wheel.localToWorld(new THREE_V(0.125, 0, 0)));
        return { bodyWidthMeters: rig.root.scale.x * rig.body.scale.x * scene.normalizedBodyWidth,
          frontTrackMeters: world(w.frontLeft).distanceTo(world(w.frontRight)),
          rearTrackMeters: world(w.rearLeft).distanceTo(world(w.rearRight)),
          wheelbaseMeters: front.distanceTo(rear),
          frontTyreWidthMeters: tyre(w.frontLeft), rearTyreWidthMeters: tyre(w.rearLeft) };
      };
      const duration = store.getState().duration, times = [];
      for (let i = 0; i / 10 <= duration; i++) times.push(i / 10);
      const original = JSON.stringify([lap.lat, lap.lng]);
      const run = () => times.map((t) => ({ t, ...measure(t) }));
      const registered = run();
      const registeredDimensions = dimensions();
      let smoothBoundaryCheck = null;
      if (race === 'fuji_aim_2020_07_30' && record.lap === 1) {
        const g = registered[330].gaps.frontRight;
        const source = useCurb ? dryEdges[g.side] : edges[g.side];
        const sign = g.side === 'left' ? +1 : -1;
        const vals = Array.from({length: 41}, (_, k) => {
          const delta = (k - 20) * 0.00025;
          const i = nearest(g.point[0], g.point[1] + delta);
          return signedGap(source, g.point[0], g.point[1] + delta, sign, i).gap;
        });
        smoothBoundaryCheck = Math.max(...vals.slice(1).map((v, i) => Math.abs(v - vals[i])));
      }
      store.getState().setGpsRegistrationEnabled(false); // the HUD toggle: live, no reload
      scene.setLap(store.getState().activeLap);
      const raw = run();
      const rawDimensions = dimensions();
      store.getState().setGpsRegistrationEnabled(true);
      scene.setLap(store.getState().activeLap);
      const again = measure(55);
      scene.doRender = render; scene.updateCamera = camera;
      const lapNow = store.getState().activeLap;
      return {
        registered, raw, againX: again.x, registeredDimensions, rawDimensions, smoothBoundaryCheck,
        rawArraysUnchanged: JSON.stringify([lapNow.lat, lapNow.lng]) === original,
        stamped: lapNow.registration,
      };
    }, { race, record, surface });

    const [dx, dz] = entry.offsetMeters;
    assert.deepEqual(result.stamped.offsetMeters, entry.offsetMeters, 'viewer uses the shipped offset');
    assert(result.rawArraysUnchanged, 'recorded lat/lng untouched');
    if (result.smoothBoundaryCheck !== null) assert(result.smoothBoundaryCheck < .005,
      `rendered boundary distance must not jump at dry L1 33 s: ${result.smoothBoundaryCheck}`);
    assert.equal(result.stamped.verified, true, 'all registration input hashes verified');
    const profile = file.method.vehicleGeometry;
    assert(profile, 'registration carries independent vehicle dimensions');
    for (const mode of ['registeredDimensions', 'rawDimensions']) for (const [key, value] of Object.entries(result[mode])) {
      assert(Math.abs(value - profile[key]) < 1e-9, `${mode} ${key}: rendered ${value}, profile ${profile[key]}`);
    }
    // Pit lane / excursions are not track-limit evidence: same exclusion rule as the fit (>4 m beyond, +-8 s).
    const far = result.raw.filter((r) => r.centreBeyond > 4).map((r) => r.t);
    const keep = (t) => far.every((u) => Math.abs(u - t) > 8);
    const summarize = (rows) => {
      const used = rows.filter((r) => keep(r.t)), beyond = used.filter((r) => r.worst < -0.05);
      return { seconds: +(beyond.length / 10).toFixed(1), maxMeters: +Math.max(0, ...used.map((r) => -r.worst)).toFixed(3), samples: used.length };
    };
    let maxDeviation = 0;
    result.registered.forEach((r, i) => {
      const a = result.raw[i];
      maxDeviation = Math.max(maxDeviation, Math.abs(r.x - a.x - dx), Math.abs(r.z - a.z - dz));
      assert.equal(r.telemetry, a.telemetry, 'telemetry identical');
    });
    assert(maxDeviation < 1e-6, `rigid translation over the whole lap (max deviation ${maxDeviation})`);
    const lapReport = { offsetMeters: entry.offsetMeters, raw: summarize(result.raw), registered: summarize(result.registered),
      fitInputSummary: { raw: entry.raw, registered: entry.registered },
      maxDeviation, smoothBoundaryCheck: result.smoothBoundaryCheck,
      dimensions: { registered: result.registeredDimensions, raw: result.rawDimensions } };
    report.races[race].laps[record.lap] = lapReport;
    await writeFile(`${out}/runtime-verification.json`, JSON.stringify(report, null, 2));
    assert(lapReport.registered.seconds < lapReport.raw.seconds / 2, 'time beyond the limits at least halves');
    console.log(race, 'lap', record.lap, JSON.stringify(lapReport));

    if (shots[record.lap]) {
      const key = {};
      for (const t of shots[record.lap]) {
        const i = Math.round(t * 10);
        key[t] = { raw: result.raw[i].gaps, registered: result.registered[i].gaps };
      }
      report.races[race].laps[record.lap].keyTimes = key;
      const hiddenHud = await page.addStyleTag({ content: 'body *{visibility:hidden!important}canvas:first-child{visibility:visible!important}' });
      for (const mode of ['raw', 'registered']) for (const t of shots[record.lap]) for (const view of ['chase', 'top']) {
        await page.evaluate(({ mode, t, view }) => {
          const st = window.__replayStore.getState();
          st.setGpsRegistrationEnabled(mode === 'registered'); st.seek(t); st.setCameraMode(view);
        }, { mode, t, view });
        await page.waitForFunction(({ t, view }) => Math.abs(window.__replayScene.lastSample.time - t) < 1e-6 &&
          window.__replayScene.activeCameraMode === view, { t, view });
        await page.evaluate(({ t, view }) => { const s = window.__replayScene; for (let i = 0; i < 45; i++) s.update(t, null, view, 1 / 60); s.renderer.getContext().finish(); }, { t, view });
        await page.locator('canvas').first().screenshot({ path: `${out}/${race}-L${record.lap}-${t}-${mode}-${view}.png` });
      }
      await hiddenHud.evaluate((el) => el.remove());
    }
  }
}

// The user-facing default look and the HUD control itself.
await page.goto(`${base}?track=fuji&race=fuji_aim_01`);
await page.waitForFunction(() => window.__replayScene?.gltfModel && window.__replayStore.getState().activeLap, null, { timeout: 120000 });
await page.evaluate(() => { const s = window.__replayStore.getState(); s.setShowLapSelector(false); s.setPlaying(false); s.seek(55); s.setCameraMode('chase'); });
const button = page.locator('.hud-gps-btn');
await button.waitFor({ timeout: 30000 });
report.hud = { initial: await button.innerText(), offset: await page.locator('.hud-gps-offset').innerText() };
assert.equal(report.hud.initial, '位置合わせ ON');
const before = await page.evaluate(() => { const s = window.__replayScene; s.update(55, null, 'chase', 0); return [s.carRig.root.position.x, s.carRig.root.position.z]; });
await button.click();
report.hud.afterClick = await button.innerText();
assert.equal(report.hud.afterClick, '元GPS');
await page.waitForTimeout(400);
const after = await page.evaluate(() => { const s = window.__replayScene; s.update(55, null, 'chase', 0); return [s.carRig.root.position.x, s.carRig.root.position.z]; });
report.hud.clickMovedCarBy = [+(before[0] - after[0]).toFixed(3), +(before[1] - after[1]).toFixed(3)];
await page.screenshot({ path: `${out}/hud-default-look-raw.png` });
await page.keyboard.press('g');
assert.equal(await button.innerText(), '位置合わせ ON');
await page.waitForTimeout(600);
await page.screenshot({ path: `${out}/hud-default-look-registered.png` });

// The dated studies stay defined against raw GPS.
await page.goto(`${base}?track=fuji&race=fuji_aim_01&look=cg&alignment=local-raw`);
await page.waitForFunction(() => window.__replayStore?.getState().activeLap, null, { timeout: 120000 });
report.legacyStudyHasRegistration = await page.evaluate(() => !!window.__replayStore.getState().activeLap.registration);
assert.equal(report.legacyStudyHasRegistration, false);
// ?gps=raw starts switched off but stays toggleable.
await page.goto(`${base}?track=fuji&race=fuji_aim_01&gps=raw`);
await page.waitForFunction(() => window.__replayStore?.getState().activeLap, null, { timeout: 120000 });
report.gpsRawParam = await page.evaluate(() => window.__replayStore.getState().activeLap.registration);
assert.equal(report.gpsRawParam.enabled, false);

assert.equal(report.gpsRawParam.verified, true);
report.staleInputs = {};
for (const [name, path] of [['limits', '/data/tracks/fuji/cg_study/geometry.json'],
  ['referenceTrack', '/data/tracks/fuji/track.json']]) {
  const pattern = `**${path}`;
  await page.route(pattern, async (route) => {
    const response = await route.fetch();
    const body = JSON.parse(await response.text());
    // Real content change: a traced boundary or the projection origin.
    if (name === 'limits') body.road[0][7] += 0.1;
    else body.origin.lng += 0.000001;
    await route.fulfill({ response, body: JSON.stringify(body), contentType: 'application/json' });
  });
  await page.goto(`${base}?track=fuji&race=fuji_aim_01`);
  await page.waitForFunction(() => window.__replayStore?.getState().activeLap, null, { timeout: 120000 });
  const stale = await page.evaluate(() => {
    const store = window.__replayStore;
    const before = store.getState().activeLap.registration;
    store.getState().setGpsRegistrationEnabled(true);
    return { before, after: store.getState().activeLap.registration };
  });
  assert.equal(stale.before.inputVerification[name], false, `${name} change detected`);
  assert.equal(stale.before.verified, false);
  assert.equal(stale.before.enabled, false);
  assert.equal(stale.after.enabled, false, 'stale correction cannot be enabled by the store');
  assert.equal(await page.locator('.hud-gps-btn').isDisabled(), true);
  assert.equal(await page.locator('.hud-gps-btn').innerText(), '補正値が古い');
  report.staleInputs[name] = stale;
  await page.unroute(pattern);
}
assert.equal(errors.length, 0, 'no page errors: ' + errors.join(' | '));
report.completed = true;
console.log('HUD', JSON.stringify(report.hud), '\nSaved', `${out}/runtime-verification.json`);
} catch (error) {
  report.failure = String(error?.stack || error);
  throw error;
} finally {
  await writeFile(`${out}/runtime-verification.json`, JSON.stringify(report, null, 2));
  await browser.close();
}
