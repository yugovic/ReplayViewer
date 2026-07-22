"""Gemini image-editing toon pilot over the 12-tile ImageGen pilot kit.

Counterpart of stability_toon_pilot.py (see WORKLOG 2026-07-22: Stability
style-transfer failed positional fidelity). Gemini's editing models are the
one approach with an existing position-faithful success (ai_illustrated_tile).

Sends [source tile, style reference, instruction] to generateContent and
saves the returned image. No seed support -> determinism is handled by
gate-then-retry, not by fixed seeds. Manifest records every call.

Usage:
  python gemini_toon_pilot.py run --tiles 09 --model gemini-3.1-flash-image
  python gemini_toon_pilot.py run --tiles all --model gemini-3.1-flash-image
"""

from __future__ import annotations

import argparse
import base64
import json
import time
from pathlib import Path

import requests

API = "https://generativelanguage.googleapis.com/v1beta"
PILOT_DIR = Path("public/data/tracks/fuji/imagegen_trials/pilot")
STYLE_REF = Path("public/data/tracks/fuji/imagegen_trials/ai_illustrated_tile_nologo.png")
OUT_ROOT = PILOT_DIR / "gemini"

PROMPT = (
    "Image 1 is an aerial orthophoto tile of a racing circuit. Image 2 is the "
    "target art style: a flat cel-shaded illustration of another tile of the "
    "same circuit.\n"
    "Redraw image 1 in exactly the art style of image 2 (flat color planes, "
    "vivid green grass, uniform dark asphalt, crisp white lines, red-and-white "
    "striped curbs, soft flat shadows).\n"
    "ABSOLUTE rules — this is a map, so geometry is sacred:\n"
    "- Keep every road, white line, curb, fence, building, bridge, logo and "
    "painted marking at exactly the same position, shape, scale and rotation "
    "as image 1. Do not move anything by even one pixel.\n"
    "- Do not add or remove any object. No vehicles unless they are already "
    "in image 1. Painted sponsor logos stay painted logos.\n"
    "- Do NOT invent anything that is not in image 1: no text, no logos, no "
    "signs, no billboards, no bridges, no structures, and no red-and-white "
    "curbs on edges that have none in image 1. If image 1 shows plain "
    "asphalt, draw plain asphalt.\n"
    "- Same square extent as image 1, output 1024x1024 or larger."
)


def api_key() -> str:
    return (Path.home() / ".gemini_key").read_text(encoding="ascii").strip()


def b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


def call_gemini(key: str, model: str, src: Path, extra: str = "") -> tuple[bytes | None, dict]:
    body = {
        "contents": [{
            "parts": [
                {"inline_data": {"mime_type": "image/png", "data": b64(src)}},
                {"inline_data": {"mime_type": "image/png", "data": b64(STYLE_REF)}},
                {"text": PROMPT + ("\n" + extra if extra else "")},
            ],
        }],
        "generationConfig": {"temperature": 0.0, "responseModalities": ["IMAGE"]},
    }
    t0 = time.time()
    r = requests.post(
        f"{API}/models/{model}:generateContent",
        params={"key": key},
        json=body,
        timeout=300,
    )
    info = {"model": model, "status": r.status_code, "seconds": round(time.time() - t0, 1)}
    if r.status_code != 200:
        info["error"] = r.text[:600]
        return None, info
    try:
        parts = r.json()["candidates"][0]["content"]["parts"]
        img = next(p["inlineData"]["data"] for p in parts if "inlineData" in p)
        info["usage"] = r.json().get("usageMetadata", {})
        return base64.b64decode(img), info
    except (KeyError, StopIteration, IndexError):
        info["error"] = f"no image in response: {json.dumps(r.json())[:400]}"
        return None, info


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run")
    run.add_argument("--tiles", default="09")
    run.add_argument("--model", default="gemini-3.1-flash-image")
    run.add_argument("--max-calls", type=int, default=30)
    run.add_argument("--suffix", default="", help="output filename suffix, e.g. _b")
    run.add_argument("--extra-prompt", default="", help="appended to the standard prompt")
    args = ap.parse_args()

    key = api_key()
    tiles = [f"{i:02d}" for i in range(1, 13)] if args.tiles == "all" else args.tiles.split(",")
    if len(tiles) > args.max_calls:
        raise SystemExit("refusing: too many calls")

    outdir = OUT_ROOT / args.model
    outdir.mkdir(parents=True, exist_ok=True)
    manifest_path = OUT_ROOT / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"calls": []}

    for i, tile in enumerate(tiles):
        src = PILOT_DIR / f"pilot_{tile}_src.png"
        out = outdir / f"pilot_{tile}_gen{args.suffix}.png"
        png, info = call_gemini(key, args.model, src, args.extra_prompt)
        info.update({"tile": tile, "output": str(out)})
        manifest["calls"].append(info)
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        if png is None:
            print(f"[{i+1}/{len(tiles)}] tile {tile} FAILED: {info.get('error')}")
        else:
            out.write_bytes(png)
            print(f"[{i+1}/{len(tiles)}] tile {tile} ok ({info['seconds']}s)")


if __name__ == "__main__":
    main()
