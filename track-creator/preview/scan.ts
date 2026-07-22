import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import {
  ScanDiagnostics,
  type DiagnosticPose,
  type RoadClearance,
} from "./scanDiagnostics.ts";
import {
  confidenceForFit,
  nearestValueInGapRange,
  normalizeGapRange,
  residualToGapRange,
  solveLeastSquares2D,
  solveTranslationFit,
  type DiagnosticSide,
  type LeastSquaresRow,
} from "./scanAutoFit.ts";
import {
  effectiveMeshGapRange,
  isCurbRelation,
  isObservationConfidence,
  observationWeight,
  presetRangeForRelation,
  type CurbRelation,
  type ObservationConfidence,
} from "./scanAnchorAudit.ts";
import { ScanSatelliteOverlay, type SatelliteMeta } from "./scanSatellite.ts";

const TRACK_ID = "fuji";
const RACE_ID = new URLSearchParams(location.search).get("race") || "fuji_aim_01";
const DATA_BASE = "/data";
const WORLD_URL = `${DATA_BASE}/tracks/${TRACK_ID}/scene.mod.glb`;
const ROAD_URL = `${DATA_BASE}/tracks/${TRACK_ID}/road_ac_local.mod.glb`;
const CAR_URL = "/assets/RX3_race.glb";
const CAR_LENGTH = 4.06;
const CAR_WIDTH = 1.695;
const CAR_WHEELBASE = 2.57;
const CAR_WHEEL_TRACK = 1.47;
const CAR_GROUND_CLEARANCE = 0.08;
const ESTIMATED_ALIGNMENT = { x: -3.5, z: -4.4 };
const RIGID_ALIGNMENT_ANCHOR = { x: 625.0024, z: -447.9128 };
const DIAGNOSTIC_UPDATE_INTERVAL_MS = 200;
const DIAGNOSTIC_PATH_STRIDE = 4;
const MAX_AUTO_FIT_TRANSLATION_M = 12;
const DIAGNOSTIC_ANCHOR_IDS = ["A", "B", "C"] as const;
type DiagnosticAnchorId = (typeof DIAGNOSTIC_ANCHOR_IDS)[number];
interface DiagnosticAnchorConfig {
  label: string;
  referenceTime: number;
  distanceM: number;
  side: DiagnosticSide;
  relation: CurbRelation;
  confidence: ObservationConfidence;
  targetGapMinM: number;
  targetGapMaxM: number;
  boundaryOffsetM: number;
  targetGapM?: number;
}
const DEFAULT_DIAGNOSTIC_ANCHORS: Record<DiagnosticAnchorId, DiagnosticAnchorConfig> = {
  A: { label: "Brake", referenceTime: 12.57, distanceM: 625.6587, side: "left", relation: "unreviewed", confidence: "low", targetGapMinM: 0.1, targetGapMaxM: 0.1, boundaryOffsetM: 0 },
  B: { label: "T1 clip", referenceTime: 16.6, distanceM: 737.802, side: "right", relation: "unreviewed", confidence: "low", targetGapMinM: 0.1, targetGapMaxM: 0.1, boundaryOffsetM: 0 },
  C: { label: "T2 clip", referenceTime: 25.97, distanceM: 1004.0511, side: "right", relation: "unreviewed", confidence: "low", targetGapMinM: 0.1, targetGapMaxM: 0.1, boundaryOffsetM: 0 },
};
const SATELLITE_SOURCES = {
  esri: { label: "Esri z19", imageUrl: `${DATA_BASE}/tracks/${TRACK_ID}/satellite_z19.jpg` },
  bing: { label: "Bing z19", imageUrl: `${DATA_BASE}/tracks/${TRACK_ID}/satellite_bing.jpg` },
} as const;
type SatelliteSourceId = keyof typeof SATELLITE_SOURCES;

interface TrackOrigin { lat: number; lng: number; alt: number }
interface LapMeta { lap: number; lap_time: string; lap_time_seconds: number; vehicle_id: string }
interface LapData {
  meta: LapMeta;
  t: number[]; lat: number[]; lng: number[]; speed: number[];
  aps: number[]; brake: number[]; steer: number[]; gear: number[];
  dist: number[];
}
interface LapRecord {
  vehicle_id: string; lap: number; data_file?: string;
  lap_time_seconds: number; lap_time: string;
}
interface LapsIndex { laps: LapRecord[] }

const loading = document.getElementById("loading")!;
const hud = document.getElementById("hud")!;
const panel = document.getElementById("panel")!;
const lapSelect = document.getElementById("lapSelect") as HTMLSelectElement;
const playBtn = document.getElementById("playBtn") as HTMLButtonElement;
const speedSelect = document.getElementById("speedSelect") as HTMLSelectElement;
const timeSlider = document.getElementById("timeSlider") as HTMLInputElement;
const timeLabel = document.getElementById("timeLabel")!;
const followChk = document.getElementById("followChk") as HTMLInputElement;
const offsetXInput = document.getElementById("offsetX") as HTMLInputElement;
const offsetZInput = document.getElementById("offsetZ") as HTMLInputElement;
const rigidFitChk = document.getElementById("rigidFitChk") as HTMLInputElement;
const rotationDegInput = document.getElementById("rotationDeg") as HTMLInputElement;
const topViewBtn = document.getElementById("topViewBtn") as HTMLButtonElement;
const applyEstimateBtn = document.getElementById("applyEstimateBtn") as HTMLButtonElement;
const resetOffsetBtn = document.getElementById("resetOffsetBtn") as HTMLButtonElement;
const diagnosticChk = document.getElementById("diagnosticChk") as HTMLInputElement;
const diagnosticsPanel = document.getElementById("diagnostics") as HTMLElement;
const anchorUi = Object.fromEntries(DIAGNOSTIC_ANCHOR_IDS.map((id) => [id, {
  card: document.getElementById("anchorCard" + id) as HTMLElement,
  time: document.getElementById("anchorTime" + id) as HTMLInputElement,
  relation: document.getElementById("anchorRelation" + id) as HTMLSelectElement,
  confidence: document.getElementById("anchorConfidence" + id) as HTMLSelectElement,
  gapMin: document.getElementById("anchorGapMin" + id) as HTMLInputElement,
  gapMax: document.getElementById("anchorGapMax" + id) as HTMLInputElement,
  boundaryOffset: document.getElementById("anchorBoundaryOffset" + id) as HTMLInputElement,
  jump: document.getElementById(`jumpAnchor${id}`) as HTMLButtonElement,
  side: document.getElementById(`anchorSide${id}`) as HTMLSelectElement,
  set: document.getElementById(`setAnchor${id}`) as HTMLButtonElement,
}])) as Record<DiagnosticAnchorId, {
  card: HTMLElement;
  time: HTMLInputElement;
  relation: HTMLSelectElement;
  confidence: HTMLSelectElement;
  gapMin: HTMLInputElement;
  gapMax: HTMLInputElement;
  boundaryOffset: HTMLInputElement;
  jump: HTMLButtonElement;
  side: HTMLSelectElement;
  set: HTMLButtonElement;
}>;
const autoFitBtn = document.getElementById("autoFitBtn") as HTMLButtonElement;
const applyAutoFitBtn = document.getElementById("applyAutoFitBtn") as HTMLButtonElement;
const undoAutoFitBtn = document.getElementById("undoAutoFitBtn") as HTMLButtonElement;
const resetAnchorsBtn = document.getElementById("resetAnchorsBtn") as HTMLButtonElement;
const autoFitResult = document.getElementById("autoFitResult")!;
const refreshDiagnosticsBtn = document.getElementById("refreshDiagnostics") as HTMLButtonElement;
const diagnosticMetrics = document.getElementById("diagnosticMetrics")!;
const diagnosticComparison = document.getElementById("diagnosticComparison")!;
const satelliteChk = document.getElementById("satelliteChk") as HTMLInputElement;
const satelliteSource = document.getElementById("satelliteSource") as HTMLSelectElement;
const satelliteOpacity = document.getElementById("satelliteOpacity") as HTMLInputElement;
const circuitViewBtn = document.getElementById("circuitViewBtn") as HTMLButtonElement;
const satelliteStatus = document.getElementById("satelliteStatus")!;
const lapInfo = document.getElementById("lapInfo")!;

