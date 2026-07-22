import { normalizeGapRange, type GapRange } from "./scanAutoFit.ts";

export type CurbRelation =
  | "unreviewed"
  | "graze"
  | "on-curb"
  | "over-curb"
  | "near"
  | "clear"
  | "custom";

export type ObservationConfidence = "high" | "medium" | "low";

const RELATION_RANGES: Partial<Record<CurbRelation, GapRange>> = {
  graze: { minM: -0.05, maxM: 0.15 },
  "on-curb": { minM: -0.35, maxM: -0.05 },
  "over-curb": { minM: -1, maxM: -0.35 },
  near: { minM: 0.15, maxM: 0.5 },
  clear: { minM: 0.5, maxM: 2 },
};

const CONFIDENCE_WEIGHTS: Record<ObservationConfidence, number> = {
  high: 1,
  medium: 0.5,
  low: 0.2,
};

export function isCurbRelation(value: unknown): value is CurbRelation {
  return value === "unreviewed" || value === "graze" || value === "on-curb" ||
    value === "over-curb" || value === "near" || value === "clear" || value === "custom";
}

export function isObservationConfidence(value: unknown): value is ObservationConfidence {
  return value === "high" || value === "medium" || value === "low";
}

export function presetRangeForRelation(relation: CurbRelation): GapRange | null {
  const range = RELATION_RANGES[relation];
  return range ? { ...range } : null;
}

export function observationWeight(confidence: ObservationConfidence) {
  return CONFIDENCE_WEIGHTS[confidence];
}

export function effectiveMeshGapRange(minM: number, maxM: number, boundaryOffsetM: number) {
  const range = normalizeGapRange(minM, maxM);
  return {
    minM: range.minM + boundaryOffsetM,
    maxM: range.maxM + boundaryOffsetM,
  };
}
