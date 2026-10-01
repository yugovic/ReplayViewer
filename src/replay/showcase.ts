/** A fixed real-log excerpt, shared by playback and the comparison link. */
export const FUJI_SHOWCASE = { start: 47, end: 72, name: "富士スピードウェイ · ヘアピン" } as const;

export type ShowcaseLook = "reference" | "original" | "cg";

export function resolveShowcase(search: string): ShowcaseLook | null {
  const p = new URLSearchParams(search);
  if (p.get("showcase") !== "hairpin" || p.get("track") !== "fuji" || p.get("race") !== "fuji_aim_01") return null;
  return p.get("look") === "cg" ? "cg" : p.get("look") === "original" ? "original" : "reference";
}

export interface PlaybackWindow { start: number; end: number }

export function advanceInWindow(time: number, delta: number, window: PlaybackWindow, loop: boolean) {
  const length = window.end - window.start;
  if (length <= 0) return { time: window.start, playing: false };
  const next = Math.max(time, window.start) + delta;
  if (next < window.end) return { time: next, playing: true };
  return loop
    ? { time: window.start + (next - window.start) % length, playing: true }
    : { time: window.end, playing: false };
}
