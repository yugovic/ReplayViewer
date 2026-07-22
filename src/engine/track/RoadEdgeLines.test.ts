import { describe, expect, it } from "vitest";
import * as THREE from "three";
import type { TrackData } from "../../replay/types";
import {
  buildRoadEdgeLinesGroup,
  isRoadEdgeProfile,
  sampleRoadEdgeOffsets,
  type RoadEdgeProfile,
} from "./RoadEdgeLines";

const profile: RoadEdgeProfile = {
  version: 1,
  stepMeters: 2,
  totalLength: 8,
  left: [4, 6, 8, 10],
  right: [5, 7, 9, 11],
  interpretation: "track-line-center",
  lineInsetMeters: 0,
};

describe("RoadEdgeLines", () => {
  it("validates and interpolates a closed edge profile", () => {
    expect(isRoadEdgeProfile(profile)).toBe(true);
    expect(sampleRoadEdgeOffsets(profile, 1)).toEqual({ left: 5, right: 6, lineInset: 0 });
    expect(sampleRoadEdgeOffsets(profile, 9)).toEqual({ left: 5, right: 6, lineInset: 0 });
    expect(sampleRoadEdgeOffsets(profile, -1)).toEqual({ left: 7, right: 8, lineInset: 0 });
  });

  it("rejects mismatched or unsafe arrays", () => {
    expect(isRoadEdgeProfile({ ...profile, right: [5] })).toBe(false);
    expect(isRoadEdgeProfile({ ...profile, left: [4, -1, 8, 10] })).toBe(false);
  });

  it("builds two closed, finite ribbons", () => {
    const track: TrackData = {
      version: 1,
      trackId: "test",
      trackName: "test",
      width: 8,
      totalLength: 40,
      origin: { lat: 0, lng: 0, alt: 0 },
      bounds: { lat: [0, 1], lng: [0, 1], x: [0, 10], z: [0, 10] },
      centerline: [
        { lat: 0, lng: 0, alt: 0, dist: 0, x: 0, y: 0, z: 0 },
        { lat: 0, lng: 0, alt: 0, dist: 10, x: 10, y: 0, z: 0 },
        { lat: 0, lng: 0, alt: 0, dist: 20, x: 10, y: 0, z: 10 },
        { lat: 0, lng: 0, alt: 0, dist: 30, x: 0, y: 0, z: 10 },
        { lat: 0, lng: 0, alt: 0, dist: 40, x: 0, y: 0, z: 0 },
      ],
    };
    const squareProfile: RoadEdgeProfile = {
      version: 1,
      stepMeters: 10,
      totalLength: 40,
      left: [4, 4, 4, 4],
      right: [5, 5, 5, 5],
      interpretation: "track-line-center",
    };
    const group = buildRoadEdgeLinesGroup(track, squareProfile, { sampleStep: 5 });
    expect(group.children).toHaveLength(2);
    for (const child of group.children) {
      const position = (child as THREE.Mesh).geometry.getAttribute("position");
      expect(position.count).toBe(16);
      for (let i = 0; i < position.count; i += 1) {
        expect(Number.isFinite(position.getX(i))).toBe(true);
        expect(Number.isFinite(position.getY(i))).toBe(true);
        expect(Number.isFinite(position.getZ(i))).toBe(true);
      }
    }
  });
});
