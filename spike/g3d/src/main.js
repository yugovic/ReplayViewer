// G3D Spike - Google Photorealistic 3D Tiles evaluation for HackTheTrack.
// Standalone: does not import anything from the main app's src/.
//
// What it does:
//   - Loads Google Photorealistic 3D Tiles centred on Barber or Fuji.
//   - Overlays the main app's track centerline (lat/lng/alt from track.json)
//     converted to the SAME ECEF/ENU frame the tiles use, so XY/height
//     mismatch can be judged by eye and measured.
//   - Shows the required copyright attribution and supports S=screenshot.

import {
  WebGLRenderer,
  PerspectiveCamera,
  Scene,
  Vector3,
  Matrix4,
  MathUtils,
  BufferGeometry,
  Float32BufferAttribute,
  Line,
  LineBasicMaterial,
  Group,
  Sphere,
  Box3,
  Mesh,
  BoxGeometry,
  CircleGeometry,
  MeshStandardMaterial,
  MeshBasicMaterial,
  DirectionalLight,
  AmbientLight,
  Quaternion,
  Euler,
} from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import {
  TilesRenderer,
  WGS84_ELLIPSOID,
  OBJECT_FRAME,
} from '3d-tiles-renderer';
import { GoogleCloudAuthPlugin } from '3d-tiles-renderer/core/plugins';
import { TilesFadePlugin, ReorientationPlugin } from '3d-tiles-renderer/three/plugins';

// ---------------------------------------------------------------------------
// Track presets (lat/lng only trusted from track.json; these are fallbacks /
// camera hints matching the spec).
// ---------------------------------------------------------------------------
const TRACKS = {
  barber: { name: 'Barber Motorsports Park', lat: 33.53252, lng: -86.61931 },
  fuji: { name: 'Fuji Speedway', lat: 35.37170, lng: 138.92560 },
};

// Track-specific geoid separation (ellipsoid − orthometric) measured in P4.
// track.json alt is orthometric (above MSL); Google tiles use ellipsoidal
// height, so we add this to place the car/line on the tile surface.
const GEOID = { barber: -30.0, fuji: 45.0 };

// Default race per track when ?race= is present but omitted (or bare ?race).
const DEFAULT_RACE = { barber: 'barber_r1', fuji: 'fuji_aim_01' };

const params = new URLSearchParams(location.search);
const trackId = (params.get('track') || 'barber').toLowerCase();
const preset = TRACKS[trackId] || TRACKS.barber;

// Spectator mode is enabled by the presence of ?race (any value, incl. empty).
const hasRaceParam = params.has('race');
const raceId = hasRaceParam
  ? (params.get('race') || DEFAULT_RACE[trackId] || '')
  : '';
const lapFileParam = params.get('lap') || '';

// ---------------------------------------------------------------------------
// API key: ?key= -> localStorage -> input UI. Never committed to code.
// ---------------------------------------------------------------------------
const KEY_STORE = 'g3d_api_key';

function resolveApiKey() {
  const fromUrl = params.get('key');
  if (fromUrl) {
    localStorage.setItem(KEY_STORE, fromUrl);
    // strip the key from the address bar so it is not left visible / shared
    params.delete('key');
    const clean = location.pathname + (params.toString() ? '?' + params.toString() : '');
    history.replaceState(null, '', clean);
    return fromUrl;
  }
  return localStorage.getItem(KEY_STORE) || '';
}

const keygate = document.getElementById('keygate');
const keyinput = document.getElementById('keyinput');
const keyerr = document.getElementById('keyerr');

document.getElementById('keysave').addEventListener('click', () => {
  const v = keyinput.value.trim();
  if (!v) { keyerr.textContent = 'キーを入力してください'; return; }
  localStorage.setItem(KEY_STORE, v);
  location.reload();
});
keyinput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') document.getElementById('keysave').click();
});