const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(innerWidth, innerHeight);
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.05;
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
document.body.appendChild(renderer.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x9fc6df);
scene.fog = new THREE.Fog(0xb7cfdd, 900, 3300);
const camera = new THREE.PerspectiveCamera(55, innerWidth / innerHeight, 0.1, 8000);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.maxPolarAngle = Math.PI * 0.48;
controls.enabled = false;

scene.add(new THREE.HemisphereLight(0xdbeeff, 0x6b604d, 1.35));
const sun = new THREE.DirectionalLight(0xfff4dc, 2.4);
sun.position.set(-600, 1100, -500);
sun.castShadow = true;
sun.shadow.mapSize.set(2048, 2048);
sun.shadow.camera.left = -220;
sun.shadow.camera.right = 220;
sun.shadow.camera.top = 220;
sun.shadow.camera.bottom = -220;
scene.add(sun);

const loader = new GLTFLoader();
const roadMeshes: THREE.Mesh[] = [];
const raycaster = new THREE.Raycaster();
const rayOrigin = new THREE.Vector3();
const rayDown = new THREE.Vector3(0, -1, 0);
let rayTop = 1000;
let car = new THREE.Group();
let carVisual = new THREE.Group();
scene.add(car);

let origin: TrackOrigin;
let laps: LapData[] = [];
let activeLap: LapData;
let replayTime = 0;
let playing = true;
let playbackSpeed = 1;
let lastFrame = performance.now();
let lastGroundY = 0;
const ALIGNMENT_STORAGE_KEY = `scan-alignment-v2:${TRACK_ID}`;
const LEGACY_ALIGNMENT_STORAGE_KEY = `scan-alignment-v1:${TRACK_ID}`;
const DIAGNOSTIC_ANCHORS_STORAGE_KEY = `scan-diagnostic-anchors-v1:${TRACK_ID}`;
let alignmentX = 0;
let alignmentZ = 0;
let alignmentRotationDeg = 0;
let rigidFitEnabled = false;
let diagnosticAnchors = cloneDefaultDiagnosticAnchors();
let diagnostics: ScanDiagnostics | null = null;
let diagnosticsEnabled = false;
let diagnosticAnchorId: DiagnosticAnchorId | null = null;
let lastDiagnosticUpdate = 0;
let satelliteOverlay: ScanSatelliteOverlay | null = null;
let autoFitRestore: { x: number; z: number; rotationDeg: number; rigidFit: boolean } | null = null;
let pendingAutoFit: {
  evaluation: FitCandidateEvaluation;
  condition: number;
  coverage: number;
  baselineCoverage: number;
  sourceLap: number;
} | null = null;
const carForward = new THREE.Vector3(0, 0, 1);
const followTarget = new THREE.Vector3();
const desiredCamera = new THREE.Vector3();

const DEG = Math.PI / 180;
function latLngToLocal(lat: number, lng: number) {
  return {
    x: (lng - origin.lng) * 111320 * Math.cos(origin.lat * DEG),
    z: -(lat - origin.lat) * 111320,
  };
}

async function fetchJson<T>(url: string): Promise<T> {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url} (${response.status})`);
  return response.json() as Promise<T>;
}

function loadGltf(url: string, onProgress?: (ratio: number) => void): Promise<THREE.Group> {
  return new Promise((resolve, reject) => {
    loader.load(
      url,
      (gltf) => resolve(gltf.scene),
      (event) => onProgress?.(event.total > 0 ? event.loaded / event.total : 0),
      reject,
    );
  });
}

function prepareWorld(root: THREE.Group) {
  root.name = "fuji-ac-mod-world";
  root.traverse((object) => {
    if (!(object instanceof THREE.Mesh)) return;
    object.castShadow = false;
    object.receiveShadow = true;
    const materials = Array.isArray(object.material) ? object.material : [object.material];
    for (const material of materials) {
      if ("map" in material && material.map instanceof THREE.Texture) {
        material.map.colorSpace = THREE.SRGBColorSpace;
      }
      material.needsUpdate = true;
    }
  });
  scene.add(root);
  return new THREE.Box3().setFromObject(root);
}

function prepareRoad(root: THREE.Group) {
  root.visible = false;
  root.updateMatrixWorld(true);
  root.traverse((object) => {
    if (object instanceof THREE.Mesh) roadMeshes.push(object);
  });
  const bounds = new THREE.Box3().setFromObject(root);
  rayTop = bounds.max.y + 20;
  scene.add(root);
}

function normalizeCar(root: THREE.Group) {
  root.updateMatrixWorld(true);
  const initial = new THREE.Box3().setFromObject(root);
  const initialSize = initial.getSize(new THREE.Vector3());
  if (initialSize.x > initialSize.z * 1.2) root.rotation.y = -Math.PI / 2;
  root.updateMatrixWorld(true);
  const oriented = new THREE.Box3().setFromObject(root);
  const size = oriented.getSize(new THREE.Vector3());
  const dimensionScale = new THREE.Group();
  const lengthScale = CAR_LENGTH / Math.max(size.z, 0.001);
  dimensionScale.name = "demio-dimension-scale";
  dimensionScale.scale.set(CAR_WIDTH / Math.max(size.x, 0.001), lengthScale, lengthScale);
  dimensionScale.add(root);
  dimensionScale.updateMatrixWorld(true);
  const scaled = new THREE.Box3().setFromObject(dimensionScale);
  const center = scaled.getCenter(new THREE.Vector3());
  dimensionScale.position.x -= center.x;
  dimensionScale.position.z -= center.z;
  dimensionScale.position.y -= scaled.min.y;
  root.traverse((object) => {
    if (!(object instanceof THREE.Mesh)) return;
    object.castShadow = true;
    object.receiveShadow = true;
  });
  carVisual = dimensionScale;
  car.add(dimensionScale);
}

function groundHeight(x: number, z: number) {
  rayOrigin.set(x, rayTop, z);
  raycaster.set(rayOrigin, rayDown);
  const hit = raycaster.intersectObjects(roadMeshes, false)[0];
  if (hit) lastGroundY = hit.point.y;
  return lastGroundY;
}

function segmentAt(times: number[], time: number) {
  let low = 0;
  let high = times.length - 1;
  while (low + 1 < high) {
    const mid = (low + high) >> 1;
    if (times[mid] <= time) low = mid;
    else high = mid;
  }
  return Math.min(low, times.length - 2);
}

function interpolate(series: number[], index: number, alpha: number) {
  return THREE.MathUtils.lerp(series[index], series[index + 1], alpha);
}

function sampleLap(lap: LapData, time: number) {
  const index = segmentAt(lap.t, time);
  const span = lap.t[index + 1] - lap.t[index];
  const alpha = span > 0 ? (time - lap.t[index]) / span : 0;
  const position = latLngToLocal(
    interpolate(lap.lat, index, alpha),
    interpolate(lap.lng, index, alpha),
  );
  const poseStart = Math.max(0, index - 3);
  const poseEnd = Math.min(lap.t.length - 1, index + 4);
  const start = latLngToLocal(lap.lat[poseStart], lap.lng[poseStart]);
  const end = latLngToLocal(lap.lat[poseEnd], lap.lng[poseEnd]);
  return {
    ...position,
    heading: Math.atan2(end.x - start.x, end.z - start.z),
    speed: interpolate(lap.speed, index, alpha),
    brake: interpolate(lap.brake, index, alpha),
    gear: Math.round(interpolate(lap.gear, index, alpha)),
  };
}

function formatTime(seconds: number) {
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${(seconds % 60).toFixed(1).padStart(4, "0")}`;
}

