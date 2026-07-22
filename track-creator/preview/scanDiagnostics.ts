import * as THREE from "three";

export interface DiagnosticPose {
  x: number;
  z: number;
  heading: number;
}

export interface RoadClearance {
  onRoad: boolean;
  leftCenterM: number | null;
  rightCenterM: number | null;
  leftEnvelopeGapM: number | null;
  rightEnvelopeGapM: number | null;
  roadWidthM: number | null;
  groundY: number | null;
  leftEdge: THREE.Vector3 | null;
  rightEdge: THREE.Vector3 | null;
}

export interface DiagnosticMeasurement {
  raw: RoadClearance;
  aligned: RoadClearance;
}

interface VehicleDimensions {
  length: number;
  width: number;
  wheelbase: number;
  track: number;
}

interface HitRun {
  firstIndex: number;
  lastIndex: number;
}

interface SurfaceTriangle {
  ax: number; ay: number; az: number;
  bx: number; by: number; bz: number;
  cx: number; cy: number; cz: number;
}

const SCAN_RANGE_M = 25;
const SCAN_STEP_M = 0.25;
const EDGE_REFINEMENT_STEPS = 6;
const OVERLAY_LIFT_M = 0.18;
const SURFACE_CELL_M = 20;

class RoadSurfaceIndex {
  private readonly cells = new Map<string, SurfaceTriangle[]>();

  constructor(meshes: THREE.Mesh[]) {
    const a = new THREE.Vector3();
    const b = new THREE.Vector3();
    const c = new THREE.Vector3();
    for (const mesh of meshes) {
      mesh.updateWorldMatrix(true, false);
      const position = mesh.geometry.getAttribute("position");
      const index = mesh.geometry.index;
      const count = index?.count ?? position.count;
      for (let offset = 0; offset + 2 < count; offset += 3) {
        const ia = index ? index.getX(offset) : offset;
        const ib = index ? index.getX(offset + 1) : offset + 1;
        const ic = index ? index.getX(offset + 2) : offset + 2;
        a.set(position.getX(ia), position.getY(ia), position.getZ(ia)).applyMatrix4(mesh.matrixWorld);
        b.set(position.getX(ib), position.getY(ib), position.getZ(ib)).applyMatrix4(mesh.matrixWorld);
        c.set(position.getX(ic), position.getY(ic), position.getZ(ic)).applyMatrix4(mesh.matrixWorld);
        const projectedArea = (b.x - a.x) * (c.z - a.z) - (b.z - a.z) * (c.x - a.x);
        if (Math.abs(projectedArea) < 1e-7) continue;
        const triangle: SurfaceTriangle = {
          ax: a.x, ay: a.y, az: a.z,
          bx: b.x, by: b.y, bz: b.z,
          cx: c.x, cy: c.y, cz: c.z,
        };
        const minCellX = Math.floor(Math.min(a.x, b.x, c.x) / SURFACE_CELL_M);
        const maxCellX = Math.floor(Math.max(a.x, b.x, c.x) / SURFACE_CELL_M);
        const minCellZ = Math.floor(Math.min(a.z, b.z, c.z) / SURFACE_CELL_M);
        const maxCellZ = Math.floor(Math.max(a.z, b.z, c.z) / SURFACE_CELL_M);
        for (let cellX = minCellX; cellX <= maxCellX; cellX += 1) {
          for (let cellZ = minCellZ; cellZ <= maxCellZ; cellZ += 1) {
            const key = this.cellKey(cellX, cellZ);
            const bucket = this.cells.get(key);
            if (bucket) bucket.push(triangle);
            else this.cells.set(key, [triangle]);
          }
        }
      }
    }
  }

  heightAt(x: number, z: number): number | null {
    const cellX = Math.floor(x / SURFACE_CELL_M);
    const cellZ = Math.floor(z / SURFACE_CELL_M);
    const triangles = this.cells.get(this.cellKey(cellX, cellZ));
    if (!triangles) return null;
    let highest: number | null = null;
    for (const triangle of triangles) {
      const denominator =
        (triangle.bz - triangle.cz) * (triangle.ax - triangle.cx) +
        (triangle.cx - triangle.bx) * (triangle.az - triangle.cz);
      if (Math.abs(denominator) < 1e-9) continue;
      const weightA = (
        (triangle.bz - triangle.cz) * (x - triangle.cx) +
        (triangle.cx - triangle.bx) * (z - triangle.cz)
      ) / denominator;
      const weightB = (
        (triangle.cz - triangle.az) * (x - triangle.cx) +
        (triangle.ax - triangle.cx) * (z - triangle.cz)
      ) / denominator;
      const weightC = 1 - weightA - weightB;
      if (weightA < -1e-6 || weightB < -1e-6 || weightC < -1e-6) continue;
      const y = weightA * triangle.ay + weightB * triangle.by + weightC * triangle.cy;
      if (highest === null || y > highest) highest = y;
    }
    return highest;
  }

