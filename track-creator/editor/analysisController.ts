/**
 * Analysis controller: manages the image-assisted width analysis lifecycle.
 *
 * State machine: idle → loadingImage → ready → analyzing → preview → applying → idle
 *                 ↘ error              ↘ cancelled ↗
 *
 * Non-destructive: TrackModel.def is NOT modified until the user explicitly
 * applies a proposal. Application uses a single snapshot() for one-step undo.
 */

import type { TrackModel } from "./model.ts";
import type { EditorScene } from "./scene.ts";
import { AnalysisOverlay } from "./analysisOverlay.ts";
import { loadGeoImageSource } from "./imageSources.ts";
import type { RasterSource, WidthProposal, WidthAnalysisOptions } from "../src/image-analysis/types.ts";
import { DEFAULT_WIDTH_OPTIONS } from "../src/image-analysis/types.ts";
import { analyzeWidth } from "../src/image-analysis/analyzeWidth.ts";
import { buildAnalysisStationGrid } from "../src/image-analysis/stationGrid.ts";
import {
  blendHighConfidenceOffsets,
  interpolateAcrossLowConfidence,
} from "../src/image-analysis/proposalBlend.ts";
import { centerlineFingerprint } from "../src/image-analysis/coordinates.ts";

export type AnalysisState =
  | "idle"
  | "loadingImage"
  | "ready"
  | "analyzing"
  | "preview"
  | "applying"
  | "error";

export interface AnalysisConfig {
  /** Track directory name (for fetching meta + image). */
  trackName: string;
  /** Satellite meta URL. */
  metaUrl: string;
  /** Satellite image URL. */
  imageUrl: string;
  /** Analysis options (merged with defaults). */
  options?: Partial<WidthAnalysisOptions>;
  /** Confidence threshold for "high confidence". */
  confidenceThreshold: number;
}

export class AnalysisController {
  state: AnalysisState = "idle";
  error: string | null = null;

  readonly overlay: AnalysisOverlay;
  private source: RasterSource | null = null;
  private proposal: WidthProposal | null = null;
  private config: AnalysisConfig | null = null;
  private centerlineHash: string = "";
  private abortAnalysis = false;

  constructor(
    private model: TrackModel,
    private scene: EditorScene,
  ) {
    this.overlay = new AnalysisOverlay();
    this.scene.scene.add(this.overlay.group);
  }

  /**
   * Step 1: Load the georeferenced image and prepare the RasterSource.
   */
  async loadImage(config: AnalysisConfig): Promise<void> {
    this.state = "loadingImage";
    this.error = null;

    try {
      if (!this.model.def.origin) {
        throw new Error("track.json に origin (lat/lng) がありません");
      }

      const { source } = await loadGeoImageSource(
        config.metaUrl,
        config.imageUrl,
        this.model.def.origin,
        `geo-${config.trackName}`,
      );

      this.source = source;
      this.config = config;
      this.centerlineHash = centerlineFingerprint(this.model.def.controlPoints);
      this.state = "ready";
    } catch (e) {
      this.state = "error";
      this.error = e instanceof Error ? e.message : String(e);
    }
  }