function cloneDefaultDiagnosticAnchors(): Record<DiagnosticAnchorId, DiagnosticAnchorConfig> {
  return Object.fromEntries(DIAGNOSTIC_ANCHOR_IDS.map((id) => [id, {
    ...DEFAULT_DIAGNOSTIC_ANCHORS[id],
  }])) as Record<DiagnosticAnchorId, DiagnosticAnchorConfig>;
}

function loadDiagnosticAnchors() {
  diagnosticAnchors = cloneDefaultDiagnosticAnchors();
  try {
    const saved = JSON.parse(localStorage.getItem(DIAGNOSTIC_ANCHORS_STORAGE_KEY) ?? "null") as
      Partial<Record<DiagnosticAnchorId, Partial<DiagnosticAnchorConfig>>> | null;
    if (saved) {
      for (const id of DIAGNOSTIC_ANCHOR_IDS) {
        const candidate = saved[id];
        if (!candidate) continue;
        const defaults = DEFAULT_DIAGNOSTIC_ANCHORS[id];
        const legacyTarget = Number.isFinite(candidate.targetGapM) ? candidate.targetGapM! : defaults.targetGapMinM;
        diagnosticAnchors[id] = {
          label: defaults.label,
          referenceTime: Number.isFinite(candidate.referenceTime) ? candidate.referenceTime! : defaults.referenceTime,
          distanceM: Number.isFinite(candidate.distanceM) ? candidate.distanceM! : defaults.distanceM,
          side: candidate.side === "left" || candidate.side === "right" ? candidate.side : defaults.side,
          relation: isCurbRelation(candidate.relation) ? candidate.relation : defaults.relation,
          confidence: isObservationConfidence(candidate.confidence) ? candidate.confidence : defaults.confidence,
          targetGapMinM: Number.isFinite(candidate.targetGapMinM) ? candidate.targetGapMinM! : legacyTarget,
          targetGapMaxM: Number.isFinite(candidate.targetGapMaxM) ? candidate.targetGapMaxM! : legacyTarget,
          boundaryOffsetM: Number.isFinite(candidate.boundaryOffsetM) ? candidate.boundaryOffsetM! : defaults.boundaryOffsetM,
        };
      }
    }
  } catch {
    diagnosticAnchors = cloneDefaultDiagnosticAnchors();
  }
  syncDiagnosticAnchorInputs();
}

function saveDiagnosticAnchors() {
  localStorage.setItem(DIAGNOSTIC_ANCHORS_STORAGE_KEY, JSON.stringify(diagnosticAnchors));
}

function syncDiagnosticAnchorInputs() {
  for (const id of DIAGNOSTIC_ANCHOR_IDS) {
    const anchor = diagnosticAnchors[id];
    const ui = anchorUi[id];
    ui.jump.textContent = id + " " + anchor.label;
    ui.time.value = anchor.referenceTime.toFixed(2);
    ui.side.value = anchor.side;
    ui.relation.value = anchor.relation;
    ui.confidence.value = anchor.confidence;
    ui.gapMin.value = anchor.targetGapMinM.toFixed(2);
    ui.gapMax.value = anchor.targetGapMaxM.toFixed(2);
    ui.boundaryOffset.value = anchor.boundaryOffsetM.toFixed(2);
    ui.card.classList.toggle("unreviewed", anchor.relation === "unreviewed");
    ui.card.classList.toggle("reviewed", anchor.relation !== "unreviewed");
  }
}

function updateDiagnosticTargetFromUi(id: DiagnosticAnchorId, source: "relation" | "range" | "other" = "other") {
  const ui = anchorUi[id];
  const anchor = diagnosticAnchors[id];
  anchor.side = ui.side.value === "left" ? "left" : "right";
  anchor.relation = isCurbRelation(ui.relation.value) ? ui.relation.value : "unreviewed";
  anchor.confidence = isObservationConfidence(ui.confidence.value) ? ui.confidence.value : "low";
  if (source === "relation") {
    const preset = presetRangeForRelation(anchor.relation);
    if (preset) {
      anchor.targetGapMinM = preset.minM;
      anchor.targetGapMaxM = preset.maxM;
    }
  } else {
    const range = normalizeGapRange(
      THREE.MathUtils.clamp(Number(ui.gapMin.value) || 0, -5, 10),
      THREE.MathUtils.clamp(Number(ui.gapMax.value) || 0, -5, 10),
    );
    anchor.targetGapMinM = range.minM;
    anchor.targetGapMaxM = range.maxM;
    if (source === "range") anchor.relation = "custom";
  }
  anchor.boundaryOffsetM = THREE.MathUtils.clamp(Number(ui.boundaryOffset.value) || 0, -10, 10);
  saveDiagnosticAnchors();
  syncDiagnosticAnchorInputs();
  pendingAutoFit = null;
  applyAutoFitBtn.disabled = true;
  autoFitResult.textContent = "Anchor review changed. Confirm A/B/C, then run Solve X/Z.";
}

function updateDiagnosticTimeFromUi(id: DiagnosticAnchorId) {
  const maxTime = activeLap.t.at(-1) ?? activeLap.meta.lap_time_seconds;
  const time = THREE.MathUtils.clamp(Number(anchorUi[id].time.value) || 0, 0, maxTime);
  const distanceM = sampleSeriesAtTime(activeLap, activeLap.dist, time);
  if (!Number.isFinite(distanceM)) {
    autoFitResult.textContent = "Anchor " + id + " could not use the AiM time: lap distance is unavailable.";
    return;
  }
  diagnosticAnchors[id].referenceTime = time;
  diagnosticAnchors[id].distanceM = distanceM;
  replayTime = time;
  diagnosticAnchorId = id;
  playing = false;
  playBtn.textContent = "Play";
  saveDiagnosticAnchors();
  syncDiagnosticAnchorInputs();
  pendingAutoFit = null;
  applyAutoFitBtn.disabled = true;
  updateVehicle(replayTime, true);
  renderDiagnosticComparison();
  autoFitResult.textContent = "Anchor " + id + " synced to AiM lap time " + time.toFixed(2) + "s / " + distanceM.toFixed(1) + "m.";
}

function resetDiagnosticAnchors() {
  diagnosticAnchors = cloneDefaultDiagnosticAnchors();
  saveDiagnosticAnchors();
  syncDiagnosticAnchorInputs();
  diagnosticAnchorId = null;
  pendingAutoFit = null;
  applyAutoFitBtn.disabled = true;
  autoFitResult.textContent = "Default Brake / T1 clip / T2 clip stations restored.";
  renderDiagnosticComparison();
}

