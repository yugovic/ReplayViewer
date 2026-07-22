/**
 * 3D overlay for width analysis proposals: renders proposed left/right edge
 * lines coloured by confidence (green/yellow/red), plus the current edges
 * for comparison. Non-destructive — does not touch TrackModel.def.
 */

import * as THREE from "three";
import type { TrackModel } from "./model.ts";
import type { WidthProposal } from "../src/image-analysis/types.ts";
import { classifyConfidence, CONFIDENCE_COLORS } from "../src/image-analysis/confidence.ts";
import type { ConfidenceTier } from "../src/image-analysis/confidence.ts";

const LINE_Y_OFFSET = 0.8;

export class AnalysisOverlay {
  readonly group = new THREE.Group();
  private proposalLeft: THREE.Line | null = null;
  private proposalRight: THREE.Line | null = null;
  private currentLeft: THREE.Line | null = null;
  private currentRight: THREE.Line | null = null;
  private lowConfMarkers: THREE.Points | null = null;

  constructor() {
    this.group.name = "analysis-overlay";
    this.group.visible = false;
  }

  setVisible(v: boolean) {
    this.group.visible = v;
  }

  /**
   * Build overlay geometry from a WidthProposal and the current track state.
   * Each edge line is split into segments by confidence tier so colours match.
   */
  update(model: TrackModel, proposal: WidthProposal, threshold: number) {
    this.clear();
    const { stations, closed, totalLength } = model.track;
    const step = proposal.step;
    const n = proposal.left.length;

    // Build per-vertex positions + colours for proposed edges
    const leftPos: number[] = [];
    const leftCol: number[] = [];
    const rightPos: number[] = [];
    const rightCol: number[] = [];

    // Current edges (thin grey reference lines)
    const curLeftPos: number[] = [];
    const curRightPos: number[] = [];

    const lowConfPoints: number[] = [];

    for (let i = 0; i < n; i++) {
      const dist = Math.min(i * step, totalLength);
      const s = model.sampler(dist);
      const nx = -s.tz;
      const nz = s.tx;

      // Proposed left edge
      const pLeft = proposal.left[i];
      const tierL = classifyConfidence(proposal.confidenceLeft[i], threshold);
      leftPos.push(s.x - nx * pLeft, s.yCenter + LINE_Y_OFFSET, s.z - nz * pLeft);
      pushColor(leftCol, tierL);

      // Proposed right edge
      const pRight = proposal.right[i];
      const tierR = classifyConfidence(proposal.confidenceRight[i], threshold);
      rightPos.push(s.x + nx * pRight, s.yCenter + LINE_Y_OFFSET, s.z + nz * pRight);
      pushColor(rightCol, tierR);

      // Current edges
      curLeftPos.push(s.x - nx * s.offsetLeft, s.yCenter + LINE_Y_OFFSET, s.z - nz * s.offsetLeft);
      curRightPos.push(s.x + nx * s.offsetRight, s.yCenter + LINE_Y_OFFSET, s.z + nz * s.offsetRight);

      // Low confidence markers (both sides)
      if (tierL === "low") {
        lowConfPoints.push(s.x - nx * pLeft, s.yCenter + LINE_Y_OFFSET + 1, s.z - nz * pLeft);
      }
      if (tierR === "low") {
        lowConfPoints.push(s.x + nx * pRight, s.yCenter + LINE_Y_OFFSET + 1, s.z + nz * pRight);
      }
    }

    // Close the loop for closed tracks
    if (closed && n > 0) {
      leftPos.push(leftPos[0], leftPos[1], leftPos[2]);
      rightPos.push(rightPos[0], rightPos[1], rightPos[2]);
      curLeftPos.push(curLeftPos[0], curLeftPos[1], curLeftPos[2]);
      curRightPos.push(curRightPos[0], curRightPos[1], curRightPos[2]);
      // Duplicate first colour for the closing vertex
      pushColor(leftCol, classifyConfidence(proposal.confidenceLeft[0], threshold));
      pushColor(rightCol, classifyConfidence(proposal.confidenceRight[0], threshold));
    }

    this.proposalLeft = makeLine(leftPos, leftCol, 3);
    this.proposalRight = makeLine(rightPos, rightCol, 3);
    this.currentLeft = makeSimpleLine(curLeftPos, 0x445566, 1);
    this.currentRight = makeSimpleLine(curRightPos, 0x445566, 1);

    this.group.add(this.proposalLeft, this.proposalRight, this.currentLeft, this.currentRight);

    if (lowConfPoints.length > 0) {
      const geo = new THREE.BufferGeometry();
      geo.setAttribute("position", new THREE.Float32BufferAttribute(lowConfPoints, 3));
      const mat = new THREE.PointsMaterial({
        color: 0xff4444,
        size: 10,
        sizeAttenuation: false,
        depthTest: false,
      });
      this.lowConfMarkers = new THREE.Points(geo, mat);
      this.lowConfMarkers.renderOrder = 160;
      this.group.add(this.lowConfMarkers);
    }

    this.group.visible = true;
  }

  clear() {
    for (const child of [...this.group.children]) {
      this.group.remove(child);
      if (child instanceof THREE.Line || child instanceof THREE.Points) {
        child.geometry.dispose();
        (child.material as THREE.Material).dispose();
      }
    }
    this.proposalLeft = null;
    this.proposalRight = null;
    this.currentLeft = null;
    this.currentRight = null;
    this.lowConfMarkers = null;
  }
}

function pushColor(arr: number[], tier: ConfidenceTier) {
  const hex = CONFIDENCE_COLORS[tier];
  const r = parseInt(hex.slice(1, 3), 16) / 255;
  const g = parseInt(hex.slice(3, 5), 16) / 255;
  const b = parseInt(hex.slice(5, 7), 16) / 255;
  arr.push(r, g, b);
}

function makeLine(positions: number[], colors: number[], lineWidth: number): THREE.Line {
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  geo.setAttribute("color", new THREE.Float32BufferAttribute(colors, 3));
  const mat = new THREE.LineBasicMaterial({
    vertexColors: true,
    linewidth: lineWidth,
    depthTest: false,
  });
  const line = new THREE.Line(geo, mat);
  line.renderOrder = 155;
  return line;
}

function makeSimpleLine(positions: number[], color: number, lineWidth: number): THREE.Line {
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  const mat = new THREE.LineBasicMaterial({
    color,
    linewidth: lineWidth,
    depthTest: false,
    transparent: true,
    opacity: 0.5,
  });
  const line = new THREE.Line(geo, mat);
  line.renderOrder = 150;
  return line;
}