  /**
   * Step 2: Run width analysis and enter preview state.
   */
  async runAnalysis(): Promise<void> {
    if (!this.source || !this.config) {
      this.state = "error";
      this.error = "画像が読み込まれていません";
      return;
    }

    // Verify centerline hasn't changed since image load
    const currentHash = centerlineFingerprint(this.model.def.controlPoints);
    if (currentHash !== this.centerlineHash) {
      this.state = "error";
      this.error = "中心線が変更されたため画像を再読み込みしてください";
      return;
    }

    this.state = "analyzing";
    this.abortAnalysis = false;

    try {
      const options: WidthAnalysisOptions = {
        ...DEFAULT_WIDTH_OPTIONS,
        ...this.config.options,
        corridors: this.model.def.analysisHints?.corridors,
        objective: this.model.def.analysisHints?.objective ?? "boundary",
      };
      // The proposal's arrays must be sampled at proposal.step, independently
      // of the road mesh's own station spacing. Otherwise applying index i at
      // i * proposal.step shifts observations to the wrong part of the lap.
      const { stations, currentLeft, currentRight } = buildAnalysisStationGrid(
        this.model.track.totalLength,
        this.model.track.closed,
        options.step,
        this.model.sampler,
      );

      // Run analysis (yield to UI between chunks for responsiveness)
      const chunkSize = 50;
      let result: WidthProposal | null = null;

      for (let start = 0; start < stations.length; start += chunkSize) {
        if (this.abortAnalysis) {
          this.state = "idle";
          return;
        }
        // Process in chunks — yield to event loop
        await new Promise((r) => setTimeout(r, 0));
      }

      // Run the full analysis (synchronous but fast for typical tracks)
      result = analyzeWidth({
        source: this.source,
        stations,
        closed: this.model.track.closed,
        options,
        currentLeft,
        currentRight,
      });

      this.proposal = result;
      this.overlay.update(this.model, result, this.config.confidenceThreshold);
      this.state = "preview";
    } catch (e) {
      this.state = "error";
      this.error = e instanceof Error ? e.message : String(e);
    }
  }

  /**
   * Cancel an in-progress analysis.
   */
  cancel() {
    this.abortAnalysis = true;
    this.state = "idle";
    this.overlay.clear();
    this.proposal = null;
  }

  /**
   * Discard the current proposal and return to ready state.
   */
  discard() {
    this.proposal = null;
    this.overlay.clear();
    this.state = this.source ? "ready" : "idle";
  }

