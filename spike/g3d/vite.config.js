import { defineConfig } from 'vite';
import path from 'node:path';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// The main app's real track data lives outside this spike. We serve it at
// runtime via a tiny dev middleware so the spike reads the SAME files the
// main viewer uses (no copying, no drift). Path is machine-independent.
const tracksDir = path.resolve(__dirname, '../../public/data/tracks');
const racesDir = path.resolve(__dirname, '../../public/data/races');

/** @type {import('vite').Plugin} */
const serveTrackJson = {
  name: 'serve-track-json',
  configureServer(server) {
    server.middlewares.use((req, res, next) => {
      const m = req.url && req.url.match(/^\/track\/([a-z0-9_-]+)\.json(?:\?.*)?$/i);
      if (!m) return next();
      const file = path.join(tracksDir, m[1], 'track.json');
      if (!fs.existsSync(file)) {
        res.statusCode = 404;
        res.end(`track.json not found for "${m[1]}" at ${file}`);
        return;
      }
      res.setHeader('Content-Type', 'application/json');
      res.setHeader('Cache-Control', 'no-cache');
      fs.createReadStream(file).pipe(res);
    });
  },
};

// Serve the main app's per-race lap telemetry (laps.json + *.json) the same
// way as track.json: read in place, no copy, no drift.
/** @type {import('vite').Plugin} */
const serveRaceJson = {
  name: 'serve-race-json',
  configureServer(server) {
    server.middlewares.use((req, res, next) => {
      const m = req.url && req.url.match(/^\/race\/([a-z0-9_-]+)\/([a-z0-9_.-]+\.json)(?:\?.*)?$/i);
      if (!m) return next();
      const file = path.join(racesDir, m[1], m[2]);
      // guard against path traversal: resolved file must stay under racesDir
      if (!path.resolve(file).startsWith(racesDir) || !fs.existsSync(file)) {
        res.statusCode = 404;
        res.end(`race file not found: ${m[1]}/${m[2]}`);
        return;
      }
      res.setHeader('Content-Type', 'application/json');
      res.setHeader('Cache-Control', 'no-cache');
      fs.createReadStream(file).pipe(res);
    });
  },
};

// Local API-key file: the user drops their Google Maps Platform key into
// spike/g3d/.g3d_key (one line, untracked) so it never appears in chat
// history, code, or the URL. Served only to the local dev page.
/** @type {import('vite').Plugin} */
const serveLocalKey = {
  name: 'serve-local-key',
  configureServer(server) {
    server.middlewares.use('/g3d-key', (_req, res) => {
      const file = path.join(__dirname, '.g3d_key');
      const key = fs.existsSync(file) ? fs.readFileSync(file, 'utf8').trim() : '';
      if (key) {
        res.setHeader('Content-Type', 'text/plain');
        res.setHeader('Cache-Control', 'no-store');
        res.end(key);
      } else {
        res.statusCode = 404;
        res.end('');
      }
    });
  },
};

// Dev-only screenshot sink: the automated verifier POSTs a PNG data URL and we
// write it under specs/reports/p5/shots/. Keeps evaluation shots (attribution
// included) out of the browser download flow. Not part of the shipped spike.
const shotsDir = path.resolve(__dirname, '../../specs/reports/p5/shots');
/** @type {import('vite').Plugin} */
const saveShot = {
  name: 'save-shot',
  configureServer(server) {
    server.middlewares.use('/save-shot', (req, res) => {
      if (req.method !== 'POST') { res.statusCode = 405; res.end('POST only'); return; }
      let body = '';
      req.on('data', (c) => { body += c; });
      req.on('end', () => {
        try {
          const { name, dataUrl } = JSON.parse(body);
          const safe = String(name).replace(/[^a-z0-9_.-]/gi, '_');
          const b64 = String(dataUrl).replace(/^data:image\/png;base64,/, '');
          fs.mkdirSync(shotsDir, { recursive: true });
          fs.writeFileSync(path.join(shotsDir, safe), Buffer.from(b64, 'base64'));
          res.setHeader('Content-Type', 'application/json');
          res.end(JSON.stringify({ ok: true, path: path.join(shotsDir, safe) }));
        } catch (e) {
          res.statusCode = 400; res.end(String(e));
        }
      });
    });
  },
};

export default defineConfig({
  plugins: [serveTrackJson, serveRaceJson, serveLocalKey, saveShot],
  // 5299 fixed: the main app owns 5199 and its verify harnesses assume it.
  server: { open: true, port: 5299, strictPort: true },
});