  private cellKey(cellX: number, cellZ: number) {
    return `${cellX}:${cellZ}`;
  }
}

export class ScanDiagnostics {
  private readonly group = new THREE.Group();
  private readonly surface: RoadSurfaceIndex;
  private readonly rawPath = this.makePath(0x29d9ff, 0.82);
  private readonly alignedPath = this.makePath(0xffb547, 0.9);
  private readonly rawFootprint = this.makeLoop(0x29d9ff);
  private readonly alignedFootprint = this.makeLoop(0xffb547);
  private readonly rawWheels = this.makePoints(0x29d9ff);
  private readonly alignedWheels = this.makePoints(0xffb547);
  private readonly crossSection = this.makeLine(0xffffff, 0.9);
  private readonly leftEdgeMarker = this.makeMarker(0x42e88b);
  private readonly rightEdgeMarker = this.makeMarker(0xff625c);
  private rawPoses: DiagnosticPose[] = [];
  private alignedPoses: DiagnosticPose[] = [];
  private pathWidthM = 1.4;

  constructor(
    scene: THREE.Scene,
    roadMeshes: THREE.Mesh[],
    private readonly vehicle: VehicleDimensions,
  ) {
    this.surface = new RoadSurfaceIndex(roadMeshes);
    this.group.name = "scan-diagnostics";
    this.group.visible = false;
    this.group.add(
      this.rawPath,
      this.alignedPath,
      this.rawFootprint,
      this.alignedFootprint,
      this.rawWheels,
      this.alignedWheels,
      this.crossSection,
      this.leftEdgeMarker,
      this.rightEdgeMarker,
    );
    scene.add(this.group);
  }

  setVisible(visible: boolean) {
    this.group.visible = visible;
  }

  setPaths(raw: DiagnosticPose[], aligned: DiagnosticPose[]) {
    this.rawPoses = raw;
    this.alignedPoses = aligned;
    this.setPathGeometry(this.rawPath, raw, OVERLAY_LIFT_M, this.pathWidthM);
    this.setPathGeometry(this.alignedPath, aligned, OVERLAY_LIFT_M + 0.06, this.pathWidthM);
  }

  setPathWidth(widthM: number) {
    const nextWidth = THREE.MathUtils.clamp(widthM, 0.5, 10);
    if (Math.abs(nextWidth - this.pathWidthM) < 0.01) return;
    this.pathWidthM = nextWidth;
    this.setPathGeometry(this.rawPath, this.rawPoses, OVERLAY_LIFT_M, this.pathWidthM);
    this.setPathGeometry(this.alignedPath, this.alignedPoses, OVERLAY_LIFT_M + 0.06, this.pathWidthM);
  }

  update(rawPose: DiagnosticPose, alignedPose: DiagnosticPose): DiagnosticMeasurement {
    const raw = this.measurePose(rawPose);
    const aligned = this.measurePose(alignedPose);
    this.updatePoseOverlay(this.rawFootprint, this.rawWheels, rawPose, raw.groundY, OVERLAY_LIFT_M + 0.08);
    this.updatePoseOverlay(
      this.alignedFootprint,
      this.alignedWheels,
      alignedPose,
      aligned.groundY,
      OVERLAY_LIFT_M + 0.16,
    );
    this.updateCrossSection(aligned);
    return { raw, aligned };
  }

