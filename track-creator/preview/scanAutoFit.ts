export type DiagnosticSide = "left" | "right";

export interface TranslationConstraint {
  id: string;
  heading: number;
  side: DiagnosticSide;
  currentGapM: number;
  targetGapM: number;
  weight?: number;
}

export interface GapRange {
  minM: number;
  maxM: number;
}

export interface LeastSquaresRow {
  ax: number;
  az: number;
  b: number;
  weight?: number;
}

export interface LeastSquaresSolution {
  x: number;
  z: number;
  condition: number;
}

export interface TranslationFitPoint {
  id: string;
  currentGapM: number;
  targetGapM: number;
  predictedGapM: number;
  residualM: number;
}

export type FitConfidence = "HIGH" | "MEDIUM" | "LOW";

export interface TranslationFitResult extends LeastSquaresSolution {
  rmseM: number;
  maxAbsResidualM: number;
  confidence: FitConfidence;
  points: TranslationFitPoint[];
}

const SINGULAR_EPSILON = 1e-8;

export function normalizeGapRange(minM: number, maxM: number): GapRange {
  return minM <= maxM ? { minM, maxM } : { minM: maxM, maxM: minM };
}

export function nearestValueInGapRange(valueM: number, minM: number, maxM: number) {
  const range = normalizeGapRange(minM, maxM);
  return Math.min(range.maxM, Math.max(range.minM, valueM));
}

export function residualToGapRange(valueM: number, minM: number, maxM: number) {
  return valueM - nearestValueInGapRange(valueM, minM, maxM);
}

export function lateralResponse(heading: number, side: DiagnosticSide) {
  const sideSign = side === "left" ? 1 : -1;
  return {
    x: sideSign * -Math.cos(heading),
    z: sideSign * Math.sin(heading),
  };
}

export function solveLeastSquares2D(
  rows: LeastSquaresRow[],
  damping = 0,
): LeastSquaresSolution | null {
  let xx = 0;
  let xz = 0;
  let zz = 0;
  let xb = 0;
  let zb = 0;
  for (const row of rows) {
    const weight = row.weight ?? 1;
    xx += weight * row.ax * row.ax;
    xz += weight * row.ax * row.az;
    zz += weight * row.az * row.az;
    xb += weight * row.ax * row.b;
    zb += weight * row.az * row.b;
  }

  const trace = xx + zz;
  const discriminant = Math.sqrt(Math.max(0, (xx - zz) ** 2 + 4 * xz ** 2));
  const largestEigenvalue = (trace + discriminant) / 2;
  const smallestEigenvalue = (trace - discriminant) / 2;
  if (largestEigenvalue <= SINGULAR_EPSILON || smallestEigenvalue <= SINGULAR_EPSILON) return null;

  const condition = Math.sqrt(largestEigenvalue / smallestEigenvalue);
  const regularizer = damping * Math.max(trace, 1);
  const solveXx = xx + regularizer;
  const solveZz = zz + regularizer;
  const determinant = solveXx * solveZz - xz * xz;
  if (Math.abs(determinant) <= SINGULAR_EPSILON) return null;

  return {
    x: (xb * solveZz - zb * xz) / determinant,
    z: (zb * solveXx - xb * xz) / determinant,
    condition,
  };
}

export function confidenceForFit(condition: number, rmseM: number): FitConfidence {
  if (Number.isFinite(condition) && condition <= 3 && rmseM <= 0.25) return "HIGH";
  if (Number.isFinite(condition) && condition <= 10 && rmseM <= 0.75) return "MEDIUM";
  return "LOW";
}

export function solveTranslationFit(constraints: TranslationConstraint[]): TranslationFitResult | null {
  if (constraints.length < 2) return null;
  const rows = constraints.map((constraint) => {
    const response = lateralResponse(constraint.heading, constraint.side);
    return {
      ax: response.x,
      az: response.z,
      b: constraint.targetGapM - constraint.currentGapM,
      weight: constraint.weight,
    };
  });
  const solution = solveLeastSquares2D(rows, 1e-7);
  if (!solution) return null;

  const points = constraints.map((constraint, index) => {
    const row = rows[index];
    const predictedGapM = constraint.currentGapM + row.ax * solution.x + row.az * solution.z;
    return {
      id: constraint.id,
      currentGapM: constraint.currentGapM,
      targetGapM: constraint.targetGapM,
      predictedGapM,
      residualM: predictedGapM - constraint.targetGapM,
    };
  });
  const totalWeight = constraints.reduce((sum, constraint) => sum + (constraint.weight ?? 1), 0);
  const squaredError = points.reduce(
    (sum, point, index) => sum + (constraints[index].weight ?? 1) * point.residualM ** 2,
    0,
  );
  const rmseM = Math.sqrt(squaredError / Math.max(totalWeight, 1));
  const maxAbsResidualM = Math.max(...points.map((point) => Math.abs(point.residualM)));
  return {
    ...solution,
    rmseM,
    maxAbsResidualM,
    confidence: confidenceForFit(solution.condition, rmseM),
    points,
  };
}
