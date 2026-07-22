/**
 * Dev server for the two pages and the editor's persistence API:
 *   /preview/  — read-only GLB viewer (out/ is served at /, e.g. /demo.glb)
 *   /editor/   — interactive track editor
 *
 * API (used by the editor):
 *   GET  /api/tracks            → ["demo", "fuji", ...]
 *   GET  /api/track/<name>      → { def, elevation|null, refs|null }
 *   PUT  /api/track/<name>      → body = TrackDef JSON → saves tracks/<name>/track.json
 *   POST /api/build/<name>      → builds out/<name>.glb from the saved files, returns stats
 */

import { defineConfig } from "vite";
import type { Plugin } from "vite";
import type { IncomingMessage, ServerResponse } from "node:http";
import { existsSync, readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { join, resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { buildStations } from "./src/mesh/stations.ts";
import { buildRoadMesh } from "./src/mesh/road.ts";
import { buildCurbsMesh } from "./src/mesh/curbs.ts";
import { writeGlb } from "./src/glb.ts";
import type { GlbMeshSpec } from "./src/glb.ts";
import type { TrackDef, ElevationData } from "./src/types.ts";

const here = dirname(fileURLToPath(import.meta.url));
const TRACKS = resolve(here, "tracks");
const OUT = resolve(here, "out");
const PUBLIC_DATA = resolve(here, "..", "public", "data");
const PUBLIC_ASSETS = resolve(here, "..", "public", "assets");
const NAME_RE = /^[a-zA-Z0-9_-]+$/;

function json(res: ServerResponse, status: number, body: unknown) {
  res.statusCode = status;
  res.setHeader("Content-Type", "application/json");
  res.end(JSON.stringify(body));
}

function readBody(req: IncomingMessage): Promise<string> {
  return new Promise((ok, err) => {
    const chunks: Buffer[] = [];
    req.on("data", (c: Buffer) => chunks.push(c));
    req.on("end", () => ok(Buffer.concat(chunks).toString("utf8")));
    req.on("error", err);
  });
}

function readJsonIf<T>(path: string): T | null {
  return existsSync(path) ? (JSON.parse(readFileSync(path, "utf8")) as T) : null;
}

async function buildTrack(name: string) {
  const dir = join(TRACKS, name);
  const def = readJsonIf<TrackDef>(join(dir, "track.json"));
  const elev = readJsonIf<ElevationData>(join(dir, "elevation.json"));
  if (!def) throw new Error(`track.json not found for "${name}"`);
  if (!elev) throw new Error(`elevation.json not found for "${name}" — run npm run elevation first`);

  const track = buildStations(def, elev);
  const meshes: GlbMeshSpec[] = [
    {
      name: "road",
      data: buildRoadMesh(track),
      material: { baseColor: [0.16, 0.16, 0.17, 1], roughness: 0.95 },
    },
  ];
  const curbs = buildCurbsMesh(track, def.curbs ?? []);
  if (curbs) {
    meshes.push({
      name: "curbs",
      data: curbs,
      material: { baseColor: [1, 1, 1, 1], roughness: 0.7, doubleSided: true },
    });
  }
  const outPath = join(OUT, `${name}.glb`);
  await writeGlb(outPath, meshes);
  return {
    glb: `out/${name}.glb`,
    kb: Math.round(statSync(outPath).size / 1024),
    stations: track.stations.length,
    totalLength: Math.round(track.totalLength),
    roadTris: meshes[0].data.indices.length / 3,
    curbTris: curbs ? curbs.indices.length / 3 : 0,
  };
}

function trackApi(): Plugin {
  return {
    name: "track-api",
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        void (async () => {
          const url = (req.url ?? "").split("?")[0];

          // Serve satellite images from the parent public/data/ directory
          if (req.method === "GET" && url.startsWith("/data/")) {
            const relPath = url.slice("/data/".length);
            const absPath = resolve(PUBLIC_DATA, relPath);
            if (!absPath.startsWith(PUBLIC_DATA) || !existsSync(absPath)) {
              res.statusCode = 404;
              res.end("not found");
              return;
            }
            const ext = absPath.slice(absPath.lastIndexOf(".") + 1);
            const types: Record<string, string> = {
              json: "application/json",
              jpg: "image/jpeg",
              jpeg: "image/jpeg",
              png: "image/png",
            };
            res.setHeader("Content-Type", types[ext] ?? "application/octet-stream");
            res.setHeader("Access-Control-Allow-Origin", "*");
            res.end(readFileSync(absPath));
            return;
          }

          // The replay preview reuses vehicle models from the parent app.
          if (req.method === "GET" && url.startsWith("/assets/")) {
            const relPath = url.slice("/assets/".length);
            const absPath = resolve(PUBLIC_ASSETS, relPath);
            if (!absPath.startsWith(PUBLIC_ASSETS) || !existsSync(absPath)) {
              res.statusCode = 404;
              res.end("not found");
              return;
            }
            res.setHeader("Content-Type", "model/gltf-binary");
            res.setHeader("Access-Control-Allow-Origin", "*");
            res.end(readFileSync(absPath));
            return;
          }

          if (!url.startsWith("/api/")) return next();
          try {
            if (req.method === "GET" && url === "/api/tracks") {
              const names = readdirSync(TRACKS).filter(
                (n) => NAME_RE.test(n) && existsSync(join(TRACKS, n, "track.json")),
              );
              return json(res, 200, names);
            }

            const trackMatch = url.match(/^\/api\/track\/([^/]+)$/);
            if (trackMatch) {
              const name = trackMatch[1];
              if (!NAME_RE.test(name)) return json(res, 400, { error: "bad track name" });
              const dir = join(TRACKS, name);
              if (req.method === "GET") {
                const def = readJsonIf<TrackDef>(join(dir, "track.json"));
                if (!def) return json(res, 404, { error: `unknown track "${name}"` });
                return json(res, 200, {
                  def,
                  elevation: readJsonIf(join(dir, "elevation.json")),
                  refs: readJsonIf(join(dir, "refs.json")),
                });
              }
              if (req.method === "PUT") {
                if (!existsSync(join(dir, "track.json"))) {
                  return json(res, 404, { error: `unknown track "${name}"` });
                }
                const body = await readBody(req);
                const def = JSON.parse(body) as TrackDef; // reject non-JSON before writing
                if (def.name !== name) return json(res, 400, { error: "name mismatch" });
                writeFileSync(join(dir, "track.json"), JSON.stringify(def, null, 1));
                return json(res, 200, { saved: true });
              }
            }

            const buildMatch = url.match(/^\/api\/build\/([^/]+)$/);
            if (buildMatch && req.method === "POST") {
              const name = buildMatch[1];
              if (!NAME_RE.test(name)) return json(res, 400, { error: "bad track name" });
              return json(res, 200, await buildTrack(name));
            }

            return json(res, 404, { error: `no route: ${req.method} ${url}` });
          } catch (e) {
            return json(res, 500, { error: e instanceof Error ? e.message : String(e) });
          }
        })();
      });
    },
  };
}

export default defineConfig({
  publicDir: "out",
  server: { port: 5174 },
  plugins: [trackApi()],
});