  measurePose(pose: DiagnosticPose): RoadClearance {
    const forwardX = Math.sin(pose.heading);
    const forwardZ = Math.cos(pose.heading);
    const rightX = -forwardZ;
    const rightZ = forwardX;
    const hitSamples: boolean[] = [];
    const ySamples: Array<number | null> = [];
    const sampleCount = Math.round((SCAN_RANGE_M * 2) / SCAN_STEP_M) + 1;

    for (let index = 0; index < sampleCount; index += 1) {
      const offset = -SCAN_RANGE_M + index * SCAN_STEP_M;
      const y = this.surfaceY(pose.x + rightX * offset, pose.z + rightZ * offset);
      hitSamples.push(y !== null);
      ySamples.push(y);
    }

    const runs = this.findHitRuns(hitSamples);
    const centerIndex = Math.round(SCAN_RANGE_M / SCAN_STEP_M);
    const containing = runs.find((run) => run.firstIndex <= centerIndex && run.lastIndex >= centerIndex) ?? null;
    const selected = containing ?? this.closestRun(runs, centerIndex);
    if (!selected) return this.emptyClearance();

    const sampledLeft = -SCAN_RANGE_M + selected.firstIndex * SCAN_STEP_M;
    const sampledRight = -SCAN_RANGE_M + selected.lastIndex * SCAN_STEP_M;
    const leftOffset = selected.firstIndex > 0
      ? this.refineEdge(pose, rightX, rightZ, sampledLeft, sampledLeft - SCAN_STEP_M)
      : sampledLeft;
    const rightOffset = selected.lastIndex < sampleCount - 1
      ? this.refineEdge(pose, rightX, rightZ, sampledRight, sampledRight + SCAN_STEP_M)
      : sampledRight;
    const onRoad = containing !== null;
    const centerY = ySamples[centerIndex];
    const fallbackY = ySamples[selected.firstIndex] ?? ySamples[selected.lastIndex] ?? null;
    const groundY = centerY ?? fallbackY;
    const leftY = this.surfaceY(
      pose.x + rightX * (leftOffset + SCAN_STEP_M * 0.25),
      pose.z + rightZ * (leftOffset + SCAN_STEP_M * 0.25),
    ) ?? groundY;
    const rightY = this.surfaceY(
      pose.x + rightX * (rightOffset - SCAN_STEP_M * 0.25),
      pose.z + rightZ * (rightOffset - SCAN_STEP_M * 0.25),
    ) ?? groundY;
    const leftCenterM = onRoad ? Math.max(0, -leftOffset) : null;
    const rightCenterM = onRoad ? Math.max(0, rightOffset) : null;
    const halfWidth = this.vehicle.width / 2;

    return {
      onRoad,
      leftCenterM,
      rightCenterM,
      leftEnvelopeGapM: leftCenterM === null ? null : leftCenterM - halfWidth,
      rightEnvelopeGapM: rightCenterM === null ? null : rightCenterM - halfWidth,
      roadWidthM: rightOffset - leftOffset,
      groundY,
      leftEdge: leftY === null
        ? null
        : new THREE.Vector3(pose.x + rightX * leftOffset, leftY + OVERLAY_LIFT_M, pose.z + rightZ * leftOffset),
      rightEdge: rightY === null
        ? null
        : new THREE.Vector3(pose.x + rightX * rightOffset, rightY + OVERLAY_LIFT_M, pose.z + rightZ * rightOffset),
    };
  }

  isOnRoad(x: number, z: number) {
    return this.surfaceY(x, z) !== null;
  }

  private surfaceY(x: number, z: number): number | null {
    return this.surface.heightAt(x, z);
  }

  private refineEdge(
    pose: DiagnosticPose,
    rightX: number,
    rightZ: number,
    insideOffset: number,
    outsideOffset: number,
  ) {
    let inside = insideOffset;
    let outside = outsideOffset;
    for (let iteration = 0; iteration < EDGE_REFINEMENT_STEPS; iteration += 1) {
      const middle = (inside + outside) / 2;
      const hit = this.surfaceY(pose.x + rightX * middle, pose.z + rightZ * middle) !== null;
      if (hit) inside = middle;
      else outside = middle;
    }
    return (inside + outside) / 2;
  }

  private findHitRuns(samples: boolean[]): HitRun[] {
    const runs: HitRun[] = [];
    let start = -1;
    for (let index = 0; index < samples.length; index += 1) {
      if (samples[index] && start < 0) start = index;
      const endsRun = start >= 0 && (!samples[index] || index === samples.length - 1);
      if (!endsRun) continue;
      runs.push({ firstIndex: start, lastIndex: samples[index] ? index : index - 1 });
      start = -1;
    }
    return runs;
  }

  private closestRun(runs: HitRun[], centerIndex: number): HitRun | null {
    let best: HitRun | null = null;
    let bestDistance = Number.POSITIVE_INFINITY;
    for (const run of runs) {
      const distance = centerIndex < run.firstIndex
        ? run.firstIndex - centerIndex
        : centerIndex - run.lastIndex;
      if (distance < bestDistance) {
        best = run;
        bestDistance = distance;
      }
    }
    return best;
  }

  private emptyClearance(): RoadClearance {
    return {
      onRoad: false,
      leftCenterM: null,
      rightCenterM: null,
      leftEnvelopeGapM: null,
      rightEnvelopeGapM: null,
      roadWidthM: null,
      groundY: null,
      leftEdge: null,
      rightEdge: null,
    };
  }