function loadAlignment() {
  try {
    const currentValue = localStorage.getItem(ALIGNMENT_STORAGE_KEY);
    const saved = JSON.parse(currentValue ?? localStorage.getItem(LEGACY_ALIGNMENT_STORAGE_KEY) ?? "null") as {
      x?: number; z?: number; rotationDeg?: number; rigidFit?: boolean;
    } | null;
    alignmentX = Number.isFinite(saved?.x) ? saved!.x! : ESTIMATED_ALIGNMENT.x;
    alignmentZ = Number.isFinite(saved?.z) ? saved!.z! : ESTIMATED_ALIGNMENT.z;
    alignmentRotationDeg = currentValue !== null && Number.isFinite(saved?.rotationDeg) ? saved!.rotationDeg! : 0;
    rigidFitEnabled = currentValue !== null && saved?.rigidFit === true;
  } catch {
    alignmentX = ESTIMATED_ALIGNMENT.x;
    alignmentZ = ESTIMATED_ALIGNMENT.z;
    alignmentRotationDeg = 0;
    rigidFitEnabled = false;
  }
  syncAlignmentInputs();
}

function syncAlignmentInputs() {
  offsetXInput.value = alignmentX.toFixed(1);
  offsetZInput.value = alignmentZ.toFixed(1);
  rotationDegInput.value = alignmentRotationDeg.toFixed(1);
  rotationDegInput.disabled = !rigidFitEnabled;
  rigidFitChk.checked = rigidFitEnabled;
}

function applyAlignment(x: number, z: number, rotationDeg = alignmentRotationDeg, rigidFit = rigidFitEnabled) {
  alignmentX = Math.round(x * 10) / 10;
  alignmentZ = Math.round(z * 10) / 10;
  alignmentRotationDeg = Math.round(rotationDeg * 10) / 10;
  rigidFitEnabled = rigidFit;
  localStorage.setItem(ALIGNMENT_STORAGE_KEY, JSON.stringify({
    x: alignmentX,
    z: alignmentZ,
    rotationDeg: alignmentRotationDeg,
    rigidFit: rigidFitEnabled,
  }));
  syncAlignmentInputs();
  if (activeLap) updateVehicle(replayTime, true);
  refreshDiagnosticPaths();
  renderDiagnosticComparison();
}

function alignPose(sample: ReturnType<typeof sampleLap>) {
  if (!rigidFitEnabled) {
    return { x: sample.x + alignmentX, z: sample.z + alignmentZ, heading: sample.heading };
  }

  // Positive map rotation is clockwise in the top-view X/Z coordinate plane.
  const angle = alignmentRotationDeg * DEG;
  const cos = Math.cos(angle);
  const sin = Math.sin(angle);
  const dx = sample.x - RIGID_ALIGNMENT_ANCHOR.x;
  const dz = sample.z - RIGID_ALIGNMENT_ANCHOR.z;
  const forwardX = Math.sin(sample.heading);
  const forwardZ = Math.cos(sample.heading);
  const rotatedForwardX = cos * forwardX - sin * forwardZ;
  const rotatedForwardZ = sin * forwardX + cos * forwardZ;
  return {
    x: RIGID_ALIGNMENT_ANCHOR.x + alignmentX + cos * dx - sin * dz,
    z: RIGID_ALIGNMENT_ANCHOR.z + alignmentZ + sin * dx + cos * dz,
    heading: Math.atan2(rotatedForwardX, rotatedForwardZ),
  };
}

function toDiagnosticPose(pose: { x: number; z: number; heading: number }): DiagnosticPose {
  return { x: pose.x, z: pose.z, heading: pose.heading };
}

function timeAtDistance(lap: LapData, targetDistance: number, fallbackTime: number) {
  if (lap.dist.length !== lap.t.length || lap.dist.length < 2) return fallbackTime;
  const clamped = THREE.MathUtils.clamp(targetDistance, lap.dist[0], lap.dist.at(-1) ?? targetDistance);
  let low = 0;
  let high = lap.dist.length - 1;
  while (low + 1 < high) {
    const middle = (low + high) >> 1;
    if (lap.dist[middle] <= clamped) low = middle;
    else high = middle;
  }
  const span = lap.dist[high] - lap.dist[low];
  const alpha = span > 0 ? (clamped - lap.dist[low]) / span : 0;
  return THREE.MathUtils.lerp(lap.t[low], lap.t[high], alpha);
}

function sampleSeriesAtTime(lap: LapData, series: number[], time: number) {
  if (series.length !== lap.t.length || series.length < 2) return Number.NaN;
  const index = segmentAt(lap.t, time);
  const span = lap.t[index + 1] - lap.t[index];
  const alpha = span > 0 ? (time - lap.t[index]) / span : 0;
  return interpolate(series, index, alpha);
}

function diagnosticTimeForLap(lap: LapData, anchorId: DiagnosticAnchorId) {
  const anchor = diagnosticAnchors[anchorId];
  return timeAtDistance(lap, anchor.distanceM, anchor.referenceTime);
}

function setDiagnosticAnchorAtCurrentTime(anchorId: DiagnosticAnchorId) {
  const distanceM = sampleSeriesAtTime(activeLap, activeLap.dist, replayTime);
  if (!Number.isFinite(distanceM)) {
    autoFitResult.textContent = `Anchor ${anchorId} could not be set: lap distance is unavailable.`;
    return;
  }
  diagnosticAnchors[anchorId].referenceTime = replayTime;
  diagnosticAnchors[anchorId].distanceM = distanceM;
  diagnosticAnchorId = anchorId;
  saveDiagnosticAnchors();
  syncDiagnosticAnchorInputs();
  pendingAutoFit = null;
  applyAutoFitBtn.disabled = true;
  autoFitResult.textContent = `Anchor ${anchorId} set at Lap ${activeLap.meta.lap}, ${replayTime.toFixed(2)}s / ${distanceM.toFixed(1)}m.`;
  renderDiagnosticComparison();
}

function gapForSide(clearance: RoadClearance, side: DiagnosticSide) {
  return side === "left" ? clearance.leftEnvelopeGapM : clearance.rightEnvelopeGapM;
}

interface FitAnchorObservation {
  id: DiagnosticAnchorId;
  pose: DiagnosticPose;
  side: DiagnosticSide;
  relation: CurbRelation;
  confidence: ObservationConfidence;
  targetGapMinM: number;
  targetGapMaxM: number;
  weight: number;
  rawGapM: number;
}

interface FitCandidatePoint {
  id: DiagnosticAnchorId;
  side: DiagnosticSide;
  relation: CurbRelation;
  targetGapMinM: number;
  targetGapMaxM: number;
  weight: number;
  actualGapM: number;
  residualM: number;
}

interface FitCandidateEvaluation {
  x: number;
  z: number;
  rmseM: number;
  maxAbsResidualM: number;
  points: FitCandidatePoint[];
}

function collectFitObservations(): FitAnchorObservation[] {
  if (!diagnostics) throw new Error("Road diagnostics are not ready.");
  return DIAGNOSTIC_ANCHOR_IDS.map((id) => {
    const anchor = diagnosticAnchors[id];
    if (anchor.relation === "unreviewed") {
      throw new Error("Anchor " + id + " has not been confirmed from the AiM video.");
    }
    const time = diagnosticTimeForLap(activeLap, id);
    const pose = toDiagnosticPose(sampleLap(activeLap, time));
    const clearance = diagnostics!.measurePose(pose);
    const rawGapM = gapForSide(clearance, anchor.side);
    if (!clearance.onRoad || rawGapM === null || !Number.isFinite(rawGapM)) {
      throw new Error(`Anchor ${id} is outside the collision mesh. Set the station again.`);
    }
    const target = effectiveMeshGapRange(
      anchor.targetGapMinM,
      anchor.targetGapMaxM,
      anchor.boundaryOffsetM,
    );
    return {
      id,
      pose,
      side: anchor.side,
      relation: anchor.relation,
      confidence: anchor.confidence,
      targetGapMinM: target.minM,
      targetGapMaxM: target.maxM,
      weight: observationWeight(anchor.confidence),
      rawGapM,
    };
  });
}

