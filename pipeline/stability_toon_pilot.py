"""Stability AI toon-style pilot over the 12-tile ImageGen pilot kit.

Two systems (see docs/plan-stability-toon-pilot-2026-07-22.md):
  - structure:  v2beta/stable-image/control/structure
                (photo as structural constraint + toon prompt)
  - style:      v2beta/stable-image/control/style-transfer
                (photo as init_image + Gemini toon tile as style_image)

Every call is recorded in an append-only manifest (params, seed, credits
spent from the balance endpoint) for provenance. Hard call limit so a bug
can never burn through the credit balance.

Usage:
  python stability_toon_pilot.py run --tiles 09 --variants structure:0.8 style:0.5
  python stability_toon_pilot.py run --tiles all --variants structure:0.5,0.7,0.9 style:0.3,0.5,0.8
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import requests

API = "https://api.stability.ai"
PILOT_DIR = Path("public/data/tracks/fuji/imagegen_trials/pilot")
STYLE_REF = Path("public/data/tracks/fuji/imagegen_trials/ai_illustrated_tile.png")
OUT_ROOT = PILOT_DIR / "stability"

TOON_PROMPT = (
    "Top-down aerial view converted into a clean cel-shaded illustration. "
    "Flat color planes, vivid green grass, uniform dark gray asphalt, crisp "
    "white painted lines, red-and-white striped curbs kept red-and-white. "
    "Any colored rectangle on the asphalt is a painted sponsor logo: keep it "
    "as flat painted text on the road surface. This is an empty track — there "
    "are absolutely no vehicles; do not draw any car. Keep every road edge, "
    "line, curb and fence in exactly the same position, shape, scale and "
    "rotation as the input; do not add or remove anything."
)
NEGATIVE = (
    "car, vehicle, race car, photo, photorealistic, noise, blur, "
    "added objects, watermark, 3d render, people"
)


def api_key() -> str:
    key = os.environ.get("STABILITY_API_KEY", "").strip()
    if not key:
        keyfile = Path.home() / ".stability_key"
        if keyfile.exists():
            key = keyfile.read_text(encoding="ascii").strip()
    if not key:
        raise SystemExit("no API key: set STABILITY_API_KEY or ~/.stability_key")
    return key


def balance(key: str) -> float:
    r = requests.get(f"{API}/v1/user/balance", headers={"Authorization": f"Bearer {key}"}, timeout=30)
    r.raise_for_status()
    return float(r.json()["credits"])


def call_endpoint(key: str, system: str, strength: float, src: Path, seed: int) -> tuple[bytes | None, dict]:
    """Returns (png bytes or None, info dict). 400-errors are captured into
    info so a wrong parameter name shows the API's own message."""
    headers = {"Authorization": f"Bearer {key}", "Accept": "image/*"}
    data: dict[str, str] = {
        "prompt": TOON_PROMPT,
        "negative_prompt": NEGATIVE,
        "seed": str(seed),
        "output_format": "png",
    }
    files: dict[str, tuple[str, bytes, str]] = {}
    if system == "structure":
        url = f"{API}/v2beta/stable-image/control/structure"
        data["control_strength"] = str(strength)
        files["image"] = (src.name, src.read_bytes(), "image/png")
    elif system == "style":
        url = f"{API}/v2beta/stable-image/control/style-transfer"
        data["style_strength"] = str(strength)
        cf = os.environ.get("STYLE_COMPOSITION_FIDELITY")
        if cf:
            data["composition_fidelity"] = cf
        files["init_image"] = (src.name, src.read_bytes(), "image/png")
        files["style_image"] = (STYLE_REF.name, STYLE_REF.read_bytes(), "image/png")
    else:
        raise ValueError(system)

    t0 = time.time()
    r = requests.post(url, headers=headers, data=data, files=files, timeout=300)
    info = {
        "url": url,
        "status": r.status_code,
        "seconds": round(time.time() - t0, 1),
        "params": {k: v for k, v in data.items() if k not in ("prompt", "negative_prompt")},
        "finish_reason": r.headers.get("finish-reason"),
        "response_seed": r.headers.get("seed"),
    }
    if r.status_code != 200:
        try:
            info["error"] = r.json()
        except Exception:
            info["error"] = r.text[:500]
        return None, info
    return r.content, info


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run")
    run.add_argument("--tiles", default="09", help='"all" or comma list like "01,09,12"')
    run.add_argument("--variants", nargs="+", required=True,
                     help='e.g. structure:0.5,0.7,0.9 style:0.3,0.5,0.8')
    run.add_argument("--seed", type=int, default=42)
    run.add_argument("--max-calls", type=int, default=80)
    run.add_argument("--repeat-tile", default=None,
                     help="tile number to call twice for the determinism check")
    args = ap.parse_args()

    key = api_key()
    tiles = [f"{i:02d}" for i in range(1, 13)] if args.tiles == "all" else args.tiles.split(",")
    jobs: list[tuple[str, float, str]] = []  # (system, strength, tile)
    for spec in args.variants:
        system, strengths = spec.split(":")
        for s in strengths.split(","):
            for t in tiles:
                jobs.append((system, float(s), t))
    if args.repeat_tile:
        for spec in args.variants:
            system, strengths = spec.split(":")
            jobs.append((system, float(strengths.split(",")[0]), args.repeat_tile))
    if len(jobs) > args.max_calls:
        raise SystemExit(f"{len(jobs)} calls > --max-calls {args.max_calls}; refusing")

    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    manifest_path = OUT_ROOT / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"calls": []}

    start_credits = balance(key)
    print(f"balance: {start_credits} credits, running {len(jobs)} calls")
    for i, (system, strength, tile) in enumerate(jobs):
        src = PILOT_DIR / f"pilot_{tile}_src.png"
        outdir = OUT_ROOT / f"{system}_{strength}"
        outdir.mkdir(parents=True, exist_ok=True)
        out = outdir / f"pilot_{tile}_gen.png"
        repeat = out.exists()  # determinism re-run lands beside the first
        if repeat:
            out = outdir / f"pilot_{tile}_gen_repeat.png"
        before = balance(key)
        png, info = call_endpoint(key, system, strength, src, args.seed)
        after = balance(key)
        info.update({"tile": tile, "system": system, "strength": strength,
                     "credits_spent": round(before - after, 2), "output": str(out)})
        manifest["calls"].append(info)
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        if png is None:
            print(f"[{i+1}/{len(jobs)}] {system}:{strength} tile {tile} FAILED: {info.get('error')}")
        else:
            out.write_bytes(png)
            print(f"[{i+1}/{len(jobs)}] {system}:{strength} tile {tile} ok "
                  f"({info['credits_spent']} cr, {info['seconds']}s)")
    print(f"done. spent {round(start_credits - balance(key), 2)} credits total")


if __name__ == "__main__":
    main()