const apiKey = resolveApiKey();
if (apiKey) {
  boot(apiKey);
} else {
  // Fallback: spike/g3d/.g3d_key served by the dev middleware, so the key
  // lives only in an untracked local file (never in chat/code/URL). Not
  // written to localStorage: the file stays the single source of truth.
  fetch('/g3d-key')
    .then((r) => (r.ok ? r.text() : ''))
    .catch(() => '')
    .then((text) => {
      const key = (text || '').trim();
      if (key) boot(key);
      else keygate.classList.add('show'); // UI waits for manual key entry
    });
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------
function boot(key) {
  const statsEl = document.getElementById('stats');
  const attribEl = document.getElementById('attribution');

  const renderer = new WebGLRenderer({
    antialias: true,
    logarithmicDepthBuffer: true, // avoid z-fighting between overlay line and mesh
    preserveDrawingBuffer: true,  // needed for reliable screenshot via toDataURL
  });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(window.innerWidth, window.innerHeight);
  document.getElementById('app').appendChild(renderer.domElement);

  const scene = new Scene();
  const camera = new PerspectiveCamera(60, window.innerWidth / window.innerHeight, 1, 1e8);
  camera.position.set(0, 800, 1200);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.maxDistance = 5e6;

  // -- Reference frame (identical values feed BOTH the tileset reorientation
  //    and our own ENU frame, so the overlay lands in the exact tile frame). --
  const refLatRad = MathUtils.degToRad(preset.lat);
  const refLonRad = MathUtils.degToRad(preset.lng);
  const refHeight = 0; // ellipsoid metres

  // -- Tiles --
  const tiles = new TilesRenderer('https://tile.googleapis.com/v1/3dtiles/root.json');
  const authPlugin = new GoogleCloudAuthPlugin({ apiToken: key, autoRefreshToken: true });
  tiles.registerPlugin(authPlugin);
  tiles.registerPlugin(new TilesFadePlugin());
  tiles.registerPlugin(new ReorientationPlugin({
    lat: refLatRad,
    lon: refLonRad,
    height: refHeight,
    recenter: true,
  }));
  tiles.setCamera(camera);
  tiles.setResolutionFromRenderer(camera, renderer);
  tiles.errorTarget = 8; // lower = more detail loaded
  scene.add(tiles.group);

  // surface HTTP failures (e.g. 401/403 from a bad/disabled key) in the HUD
  let lastError = '';
  tiles.addEventListener('load-error', (e) => {
    lastError = `tile load failed: ${e.error?.message || e.error || 'unknown'}`;
  });

  // -- Precompute the inverse ENU frame (ECEF -> local), same as Reorientation.
  const enuFrame = new Matrix4();
  WGS84_ELLIPSOID.getObjectFrame(
    refLatRad, refLonRad, refHeight, 0, 0, 0, enuFrame, OBJECT_FRAME,
  );
  const ecefToLocal = enuFrame.clone().invert();

  // -- Overlay line (built after track.json loads) --
  const overlayGroup = new Group();
  scene.add(overlayGroup);
  let centerline = null;          // array of {lat,lng,alt,dist}
  // Default vertical offset: in spectator mode start at the measured geoid
  // constant (so the car sits on the tiles out of the box); ↑/↓ trims it.
  let heightOffset = raceId ? (GEOID[trackId] ?? 0) : 0;
  let lineObj = null;

  function ecefOf(lat, lng, altMeters, out) {
    return WGS84_ELLIPSOID.getCartographicToPosition(
      MathUtils.degToRad(lat), MathUtils.degToRad(lng), altMeters, out,
    );
  }

  function buildLine() {
    if (!centerline) return;
    if (lineObj) { overlayGroup.remove(lineObj); lineObj.geometry.dispose(); }

    const ecef = new Vector3();
    const local = new Vector3();
    const positions = [];
    for (const p of centerline) {
      ecefOf(p.lat, p.lng, p.alt + heightOffset, ecef);
      // float64 math here; only the small LOCAL result is stored as float32
      local.copy(ecef).applyMatrix4(ecefToLocal);
      positions.push(local.x, local.y, local.z);
    }
    // close the loop
    if (centerline.length > 1) {
      const p = centerline[0];
      ecefOf(p.lat, p.lng, p.alt + heightOffset, ecef);
      local.copy(ecef).applyMatrix4(ecefToLocal);
      positions.push(local.x, local.y, local.z);
    }

    const geom = new BufferGeometry();
    geom.setAttribute('position', new Float32BufferAttribute(positions, 3));
    const mat = new LineBasicMaterial({ color: 0xff2d78, depthTest: false });
    lineObj = new Line(geom, mat);
    lineObj.renderOrder = 999;
    lineObj.frustumCulled = false;
    overlayGroup.add(lineObj);
  }

  // Frame the camera on the line once, on first build.
  let framed = false;
  function frameCamera() {
    if (framed || !lineObj) return;
    lineObj.geometry.computeBoundingSphere();
    const s = lineObj.geometry.boundingSphere || new Sphere(new Vector3(), 500);
    const c = s.center.clone();
    const r = Math.max(s.radius, 200);
    controls.target.copy(c);
    camera.position.copy(c).add(new Vector3(0.4 * r, 1.1 * r, 0.9 * r));
    camera.near = Math.max(1, r / 500);
    camera.far = r * 2000;
    camera.updateProjectionMatrix();
    controls.update();
    framed = true;
  }

  // -- Load the main app's track.json (served by vite middleware) --
  fetch(`/track/${trackId}.json`)
    .then((r) => {
      if (!r.ok) throw new Error(`track fetch ${r.status}`);
      return r.json();
    })
    .then((json) => {
      const cl = json.centerline || json.points || [];
      centerline = cl
        .filter((p) => typeof p.lat === 'number' && typeof p.lng === 'number')
        .map((p) => ({
          lat: p.lat, lng: p.lng,
          alt: typeof p.alt === 'number' ? p.alt : 0,
          dist: typeof p.dist === 'number' ? p.dist : null,
        }));
      buildLine();
      if (raceId) initRace();
    })
    .catch((err) => { lastError = `track.json: ${err.message}`; });

  // =========================================================================
  // Spectator mode (?race=): drive one real replay car over the Google mesh.
  // =========================================================================
  let race = null;                 // populated by initRace() once data loads
  let cameraMode = 'chase';        // chase | tv | orbit
  const CAMERA_MODES = ['chase', 'tv', 'orbit'];

  const transportEl = document.getElementById('transport');
  const playBtn = document.getElementById('playbtn');
  const seekEl = document.getElementById('seek');
  const clockEl = document.getElementById('clock');

  // Applying a vertical trim must move BOTH the overlay line and the car so
  // they stay locked together (shared offset per spec).
  function applyOffset() {
    buildLine();
    if (race) race.rebuild();
  }

  // Interpolate track.json centerline altitude at a given along-track distance.
  function makeAltInterpolator() {
    const ds = [], as = [];
    for (const p of centerline) {
      if (p.dist != null) { ds.push(p.dist); as.push(p.alt); }
    }
    const usable = ds.length >= 2;
    const maxD = usable ? ds[ds.length - 1] : 0;
    return (dist) => {
      if (!usable) return centerline.length ? centerline[0].alt : 0;
      let d = dist;
      if (maxD > 0) { d = ((d % maxD) + maxD) % maxD; } // wrap for looped laps
      // linear scan (arrays are ~500 pts, called per-sample at build time only)
      if (d <= ds[0]) return as[0];
      for (let i = 1; i < ds.length; i++) {
        if (d <= ds[i]) {
          const f = (d - ds[i - 1]) / (ds[i] - ds[i - 1] || 1);
          return as[i - 1] + (as[i] - as[i - 1]) * f;
        }
      }
      return as[as.length - 1];
    };
  }

  function initRace() {
    fetch(`/race/${raceId}/laps.json`)
      .then((r) => { if (!r.ok) throw new Error(`laps.json ${r.status}`); return r.json(); })
      .then((laps) => {
        const file = pickLapFile(laps);
        if (!file) throw new Error('no lap file selected');
        return fetch(`/race/${raceId}/${file}`)
          .then((r) => { if (!r.ok) throw new Error(`lap ${r.status}`); return r.json(); });
      })
      .then((lap) => buildRace(lap))
      .catch((err) => { lastError = `race: ${err.message}`; });
  }

  // Pick the fastest available lap (deterministic; no Math.random).
  function pickLapFile(laps) {
    if (lapFileParam) return lapFileParam;
    const selected = laps.selected || [];
    if (!selected.length) return null;
    // cross-reference lap_time_seconds from laps.laps[] when available
    const timeOf = (s) => {
      const hit = (laps.laps || []).find(
        (l) => l.vehicle_id === s.vehicle_id && l.lap === s.lap,
      );
      return hit && typeof hit.lap_time_seconds === 'number'
        ? hit.lap_time_seconds : Infinity;
    };
    let best = selected[0], bestT = timeOf(best);
    for (const s of selected) {
      const t = timeOf(s);
      if (t < bestT) { best = s; bestT = t; }
    }
    return best.data_file;
  }

  function buildRace(lap) {
    const t = lap.t || [];
    const lat = lap.lat || [];
    const lng = lap.lng || [];
    const dist = lap.dist || [];
    const speed = lap.speed || [];
    const n = Math.min(t.length, lat.length, lng.length);
    if (n < 2) { lastError = 'race: lap too short'; return; }

    const altAt = makeAltInterpolator();
    const positions = new Array(n);   // Vector3 local positions
    const forwards = new Array(n);    // Vector3 unit forward (XZ plane)

    function rebuild() {
      const ecef = new Vector3();
      for (let i = 0; i < n; i++) {
        const a = altAt(dist[i] ?? 0) + heightOffset;
        ecefOf(lat[i], lng[i], a, ecef);
        const p = (positions[i] || new Vector3());
        p.copy(ecef).applyMatrix4(ecefToLocal);
        positions[i] = p;
      }
      // headings from forward differences (last uses previous segment)
      for (let i = 0; i < n; i++) {
        const a = positions[i];
        const b = positions[Math.min(i + 1, n - 1)];
        const f = (forwards[i] || new Vector3());
        f.set(b.x - a.x, 0, b.z - a.z);
        if (f.lengthSq() < 1e-9 && i > 0) f.copy(forwards[i - 1]);
        else f.normalize();
        forwards[i] = f;
      }
    }
    rebuild();

    // -- Vehicle: two boxes + ground marker + own lighting (tiles stay unlit) --
    const carGroup = new Group();
    const bodyMat = new MeshStandardMaterial({ color: 0xff3b30, metalness: 0.3, roughness: 0.5 });
    const cabinMat = new MeshStandardMaterial({ color: 0x1a2733, metalness: 0.2, roughness: 0.6 });
    // model space: +Z = forward (length 4.6), X = width 1.8, Y = height
    const body = new Mesh(new BoxGeometry(1.8, 0.8, 4.6), bodyMat);
    body.position.y = 0.55;
    const cabin = new Mesh(new BoxGeometry(1.55, 0.55, 2.4), cabinMat);
    cabin.position.set(0, 1.15, -0.2);
    carGroup.add(body, cabin);
    // ground contact shadow marker (dark ellipse just above the surface)
    const marker = new Mesh(
      new CircleGeometry(1.6, 24),
      new MeshBasicMaterial({ color: 0x000000, transparent: true, opacity: 0.35, depthWrite: false }),
    );
    marker.rotation.x = -Math.PI / 2;
    marker.position.y = 0.03;
    marker.scale.set(1.0, 1.7, 1.0);
    marker.renderOrder = 1;
    carGroup.add(marker);
    scene.add(carGroup);

    // Vehicle-only lighting: unlit Google tiles ignore these (no double shadow).
    const carSun = new DirectionalLight(0xffffff, 2.2);
    carSun.position.set(0.5, 1.0, 0.3);
    const carAmb = new AmbientLight(0xffffff, 0.9);
    scene.add(carSun, carAmb);

    // -- Playback state --
    let time = t[0];
    let playing = true;
    let rate = 1;
    const RATES = [0.5, 1, 2, 4];
    const tEnd = t[n - 1];
    const lapTime = (lap.meta && lap.meta.lap_time) || '';
    const vehId = (lap.meta && lap.meta.vehicle_id) || raceId;

    // find sample index for a time (linear from a hint; arrays ~1.5-2.2k)
    function sampleAt(time) {
      let i = 0;
      while (i < n - 1 && t[i + 1] <= time) i++;
      const t0 = t[i], t1 = t[Math.min(i + 1, n - 1)];
      const f = t1 > t0 ? (time - t0) / (t1 - t0) : 0;
      return { i, j: Math.min(i + 1, n - 1), f };
    }

    const _pos = new Vector3();
    const _fwd = new Vector3();
    const _up = new Vector3(0, 1, 0);
    const _right = new Vector3();
    const _m = new Matrix4();
    const _q = new Quaternion();
    let curSpeed = 0;

    function poseAt(time) {
      const { i, j, f } = sampleAt(time);
      _pos.copy(positions[i]).lerp(positions[j], f);
      _fwd.copy(forwards[i]).lerp(forwards[j], f);
      if (_fwd.lengthSq() < 1e-9) _fwd.copy(forwards[i]);
      _fwd.normalize();
      curSpeed = (speed[i] ?? 0) + ((speed[j] ?? 0) - (speed[i] ?? 0)) * f;
      return { pos: _pos, fwd: _fwd };
    }

    function orientCar() {
      // basis: right = up × fwd, up' = fwd × right, forward = fwd (+Z)
      _right.crossVectors(_up, _fwd).normalize();
      const up2 = new Vector3().crossVectors(_fwd, _right).normalize();
      _m.makeBasis(_right, up2, _fwd);
      _q.setFromRotationMatrix(_m);
      carGroup.quaternion.copy(_q);
      carGroup.position.copy(_pos);
    }

    // -- Cameras --
    const chaseCam = new Vector3();
    let chaseInit = false;
    function updateChase() {
      const back = new Vector3().copy(_fwd).multiplyScalar(-9);
      const desired = new Vector3().copy(_pos).add(back);
      desired.y += 4.2;
      if (!chaseInit) { chaseCam.copy(desired); chaseInit = true; }
      else chaseCam.lerp(desired, 0.12);
      camera.position.copy(chaseCam);
      camera.up.set(0, 1, 0);
      camera.lookAt(_pos.x, _pos.y + 1.0, _pos.z);
      camera.near = 0.5; camera.far = 5e6;
      camera.updateProjectionMatrix();
    }

    // TV: a few fixed aerial points chosen by along-track fraction.
    let tvPoints = null;
    function buildTvPoints() {
      const box = new Box3();
      for (const p of positions) box.expandByPoint(p);
      const c = box.getCenter(new Vector3());
      const sz = box.getSize(new Vector3());
      const R = Math.max(sz.x, sz.z) * 0.55 + 40;
      const H = Math.max(sz.x, sz.z) * 0.35 + 60;
      const mk = (ang) => new Vector3(
        c.x + R * Math.cos(ang), c.y + H, c.z + R * Math.sin(ang),
      );
      tvPoints = [mk(0), mk(Math.PI / 2), mk(Math.PI), mk((3 * Math.PI) / 2)];
    }
    function updateTv(time) {
      if (!tvPoints) buildTvPoints();
      const frac = tEnd > t[0] ? (time - t[0]) / (tEnd - t[0]) : 0;
      const idx = Math.min(tvPoints.length - 1, Math.floor(frac * tvPoints.length));
      camera.position.copy(tvPoints[idx]);
      camera.up.set(0, 1, 0);
      camera.lookAt(_pos.x, _pos.y, _pos.z);
      camera.near = 1; camera.far = 5e6;
      camera.updateProjectionMatrix();
    }

    // -- UI wiring --
    transportEl.classList.add('show');
    function fmt(s) { return `${s.toFixed(1)}`; }
    function syncUI() {
      playBtn.textContent = playing ? '❚❚' : '▶';
      const frac = tEnd > t[0] ? (time - t[0]) / (tEnd - t[0]) : 0;
      seekEl.value = String(Math.round(frac * 1000));
      clockEl.textContent = `${fmt(time - t[0])} / ${fmt(tEnd - t[0])}s`;
    }
    playBtn.addEventListener('click', () => { playing = !playing; syncUI(); });
    seekEl.addEventListener('input', () => {
      const frac = Number(seekEl.value) / 1000;
      time = t[0] + frac * (tEnd - t[0]);
      chaseInit = false;
    });

    let last = performance.now();
    function update() {
      const now = performance.now();
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      if (playing) {
        time += dt * rate;
        if (time > tEnd) time = t[0] + (time - tEnd); // loop
        if (time < t[0]) time = t[0];
      }
      poseAt(time);
      orientCar();
      if (cameraMode === 'chase') { controls.enabled = false; updateChase(); }
      else if (cameraMode === 'tv') { controls.enabled = false; updateTv(time); }
      else { controls.enabled = true; } // orbit: user drives, car not followed
      syncUI();
    }

    race = {
      rebuild,
      update,
      setRate(r) { rate = r; },
      cycleRate() {
        const i = RATES.indexOf(rate);
        rate = RATES[(i + 1) % RATES.length];
        return rate;
      },
      togglePlay() { playing = !playing; syncUI(); },
      seek(frac) {
        time = t[0] + Math.max(0, Math.min(1, frac)) * (tEnd - t[0]);
        chaseInit = false;
      },
      get rate() { return rate; },
      get speedKmh() { return curSpeed; },
      get info() { return { vehId, lapTime }; },
      get pos() { return _pos.clone(); },
      onOrbitEnter() { controls.target.copy(_pos); controls.update(); },
    };

    // frame camera immediately so we don't sit at the default overview
    framed = true;
    poseAt(time);
    orientCar();
    updateChase();
  }

  // -- Attribution (ToS requirement): pull the copyright string from the plugin.
  function updateAttribution() {
    try {
      const out = [];
      authPlugin.getAttributions(out);
      const text = out
        .map((a) => (a && a.type !== 'image' ? a.value : ''))
        .filter(Boolean)
        .join(' · ');
      attribEl.textContent = text || 'Data © Google';
    } catch {
      attribEl.textContent = 'Data © Google';
    }
  }

  // -- HUD / FPS --
  let frames = 0, fpsT = performance.now(), fps = 0;
  function updateStats() {
    frames++;
    const now = performance.now();
    if (now - fpsT >= 500) {
      fps = Math.round((frames * 1000) / (now - fpsT));
      frames = 0; fpsT = now;
    }
    let raceLine = '';
    if (race) {
      const { vehId, lapTime } = race.info;
      raceLine =
        `car: <b>${vehId}</b>${lapTime ? ` &nbsp;lap ${lapTime}` : ''}<br>` +
        `camera: <b>${cameraMode}</b> &nbsp; speed: <b>${race.rate}x</b> &nbsp; ` +
        `${Math.round(race.speedKmh)} km/h<br>`;
    } else if (raceId) {
      raceLine = `loading race ${raceId}…<br>`;
    }
    statsEl.innerHTML =
      `<b>${preset.name}</b> (${trackId})<br>` +
      `FPS: ${fps}<br>` +
      raceLine +
      `tiles: ${tiles.group.children.length} groups / downloading ${tiles.downloadQueue?.items?.length ?? '?'} / parsing ${tiles.parseQueue?.items?.length ?? '?'}<br>` +
      `height offset: <b>${heightOffset >= 0 ? '+' : ''}${heightOffset.toFixed(1)} m</b>${(lineObj || race) ? '' : ' (loading track…)'}` +
      (lastError ? `<br><span style="color:#ff7b72">${lastError}</span>` : '');
  }

  // -- Render loop --
  function animate() {
    requestAnimationFrame(animate);
    if (race) race.update();
    if (controls.enabled) controls.update();
    camera.updateMatrixWorld();
    tiles.update();
    if (!race) frameCamera();
    updateAttribution();
    updateStats();
    renderer.render(scene, camera);
  }
  animate();

  // -- Resize --
  window.addEventListener('resize', () => {
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
    tiles.setResolutionFromRenderer(camera, renderer);
  });

  // -- Keyboard --
  window.addEventListener('keydown', (e) => {
    const step = e.shiftKey ? 10 : 1;
    switch (e.key) {
      case 's': case 'S':
        screenshot();
        break;
      case 'l': case 'L':
        overlayGroup.visible = !overlayGroup.visible;
        break;
      case 'k': case 'K':
        keyinput.value = localStorage.getItem(KEY_STORE) || '';
        keygate.classList.add('show');
        break;
      case 'ArrowUp':
        heightOffset += step; applyOffset(); e.preventDefault();
        break;
      case 'ArrowDown':
        heightOffset -= step; applyOffset(); e.preventDefault();
        break;
      case '0':
        heightOffset = raceId ? (GEOID[trackId] ?? 0) : 0; applyOffset();
        break;
      case ' ': // Space: play/pause
        if (race) { race.togglePlay(); e.preventDefault(); }
        break;
      case '[':
      case ']':
        if (race) race.cycleRate();
        break;
      case 'c': case 'C':
        if (race) {
          const i = CAMERA_MODES.indexOf(cameraMode);
          cameraMode = CAMERA_MODES[(i + 1) % CAMERA_MODES.length];
          if (cameraMode === 'orbit') race.onOrbitEnter();
        }
        break;
    }
  });

  // -- Debug hook for automated evaluation (Playwright). Exposes the internal
  //    closure state so the P4 spike can be driven deterministically. --
  window.__g3d = {
    camera, controls, tiles, scene, renderer,
    get offset() { return heightOffset; },
    setOffset(v) { heightOffset = v; applyOffset(); },
    // -- spectator hooks (null until race data loads) --
    get raceReady() { return !!race; },
    get cameraMode() { return cameraMode; },
    setCameraMode(m) {
      if (race && CAMERA_MODES.includes(m)) {
        cameraMode = m;
        if (m === 'orbit') race.onOrbitEnter();
      }
    },
    play() { if (race) race.togglePlay(); },
    setRate(r) { if (race) race.setRate(r); },
    // manual playback tick (for headless/hidden-tab driving where rAF is paused)
    tick() { if (race) race.update(); },
    seek(frac) { if (race) race.seek(frac); },
    get carPos() { return race ? race.pos : null; },
    get speedKmh() { return race ? race.speedKmh : 0; },
    get fps() { return fps; },
    get err() { return lastError; },
    get downloading() { return tiles.downloadQueue?.items?.length ?? -1; },
    get parsing() { return tiles.parseQueue?.items?.length ?? -1; },
    get groups() { return tiles.group.children.length; },
    get lineReady() { return !!lineObj; },
    get target() { return controls.target.clone(); },
    // Position the camera around the current controls.target using spherical
    // coords: azimuth (deg, 0=+Z looking north-ish), elevation (deg above
    // horizon, 90=straight down), distance (m).
    view(azDeg, elDeg, dist) {
      const c = controls.target;
      const az = MathUtils.degToRad(azDeg), el = MathUtils.degToRad(elDeg);
      const x = dist * Math.cos(el) * Math.sin(az);
      const z = dist * Math.cos(el) * Math.cos(az);
      const y = dist * Math.sin(el);
      camera.position.set(c.x + x, c.y + y, c.z + z);
      camera.near = Math.max(1, dist / 500);
      camera.far = dist * 4000;
      camera.updateProjectionMatrix();
      controls.update();
      framed = true; // stop auto-framing from overriding us
    },
    toggleLine(v) { overlayGroup.visible = v; },
  };

  function screenshot() {
    // render once synchronously so the buffer is fresh, then export
    renderer.render(scene, camera);
    const url = renderer.domElement.toDataURL('image/png');
    const a = document.createElement('a');
    const stamp = new Date().toISOString().replace(/[:.]/g, '-');
    a.href = url;
    a.download = `g3d_${trackId}_${stamp}.png`;
    a.click();
  }
}