function evaluateFitCandidate(
  observations: FitAnchorObservation[],
  x: number,
  z: number,
): FitCandidateEvaluation | null {
  if (!diagnostics) return null;
  const points: FitCandidatePoint[] = [];
  for (const observation of observations) {
    const clearance = diagnostics.measurePose({
      x: observation.pose.x + x,
      z: observation.pose.z + z,
      heading: observation.pose.heading,
    });
    const actualGapM = gapForSide(clearance, observation.side);
    if (!clearance.onRoad || actualGapM === null || !Number.isFinite(actualGapM)) return null;
    points.push({
      id: observation.id,
      side: observation.side,
      relation: observation.relation,
      targetGapMinM: observation.targetGapMinM,
      targetGapMaxM: observation.targetGapMaxM,
      weight: observation.weight,
      actualGapM,
      residualM: residualToGapRange(
        actualGapM,
        observation.targetGapMinM,
        observation.targetGapMaxM,
      ),
    });
  }
  const totalWeight = points.reduce((sum, point) => sum + point.weight, 0);
  const squaredError = points.reduce((sum, point) => sum + point.weight * point.residualM ** 2, 0);
  return {
    x,
    z,
    rmseM: Math.sqrt(squaredError / Math.max(totalWeight, 1e-6)),
    maxAbsResidualM: Math.max(...points.map((point) => Math.abs(point.residualM))),
    points,
  };
}

function fitJacobianRows(
  observations: FitAnchorObservation[],
  base: FitCandidateEvaluation,
): LeastSquaresRow[] | null {
  const epsilon = 0.15;
  const shiftedX = evaluateFitCandidate(observations, base.x + epsilon, base.z);
  const shiftedZ = evaluateFitCandidate(observations, base.x, base.z + epsilon);
  if (!shiftedX || !shiftedZ) return null;
  return base.points.map((point, index) => ({
    ax: (shiftedX.points[index].actualGapM - point.actualGapM) / epsilon,
    az: (shiftedZ.points[index].actualGapM - point.actualGapM) / epsilon,
    b: -point.residualM,
    weight: point.weight,
  }));
}

function refineFitCandidate(
  observations: FitAnchorObservation[],
  initialX: number,
  initialZ: number,
) {
  if (Math.hypot(initialX, initialZ) > MAX_AUTO_FIT_TRANSLATION_M) return null;
  let best = evaluateFitCandidate(observations, initialX, initialZ);
  if (!best) return null;
  for (let iteration = 0; iteration < 7; iteration += 1) {
    const rows = fitJacobianRows(observations, best);
    if (!rows) break;
    const step = solveLeastSquares2D(rows, 1e-3);
    if (!step) break;
    const stepLength = Math.hypot(step.x, step.z);
    const scaleLimit = stepLength > 2.5 ? 2.5 / stepLength : 1;
    let improved: FitCandidateEvaluation | null = null;
    for (const lineScale of [1, 0.5, 0.25, 0.1]) {
      const x = best.x + step.x * scaleLimit * lineScale;
      const z = best.z + step.z * scaleLimit * lineScale;
      if (Math.hypot(x, z) > MAX_AUTO_FIT_TRANSLATION_M) continue;
      const trial = evaluateFitCandidate(observations, x, z);
      if (trial && trial.rmseM + 1e-4 < best.rmseM) {
        improved = trial;
        break;
      }
    }
    if (!improved) break;
    best = improved;
    if (stepLength * scaleLimit < 0.01) break;
  }
  return best;
}

function searchValidFitCandidate(
  observations: FitAnchorObservation[],
  initial: FitCandidateEvaluation,
) {
  let best = initial;
  const directions = [
    [1, 0], [-1, 0], [0, 1], [0, -1],
    [Math.SQRT1_2, Math.SQRT1_2], [Math.SQRT1_2, -Math.SQRT1_2],
    [-Math.SQRT1_2, Math.SQRT1_2], [-Math.SQRT1_2, -Math.SQRT1_2],
  ] as const;
  for (const distance of [2, 1, 0.5, 0.2, 0.1]) {
    for (let attempt = 0; attempt < 6; attempt += 1) {
      let improved: FitCandidateEvaluation | null = null;
      for (const [dx, dz] of directions) {
        const x = best.x + dx * distance;
        const z = best.z + dz * distance;
        if (Math.hypot(x, z) > MAX_AUTO_FIT_TRANSLATION_M) continue;
        const trial = evaluateFitCandidate(observations, x, z);
        if (trial && (!improved || trial.rmseM < improved.rmseM)) improved = trial;
      }
      if (!improved || improved.rmseM + 1e-4 >= best.rmseM) break;
      best = improved;
    }
  }
  return best;
}

function fitCondition(observations: FitAnchorObservation[], evaluation: FitCandidateEvaluation) {
  const rows = fitJacobianRows(observations, evaluation);
  if (!rows) return Number.POSITIVE_INFINITY;
  return solveLeastSquares2D(rows.map((row) => ({ ...row, b: 0 })))?.condition ?? Number.POSITIVE_INFINITY;
}

function lapRoadCoverage(lap: LapData, x: number, z: number) {
  if (!diagnostics) return 0;
  const stride = Math.max(1, Math.floor(lap.t.length / 100));
  let hits = 0;
  let total = 0;
  for (let index = 0; index < lap.t.length; index += stride) {
    const position = latLngToLocal(lap.lat[index], lap.lng[index]);
    if (diagnostics.isOnRoad(position.x + x, position.z + z)) hits += 1;
    total += 1;
  }
  return total > 0 ? hits / total : 0;
}

function renderAutoFitReport(
  evaluation: FitCandidateEvaluation,
  condition: number,
  coverage: number,
  baselineCoverage: number,
  status: string,
) {
  const geometricConfidence = confidenceForFit(condition, evaluation.rmseM);
  const coverageAcceptable = coverage >= 0.91 && coverage + 0.05 >= baselineCoverage;
  const auditPass = evaluation.rmseM < 0.3 && condition < 10 &&
    coverageAcceptable && evaluation.maxAbsResidualM < 1;
  const confidence = auditPass ? geometricConfidence : "LOW";
  const lines = evaluation.points.map((point) =>
    point.id + " " + point.side.toUpperCase().padEnd(5) +
    " " + point.relation.padEnd(9) +
    " target " + point.targetGapMinM.toFixed(2) + ".." + point.targetGapMaxM.toFixed(2) +
    " actual " + point.actualGapM.toFixed(2) +
    " residual " + (point.residualM >= 0 ? "+" : "") + point.residualM.toFixed(2) + "m",
  );
  lines.unshift("Audit " + (auditPass ? "PASS" : "REVIEW") + " | limits RMSE<0.30 / cond<10 / coverage>=91%");
  autoFitResult.textContent =
    `Lap ${activeLap.meta.lap} | translation only | log unchanged\n` +
    `X ${evaluation.x.toFixed(2)}m / Z ${evaluation.z.toFixed(2)}m\n` +
    `Confidence ${confidence} | RMSE ${evaluation.rmseM.toFixed(2)}m | condition ${Number.isFinite(condition) ? condition.toFixed(2) : "INF"}\n` +
    `Lap road coverage ${(coverage * 100).toFixed(0)}% | reference ${(baselineCoverage * 100).toFixed(0)}%\n` +
    lines.join("\n") +
    `\n${status}` +
    (confidence === "LOW" ? "\nWarning: translation alone does not explain all three targets reliably." : "") +
    (Math.hypot(evaluation.x, evaluation.z) > MAX_AUTO_FIT_TRANSLATION_M - 0.25
      ? `\nWarning: solution reached the ${MAX_AUTO_FIT_TRANSLATION_M}m search limit.`
      : "");
}