  private setPathGeometry(path: THREE.Mesh, poses: DiagnosticPose[], lift: number, ribbonWidth: number) {
    const positions: number[] = [];
    const indices: number[] = [];
    let lastY: number | null = null;
    for (const [index, pose] of poses.entries()) {
      const hitY = this.surfaceY(pose.x, pose.z);
      if (hitY !== null) lastY = hitY;
      const rightX = -Math.cos(pose.heading);
      const rightZ = Math.sin(pose.heading);
      const y = (hitY ?? lastY ?? 0) + lift;
      positions.push(
        pose.x - rightX * ribbonWidth / 2, y, pose.z - rightZ * ribbonWidth / 2,
        pose.x + rightX * ribbonWidth / 2, y, pose.z + rightZ * ribbonWidth / 2,
      );
      if (index > 0) {
        const previousLeft = (index - 1) * 2;
        const previousRight = previousLeft + 1;
        const left = index * 2;
        const right = left + 1;
        indices.push(previousLeft, left, previousRight, previousRight, left, right);
      }
    }
    path.geometry.dispose();
    path.geometry = new THREE.BufferGeometry();
    path.geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
    path.geometry.setIndex(indices);
    path.geometry.computeBoundingSphere();
  }

  private updatePoseOverlay(
    footprint: THREE.LineLoop,
    wheels: THREE.Points,
    pose: DiagnosticPose,
    groundY: number | null,
    lift: number,
  ) {
    const y = (groundY ?? 0) + lift;
    const forwardX = Math.sin(pose.heading);
    const forwardZ = Math.cos(pose.heading);
    const rightX = -forwardZ;
    const rightZ = forwardX;
    const halfLength = this.vehicle.length / 2;
    const halfWidth = this.vehicle.width / 2;
    const corners: Array<[number, number]> = [
      [halfLength, -halfWidth],
      [halfLength, halfWidth],
      [-halfLength, halfWidth],
      [-halfLength, -halfWidth],
    ];
    const footprintPositions = corners.flatMap(([forward, right]) => [
      pose.x + forwardX * forward + rightX * right,
      y,
      pose.z + forwardZ * forward + rightZ * right,
    ]);
    this.setDynamicPositions(footprint.geometry, footprintPositions);

    const wheelPositions: number[] = [];
    for (const forward of [this.vehicle.wheelbase / 2, -this.vehicle.wheelbase / 2]) {
      for (const right of [-this.vehicle.track / 2, this.vehicle.track / 2]) {
        wheelPositions.push(
          pose.x + forwardX * forward + rightX * right,
          y + 0.04,
          pose.z + forwardZ * forward + rightZ * right,
        );
      }
    }
    this.setDynamicPositions(wheels.geometry, wheelPositions);
  }

  private updateCrossSection(clearance: RoadClearance) {
    const visible = clearance.onRoad && clearance.leftEdge !== null && clearance.rightEdge !== null;
    this.crossSection.visible = visible;
    this.leftEdgeMarker.visible = visible;
    this.rightEdgeMarker.visible = visible;
    if (!visible || !clearance.leftEdge || !clearance.rightEdge) return;
    this.crossSection.geometry.dispose();
    this.crossSection.geometry = new THREE.BufferGeometry().setFromPoints([
      clearance.leftEdge,
      clearance.rightEdge,
    ]);
    this.leftEdgeMarker.position.copy(clearance.leftEdge);
    this.rightEdgeMarker.position.copy(clearance.rightEdge);
  }

  private setDynamicPositions(geometry: THREE.BufferGeometry, positions: number[]) {
    geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
    geometry.computeBoundingSphere();
  }

  private makeLine(color: number, opacity: number) {
    const line = new THREE.Line(
      new THREE.BufferGeometry(),
      new THREE.LineBasicMaterial({ color, transparent: true, opacity, depthTest: false }),
    );
    line.renderOrder = 80;
    return line;
  }

  private makePath(color: number, opacity: number) {
    const path = new THREE.Mesh(
      new THREE.BufferGeometry(),
      new THREE.MeshBasicMaterial({
        color,
        transparent: true,
        opacity,
        depthTest: false,
        depthWrite: false,
        side: THREE.DoubleSide,
      }),
    );
    path.renderOrder = 100;
    path.frustumCulled = false;
    return path;
  }

  private makeLoop(color: number) {
    const loop = new THREE.LineLoop(
      new THREE.BufferGeometry(),
      new THREE.LineBasicMaterial({ color, depthTest: false }),
    );
    loop.renderOrder = 82;
    return loop;
  }

  private makePoints(color: number) {
    const points = new THREE.Points(
      new THREE.BufferGeometry(),
      new THREE.PointsMaterial({ color, size: 0.42, sizeAttenuation: true, depthTest: false }),
    );
    points.renderOrder = 83;
    return points;
  }

  private makeMarker(color: number) {
    const marker = new THREE.Mesh(
      new THREE.SphereGeometry(0.34, 12, 8),
      new THREE.MeshBasicMaterial({ color, depthTest: false }),
    );
    marker.renderOrder = 84;
    marker.visible = false;
    return marker;
  }
}