  /**
   * Step 3: Apply the proposal to the TrackModel.
   * Uses a single snapshot() so one undo reverts everything.
   *
   * Modes:
   *   "highOnly"   — only apply high-confidence offsets; keep current for others
   *   "all"        — apply all proposed offsets
   *   "range"      — apply only within a dist range [rangeStart, rangeEnd]
   */
  applyProposal(
    mode: "highOnly" | "all" | "range",
    rangeStart?: number,
    rangeEnd?: number,
  ): { applied: number; skipped: number; warnings: string[] } {
    if (!this.proposal || !this.config) {
      return { applied: 0, skipped: 0, warnings: ["提案がありません"] };
    }

    // Verify centerline hasn't changed
    const currentHash = centerlineFingerprint(this.model.def.controlPoints);
    if (currentHash !== this.centerlineHash) {
      return { applied: 0, skipped: 0, warnings: ["中心線が変更されたため適用できません"] };
    }

    const warnings: string[] = [];
    const proposal = this.proposal;
    const threshold = this.config.confidenceThreshold;
    const step = proposal.step;
    const total = this.model.track.totalLength;
    const closed = this.model.track.closed;

    // Determine sample count for the edge arrays
    const n = closed ? Math.ceil(total / step) : Math.floor(total / step) + 1;

    // Get current offsets
    const curLeft: number[] = new Array(n);
    const curRight: number[] = new Array(n);
    for (let i = 0; i < n; i++) {
      const dist = Math.min(i * step, total);
      const s = this.model.sampler(dist);
      curLeft[i] = Math.round(s.offsetLeft * 100) / 100;
      curRight[i] = Math.round(s.offsetRight * 100) / 100;
    }

    // Build new edge arrays
    const newLeft = curLeft.slice();
    const newRight = curRight.slice();
    let applied = 0;
    let skipped = 0;

    if (mode === "highOnly") {
      const count = Math.min(
        proposal.left.length,
        proposal.right.length,
        proposal.confidenceLeft.length,
        proposal.confidenceRight.length,
        n,
      );
      // Low-confidence spans bridge between accepted neighbours instead of
      // reverting to the pre-analysis placeholder (a uniform-width band would
      // otherwise cut abruptly through measured spans).
      const fallbackLeft = interpolateAcrossLowConfidence(
        proposal.left.slice(0, count),
        proposal.confidenceLeft.slice(0, count),
        threshold,
        closed,
      );
      const fallbackRight = interpolateAcrossLowConfidence(
        proposal.right.slice(0, count),
        proposal.confidenceRight.slice(0, count),
        threshold,
        closed,
      );
      const blendedLeft = blendHighConfidenceOffsets(
        fallbackLeft,
        proposal.left.slice(0, count),
        proposal.confidenceLeft.slice(0, count),
        { threshold, step, closed },
      );
      const blendedRight = blendHighConfidenceOffsets(
        fallbackRight,
        proposal.right.slice(0, count),
        proposal.confidenceRight.slice(0, count),
        { threshold, step, closed },
      );
      for (let i = 0; i < count; i++) {
        newLeft[i] = Math.round(blendedLeft[i] * 100) / 100;
        newRight[i] = Math.round(blendedRight[i] * 100) / 100;
        if (proposal.confidenceLeft[i] >= threshold) applied++;
        else skipped++;
        if (proposal.confidenceRight[i] >= threshold) applied++;
        else skipped++;
      }
    }

    for (let i = 0; i < Math.min(proposal.left.length, n); i++) {
      const dist = i * step;
      const inRange =
        mode !== "range" ||
        (rangeStart !== undefined && rangeEnd !== undefined &&
          dist >= rangeStart && dist <= rangeEnd);

      if (!inRange) {
        skipped++;
        continue;
      }

      const confL = proposal.confidenceLeft[i];
      const confR = proposal.confidenceRight[i];

      if (mode !== "highOnly") {
        newLeft[i] = Math.round(proposal.left[i] * 100) / 100;
        newRight[i] = Math.round(proposal.right[i] * 100) / 100;
        applied += 2;
      }
    }

    // Validate
    for (let i = 0; i < n; i++) {
      if (!Number.isFinite(newLeft[i]) || newLeft[i] < 0) {
        warnings.push(`左端[${i}]が不正 (${newLeft[i]}) — 現在値を維持`);
        newLeft[i] = curLeft[i];
      }
      if (!Number.isFinite(newRight[i]) || newRight[i] < 0) {
        warnings.push(`右端[${i}]が不正 (${newRight[i]}) — 現在値を維持`);
        newRight[i] = curRight[i];
      }
    }

    // Check elevation corridor overflow
    const corridor = this.model.elev.corridorHalfWidth;
    let maxOffset = 0;
    for (let i = 0; i < n; i++) {
      maxOffset = Math.max(maxOffset, newLeft[i], newRight[i]);
    }
    if (maxOffset > corridor) {
      warnings.push(
        `標高コリドー幅 (${corridor.toFixed(1)} m) を超過 — 端標高が外挿されます (最大 ${maxOffset.toFixed(1)} m)`,
      );
    }

    // Persist per-sample confidence so later passes (re-analysis, runoff
    // layer) can tell measured spans from interpolated/unknown ones.
    const round2 = (v: number) => Math.round(v * 100) / 100;
    const confLeft = new Array<number>(n);
    const confRight = new Array<number>(n);
    for (let i = 0; i < n; i++) {
      confLeft[i] = round2(proposal.confidenceLeft[i] ?? 0);
      confRight[i] = round2(proposal.confidenceRight[i] ?? 0);
    }

    // Single snapshot for one-step undo
    this.state = "applying";
    this.model.snapshot();
    this.model.def.road.edges = {
      step,
      left: newLeft,
      right: newRight,
      confidenceLeft: confLeft,
      confidenceRight: confRight,
    };
    this.model.rebuild();

    // Clear proposal (applied)
    this.overlay.clear();
    this.proposal = null;
    this.state = "idle";

    return { applied, skipped, warnings };
  }

  /**
   * Get the current proposal (for UI display).
   */
  getProposal(): WidthProposal | null {
    return this.proposal;
  }

  /**
   * Get the loaded source resolution.
   */
  getResolution(): number | null {
    return this.source?.resolutionMetersPerPixel ?? null;
  }
}