function runThreePointAutoFit() {
  autoFitBtn.disabled = true;
  try {
    const observations = collectFitObservations();
    const linear = solveTranslationFit(observations.map((observation) => ({
      id: observation.id,
      heading: observation.pose.heading,
      side: observation.side,
      currentGapM: observation.rawGapM,
      targetGapM: nearestValueInGapRange(
        observation.rawGapM,
        observation.targetGapMinM,
        observation.targetGapMaxM,
      ),
      weight: observation.weight,
    })));
    if (!linear) throw new Error("The three lateral directions cannot determine both X and Z.");
    const seedCoordinates = [
      { x: linear.x, z: linear.z },
      { x: alignmentX, z: alignmentZ },
      { x: ESTIMATED_ALIGNMENT.x, z: ESTIMATED_ALIGNMENT.z },
      { x: 0, z: 0 },
    ];
    const validSeeds = seedCoordinates
      .filter((seed, index, all) =>
        Math.hypot(seed.x, seed.z) <= MAX_AUTO_FIT_TRANSLATION_M &&
        all.findIndex((other) => Math.hypot(seed.x - other.x, seed.z - other.z) < 0.01) === index,
      )
      .map((seed) => evaluateFitCandidate(observations, seed.x, seed.z))
      .filter((candidate): candidate is FitCandidateEvaluation => candidate !== null);
    if (validSeeds.length === 0) throw new Error("No translation seed keeps all three stations on the collision mesh.");
    let refined = validSeeds.reduce((best, candidate) => candidate.rmseM < best.rmseM ? candidate : best);
    refined = searchValidFitCandidate(observations, refined);
    refined = refineFitCandidate(observations, refined.x, refined.z) ?? refined;
    refined = searchValidFitCandidate(observations, refined);
    const condition = fitCondition(observations, refined);
    const coverage = lapRoadCoverage(activeLap, refined.x, refined.z);
    const baselineCoverage = Math.max(
      lapRoadCoverage(activeLap, 0, 0),
      lapRoadCoverage(activeLap, alignmentX, alignmentZ),
      lapRoadCoverage(activeLap, ESTIMATED_ALIGNMENT.x, ESTIMATED_ALIGNMENT.z),
    );
    pendingAutoFit = {
      evaluation: refined,
      condition,
      coverage,
      baselineCoverage,
      sourceLap: activeLap.meta.lap,
    };
    applyAutoFitBtn.disabled = false;
    renderAutoFitReport(
      refined,
      condition,
      coverage,
      baselineCoverage,
      "Candidate only. Press Apply to preview it; Restore returns to the previous alignment.",
    );
  } catch (error) {
    pendingAutoFit = null;
    applyAutoFitBtn.disabled = true;
    autoFitResult.textContent = `Fit not applied: ${error instanceof Error ? error.message : String(error)}`;
  } finally {
    autoFitBtn.disabled = false;
  }
}

function applyPendingAutoFit() {
  if (!pendingAutoFit) return;
  const pending = pendingAutoFit;
  if (pending.sourceLap !== activeLap.meta.lap) {
    pendingAutoFit = null;
    applyAutoFitBtn.disabled = true;
    autoFitResult.textContent = "Fit expired because the source lap changed. Run Solve X/Z again.";
    return;
  }
  autoFitRestore = {
    x: alignmentX,
    z: alignmentZ,
    rotationDeg: alignmentRotationDeg,
    rigidFit: rigidFitEnabled,
  };
  applyAlignment(pending.evaluation.x, pending.evaluation.z, 0, false);
  const observations = collectFitObservations();
  const applied = evaluateFitCandidate(observations, alignmentX, alignmentZ) ?? pending.evaluation;
  const coverage = lapRoadCoverage(activeLap, alignmentX, alignmentZ);
  renderAutoFitReport(
    applied,
    fitCondition(observations, applied),
    coverage,
    pending.baselineCoverage,
    "Applied to the viewer. Restore returns to the previous alignment.",
  );
  pendingAutoFit = null;
  applyAutoFitBtn.disabled = true;
  undoAutoFitBtn.disabled = false;
}

function restorePreAutoFitAlignment() {
  if (!autoFitRestore) return;
  const restore = autoFitRestore;
  autoFitRestore = null;
  applyAlignment(restore.x, restore.z, restore.rotationDeg, restore.rigidFit);
  undoAutoFitBtn.disabled = true;
  autoFitResult.textContent = `Restored X ${alignmentX.toFixed(1)}m / Z ${alignmentZ.toFixed(1)}m.`;
}

function refreshDiagnosticPaths() {
  if (!diagnosticsEnabled || !diagnostics || !activeLap) return;
  const rawPath: DiagnosticPose[] = [];
  const alignedPath: DiagnosticPose[] = [];
  const appendAt = (time: number) => {
    const raw = sampleLap(activeLap, time);
    rawPath.push(toDiagnosticPose(raw));
    alignedPath.push(toDiagnosticPose(alignPose(raw)));
  };
  for (let index = 0; index < activeLap.t.length; index += DIAGNOSTIC_PATH_STRIDE) {
    appendAt(activeLap.t[index]);
  }
  const finalTime = activeLap.t.at(-1);
  if (finalTime !== undefined && rawPath.length > 0 && activeLap.t[(rawPath.length - 1) * DIAGNOSTIC_PATH_STRIDE] !== finalTime) {
    appendAt(finalTime);
  }
  diagnostics.setPaths(rawPath, alignedPath);
}

function formatDiagnosticValue(value: number | null) {
  if (value === null || !Number.isFinite(value)) return "  --";
  return `${value < 0 ? "" : " "}${value.toFixed(2)}`;
}

function formatGapPair(clearance: RoadClearance) {
  if (!clearance.onRoad) return "OFF ROAD";
  return `L ${formatDiagnosticValue(clearance.leftEnvelopeGapM)} / R ${formatDiagnosticValue(clearance.rightEnvelopeGapM)}`;
}

function updateDiagnosticReadout(
  rawPose: ReturnType<typeof sampleLap>,
  alignedPose: ReturnType<typeof alignPose>,
  time: number,
  force = false,
) {
  if (!diagnosticsEnabled || !diagnostics) return;
  const now = performance.now();
  if (!force && now - lastDiagnosticUpdate < DIAGNOSTIC_UPDATE_INTERVAL_MS) return;
  lastDiagnosticUpdate = now;
  const measurement = diagnostics.update(toDiagnosticPose(rawPose), toDiagnosticPose(alignedPose));
  const distance = sampleSeriesAtTime(activeLap, activeLap.dist, time);
  const anchor = diagnosticAnchorId ? `Anchor ${diagnosticAnchorId}` : "Free time";
  const raw = measurement.raw;
  const aligned = measurement.aligned;
  diagnosticMetrics.textContent =
    `${anchor} | Lap ${activeLap.meta.lap} | t ${time.toFixed(2)} s | d ${Number.isFinite(distance) ? distance.toFixed(1) : "--"} m\n` +
    `Envelope gap      LEFT       RIGHT      ROAD\n` +
    `Raw log       ${formatDiagnosticValue(raw.leftEnvelopeGapM)} m ${formatDiagnosticValue(raw.rightEnvelopeGapM)} m  ${formatDiagnosticValue(raw.roadWidthM)} m\n` +
    `Aligned       ${formatDiagnosticValue(aligned.leftEnvelopeGapM)} m ${formatDiagnosticValue(aligned.rightEnvelopeGapM)} m  ${formatDiagnosticValue(aligned.roadWidthM)} m\n` +
    `Center->edge  ${formatDiagnosticValue(aligned.leftCenterM)} m ${formatDiagnosticValue(aligned.rightCenterM)} m\n` +
    `Negative gap means the 1.695 m vehicle envelope crosses the mesh edge.`;
}

function renderDiagnosticComparison() {
  if (!diagnosticsEnabled || !diagnostics || !diagnosticAnchorId) {
    diagnosticComparison.textContent = "Select A, B or C to compare all laps.";
    return;
  }
  const rows = laps.map((lap) => {
    const time = diagnosticTimeForLap(lap, diagnosticAnchorId!);
    const rawPose = sampleLap(lap, time);
    const alignedPose = alignPose(rawPose);
    const raw = diagnostics!.measurePose(toDiagnosticPose(rawPose));
    const aligned = diagnostics!.measurePose(toDiagnosticPose(alignedPose));
    return `<tr${lap === activeLap ? " class=\"active-lap\"" : ""}><td>Lap ${lap.meta.lap}</td><td>${time.toFixed(2)}</td><td>${formatGapPair(raw)}</td><td>${formatGapPair(aligned)}</td></tr>`;
  });
  diagnosticComparison.innerHTML =
    `<table><thead><tr><th>${diagnosticAnchorId} / gap m</th><th>t s</th><th>Raw L/R</th><th>Aligned L/R</th></tr></thead><tbody>${rows.join("")}</tbody></table>`;
}

function showTopView() {
  playing = false;
  playBtn.textContent = "Play";
  followChk.checked = false;
  controls.enabled = true;
  diagnostics?.setPathWidth(1.4);
  camera.position.set(car.position.x, car.position.y + 45, car.position.z + 0.01);
  controls.target.copy(car.position);
  controls.update();
}

function showCircuitView() {
  if (!satelliteOverlay) return;
  playing = false;
  playBtn.textContent = "Play";
  followChk.checked = false;
  controls.enabled = true;
  diagnostics?.setPathWidth(7);
  const bounds = satelliteOverlay.getBounds();
  const center = bounds.getCenter(new THREE.Vector3());
  const size = bounds.getSize(new THREE.Vector3());
  const verticalFov = camera.fov * DEG;
  const visibleHeight = Math.max(size.z, size.x / Math.max(camera.aspect, 0.1));
  const height = visibleHeight / (2 * Math.tan(verticalFov / 2)) * 1.08;
  camera.position.set(center.x, center.y + height, center.z + 0.01);
  controls.target.copy(center);
  controls.update();
}

async function loadSelectedSatelliteSource() {
  if (!satelliteOverlay) throw new Error("Satellite overlay is not ready.");
  const sourceId = satelliteSource.value as SatelliteSourceId;
  const source = SATELLITE_SOURCES[sourceId] ?? SATELLITE_SOURCES.esri;
  satelliteStatus.textContent = `Loading ${source.label}...`;
  await satelliteOverlay.setSource(source.imageUrl);
  satelliteOverlay.setOpacity(Number(satelliteOpacity.value));
  satelliteOverlay.setVisible(diagnosticsEnabled && satelliteChk.checked);
  satelliteStatus.textContent =
    `${source.label} @ ${Math.round(Number(satelliteOpacity.value) * 100)}% | GPS geo-lock | visual validation only`;
}

async function setSatelliteEnabled(enabled: boolean) {
  satelliteChk.checked = enabled;
  if (!satelliteOverlay) return;
  if (!enabled) {
    satelliteOverlay.setVisible(false);
    satelliteStatus.textContent = "Photo overlay off. GPS geo-lock remains unchanged.";
    return;
  }
  satelliteChk.disabled = true;
  satelliteSource.disabled = true;
  try {
    await loadSelectedSatelliteSource();
    showCircuitView();
  } catch (error) {
    satelliteChk.checked = false;
    satelliteOverlay.setVisible(false);
    satelliteStatus.textContent = `Photo load failed: ${error instanceof Error ? error.message : String(error)}`;
  } finally {
    satelliteChk.disabled = false;
    satelliteSource.disabled = false;
  }
}

function jumpToDiagnosticAnchor(anchorId: DiagnosticAnchorId) {
  diagnosticAnchorId = anchorId;
  replayTime = diagnosticTimeForLap(activeLap, anchorId);
  playing = false;
  playBtn.textContent = "Play";
  updateVehicle(replayTime, true);
  showTopView();
  renderDiagnosticComparison();
}

function setDiagnosticsEnabled(enabled: boolean) {
  diagnosticsEnabled = enabled;
  diagnosticChk.checked = enabled;
  diagnosticsPanel.hidden = !enabled;
  panel.classList.toggle("diagnostic-open", enabled);
  diagnostics?.setVisible(enabled);
  satelliteOverlay?.setVisible(enabled && satelliteChk.checked);
  if (!enabled) {
    diagnosticAnchorId = null;
    return;
  }
  playing = false;
  playBtn.textContent = "Play";
  lastDiagnosticUpdate = 0;
  refreshDiagnosticPaths();
  updateVehicle(replayTime, true);
  showTopView();
  renderDiagnosticComparison();
}

function setLap(index: number) {
  if (activeLap && activeLap !== laps[index]) {
    pendingAutoFit = null;
    applyAutoFitBtn.disabled = true;
  }
  activeLap = laps[index];
  replayTime = diagnosticAnchorId ? diagnosticTimeForLap(activeLap, diagnosticAnchorId) : 0;
  timeSlider.max = String(activeLap.t.at(-1) ?? activeLap.meta.lap_time_seconds);
  timeSlider.value = String(replayTime);
  lapInfo.textContent = `${activeLap.meta.vehicle_id} | ${activeLap.t.length} samples | ${activeLap.meta.lap_time}`;
  refreshDiagnosticPaths();
  updateVehicle(replayTime, true);
  renderDiagnosticComparison();
}

function updateVehicle(time: number, snapCamera = false) {
  const sample = sampleLap(activeLap, time);
  const aligned = alignPose(sample);
  const y = groundHeight(aligned.x, aligned.z) + CAR_GROUND_CLEARANCE;
  car.position.set(aligned.x, y, aligned.z);
  car.rotation.set(0, aligned.heading, 0);
  carForward.set(Math.sin(aligned.heading), 0, Math.cos(aligned.heading));

  if (followChk.checked) {
    followTarget.set(aligned.x, y + 1.1, aligned.z);
    desiredCamera.copy(followTarget).addScaledVector(carForward, -11).add(new THREE.Vector3(0, 5, 0));
    if (snapCamera) camera.position.copy(desiredCamera);
    else camera.position.lerp(desiredCamera, 0.08);
    controls.target.lerp(followTarget, snapCamera ? 1 : 0.14);
  }

  timeSlider.value = String(time);
  timeLabel.textContent = `${formatTime(time)} / ${formatTime(activeLap.t.at(-1) ?? 0)}`;
  hud.textContent = `Lap ${activeLap.meta.lap}  |  ${sample.speed.toFixed(1)} km/h  |  Gear ${sample.gear}\n` +
    `Brake ${sample.brake.toFixed(0)}%  |  Offset X ${alignmentX.toFixed(1)} m / Z ${alignmentZ.toFixed(1)} m\n` +
    (rigidFitEnabled ? `Rigid fit ON  |  Map rotation ${alignmentRotationDeg.toFixed(1)} deg` : "Rigid fit OFF  |  Translation only");
  updateDiagnosticReadout(sample, aligned, time, snapCamera);
}

async function boot() {
  loadAlignment();
  loadDiagnosticAnchors();
  const [track, lapsIndex, satelliteMeta] = await Promise.all([
    fetchJson<{ origin: TrackOrigin }>(`${DATA_BASE}/tracks/${TRACK_ID}/track.json`),
    fetchJson<LapsIndex>(`${DATA_BASE}/races/${RACE_ID}/laps.json`),
    fetchJson<SatelliteMeta>(`${DATA_BASE}/tracks/${TRACK_ID}/satellite_z19_meta.json`),
  ]);
  origin = track.origin;

  loading.textContent = "Loading full circuit materials and objects... 0%";
  const [world, road, carModel] = await Promise.all([
    loadGltf(WORLD_URL, (ratio) => {
      loading.textContent = `Loading full circuit materials and objects... ${Math.round(ratio * 100)}%`;
    }),
    loadGltf(ROAD_URL),
    loadGltf(CAR_URL),
  ]);
  const worldBounds = prepareWorld(world);
  prepareRoad(road);
  normalizeCar(carModel);
  diagnostics = new ScanDiagnostics(scene, roadMeshes, {
    length: CAR_LENGTH,
    width: CAR_WIDTH,
    wheelbase: CAR_WHEELBASE,
    track: CAR_WHEEL_TRACK,
  });
  satelliteOverlay = new ScanSatelliteOverlay(scene, origin, satelliteMeta, worldBounds.min.y - 5);

  const records = lapsIndex.laps.filter((record): record is LapRecord & { data_file: string } => Boolean(record.data_file));
  laps = await Promise.all(records.map((record) => fetchJson<LapData>(`${DATA_BASE}/races/${RACE_ID}/${record.data_file}`)));
  for (const [index, lap] of laps.entries()) {
    const option = document.createElement("option");
    option.value = String(index);
    option.textContent = `Lap ${lap.meta.lap} - ${lap.meta.lap_time}`;
    lapSelect.append(option);
  }

  const size = worldBounds.getSize(new THREE.Vector3());
  hud.textContent = `Fuji AC MOD  |  ${size.x.toFixed(0)} x ${size.z.toFixed(0)} m`;
  setLap(0);
  loading.style.display = "none";
  panel.style.display = "";
}

lapSelect.addEventListener("change", () => setLap(Number(lapSelect.value)));
playBtn.addEventListener("click", () => {
  playing = !playing;
  playBtn.textContent = playing ? "Pause" : "Play";
  if (playing && diagnosticAnchorId) {
    diagnosticAnchorId = null;
    renderDiagnosticComparison();
  }
});
speedSelect.addEventListener("change", () => { playbackSpeed = Number(speedSelect.value); });
timeSlider.addEventListener("input", () => {
  replayTime = Number(timeSlider.value);
  playing = false;
  playBtn.textContent = "Play";
  diagnosticAnchorId = null;
  updateVehicle(replayTime, true);
  renderDiagnosticComparison();
});
followChk.addEventListener("change", () => {
  controls.enabled = !followChk.checked;
  if (followChk.checked) updateVehicle(replayTime, true);
});
offsetXInput.addEventListener("change", () => applyAlignment(Number(offsetXInput.value) || 0, alignmentZ));
offsetZInput.addEventListener("change", () => applyAlignment(alignmentX, Number(offsetZInput.value) || 0));
rigidFitChk.addEventListener("change", () => applyAlignment(alignmentX, alignmentZ, alignmentRotationDeg, rigidFitChk.checked));
rotationDegInput.addEventListener("change", () => applyAlignment(
  alignmentX,
  alignmentZ,
  Number(rotationDegInput.value) || 0,
));
applyEstimateBtn.addEventListener("click", () => applyAlignment(
  ESTIMATED_ALIGNMENT.x,
  ESTIMATED_ALIGNMENT.z,
  0,
  false,
));
resetOffsetBtn.addEventListener("click", () => applyAlignment(0, 0, 0, false));
topViewBtn.addEventListener("click", showTopView);
diagnosticChk.addEventListener("change", () => setDiagnosticsEnabled(diagnosticChk.checked));
for (const id of DIAGNOSTIC_ANCHOR_IDS) {
  anchorUi[id].jump.addEventListener("click", () => jumpToDiagnosticAnchor(id));
  anchorUi[id].set.addEventListener("click", () => setDiagnosticAnchorAtCurrentTime(id));
  anchorUi[id].time.addEventListener("change", () => updateDiagnosticTimeFromUi(id));
  anchorUi[id].side.addEventListener("change", () => updateDiagnosticTargetFromUi(id));
  anchorUi[id].relation.addEventListener("change", () => updateDiagnosticTargetFromUi(id, "relation"));
  anchorUi[id].confidence.addEventListener("change", () => updateDiagnosticTargetFromUi(id));
  anchorUi[id].gapMin.addEventListener("change", () => updateDiagnosticTargetFromUi(id, "range"));
  anchorUi[id].gapMax.addEventListener("change", () => updateDiagnosticTargetFromUi(id, "range"));
  anchorUi[id].boundaryOffset.addEventListener("change", () => updateDiagnosticTargetFromUi(id));
}
autoFitBtn.addEventListener("click", runThreePointAutoFit);
applyAutoFitBtn.addEventListener("click", applyPendingAutoFit);
undoAutoFitBtn.addEventListener("click", restorePreAutoFitAlignment);
resetAnchorsBtn.addEventListener("click", resetDiagnosticAnchors);
satelliteChk.addEventListener("change", () => { void setSatelliteEnabled(satelliteChk.checked); });
satelliteSource.addEventListener("change", () => {
  if (satelliteChk.checked) void setSatelliteEnabled(true);
  else satelliteStatus.textContent = `${SATELLITE_SOURCES[satelliteSource.value as SatelliteSourceId].label} selected. Enable Photo overlay to load.`;
});
satelliteOpacity.addEventListener("input", () => {
  satelliteOverlay?.setOpacity(Number(satelliteOpacity.value));
  if (satelliteChk.checked) {
    const source = SATELLITE_SOURCES[satelliteSource.value as SatelliteSourceId];
    satelliteStatus.textContent = `${source.label} @ ${Math.round(Number(satelliteOpacity.value) * 100)}% | GPS geo-lock | visual validation only`;
  }
});
circuitViewBtn.addEventListener("click", showCircuitView);
refreshDiagnosticsBtn.addEventListener("click", () => {
  lastDiagnosticUpdate = 0;
  refreshDiagnosticPaths();
  updateVehicle(replayTime, true);
  renderDiagnosticComparison();
});

addEventListener("keydown", (event) => {
  if (event.target instanceof HTMLInputElement || event.target instanceof HTMLSelectElement) return;
  const step = event.shiftKey ? 1 : 0.1;
  if (event.key === "ArrowLeft") applyAlignment(alignmentX - step, alignmentZ);
  else if (event.key === "ArrowRight") applyAlignment(alignmentX + step, alignmentZ);
  else if (event.key === "ArrowUp") applyAlignment(alignmentX, alignmentZ - step);
  else if (event.key === "ArrowDown") applyAlignment(alignmentX, alignmentZ + step);
  else return;
  event.preventDefault();
});

addEventListener("resize", () => {
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
});

renderer.setAnimationLoop((now) => {
  const delta = Math.min((now - lastFrame) / 1000, 0.1);
  lastFrame = now;
  if (activeLap) {
    if (playing) {
      const duration = activeLap.t.at(-1) ?? 0;
      replayTime = duration > 0 ? (replayTime + delta * playbackSpeed) % duration : 0;
    }
    updateVehicle(replayTime);
  }
  controls.update();
  renderer.render(scene, camera);
});

boot().catch((error) => {
  loading.textContent = `Error: ${error instanceof Error ? error.message : String(error)}`;
  console.error(error);
});
