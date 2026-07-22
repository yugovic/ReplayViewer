"""make_track.py — one-command orchestrator for the automated track pipeline.

  data prep (now)  ->  headless Blender build  ->  place glb  ->  (verify)

Stages:
  1. prep     : run prep_data.py  (Python only; skips itself when outputs are fresh)
  1b. edges   : edges_to_tuned.py — bridge the web editor's road.edges (widths +
                asymmetric centers) into tuned.json; skipped when the editor's
                track.json has no road.edges
  2. build    : blender -b -P build_track_blender.py  (needs Blender; Track Tools optional)
                also saves <kit>/<track>_staging.blend for GUI tuning; GUI tuning
                round-trips via <kit>/tuned.json and is re-applied on rebuilds
  3. verify   : verify_track.js — numeric car-vs-mesh height assertions via
                Playwright (starts the dev server itself when needed)

If Blender isn't found, stage 1 still runs and the exact Blender command is
printed for you to run manually. This lets the pipeline be useful before any
install, and fully automatic once Blender (+ optionally Track Tools) is present.

Usage:
  python make_track.py --track fuji [--blender PATH] [--tracktools TT.blend] \
      [--textures ../textures_cc0] [--force] [--verify] [--race fuji_aim_01] \
      [--dev-url http://localhost:5200]
"""
import argparse, glob, os, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))


def find_blender(explicit):
    if explicit:
        return explicit
    env = os.environ.get("BLENDER")
    if env and os.path.exists(env):
        return env
    candidates = sorted(
        glob.glob(r"C:\Program Files\Blender Foundation\Blender*\blender.exe"),
        reverse=True,  # newest version first
    )
    candidates += [
        "/Applications/Blender.app/Contents/MacOS/Blender",
        shutil.which("blender") or "",
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return None


def run(cmd, **kw):
    print("[run]", " ".join(str(c) for c in cmd))
    return subprocess.run(cmd, check=True, **kw)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--track", required=True)
    ap.add_argument("--blender", default="")
    ap.add_argument("--tracktools", default="")
    ap.add_argument("--textures", default=os.path.join(HERE, "..", "textures_cc0"))
    ap.add_argument("--grid", type=int, default=500)
    ap.add_argument("--force", action="store_true", help="re-run prep even when outputs are fresh")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--race", default="", help="race id for the verify stage (e.g. fuji_aim_01)")
    ap.add_argument("--dev-url", default="http://localhost:5199")  # vite.config strictPort
    args = ap.parse_args()

    kit = os.path.join(REPO, "tracktools-kit", args.track)

    # 1. prep (skips itself when outputs are newer than inputs; tuned.json is
    #    a GUI-written overlay that prep never touches)
    prep_cmd = [sys.executable, os.path.join(HERE, "prep_data.py"),
                "--track", args.track, "--repo", REPO, "--grid", str(args.grid)]
    if args.force:
        prep_cmd.append("--force")
    run(prep_cmd)

    # 1b. web editor edges -> tuned.json (widths + centers). Only when the
    #     editor has authored edges; a failure here must not mask stale data.
    editor_json = os.path.join(REPO, "track-creator", "tracks", args.track, "track.json")
    if os.path.exists(editor_json):
        import json
        has_edges = bool(json.load(open(editor_json, encoding="utf-8"))
                         .get("road", {}).get("edges"))
        if has_edges:
            run([sys.executable, os.path.join(HERE, "edges_to_tuned.py"),
                 "--track", args.track])
        else:
            print("[make_track] editor track.json has no road.edges — tuned.json left as-is")

    # 2. build (Blender)
    blender = find_blender(args.blender)
    build_cmd = [
        blender or "blender", "-b", "-P", os.path.join(HERE, "build_track_blender.py"),
        "--", "--kit", kit, "--repo", REPO, "--textures", args.textures,
    ]
    if args.tracktools:
        build_cmd += ["--tracktools", args.tracktools]
    if not blender:
        print("\n[make_track] Blender not found. Prep done. Run this once Blender is installed:\n  "
              + " ".join(f'"{c}"' if " " in str(c) else str(c) for c in build_cmd) + "\n")
        return
    run(build_cmd)

    # 3. verify (optional): numeric car-vs-mesh assertions; starts the dev
    #    server itself when it isn't already running
    if args.verify:
        verify_cmd = ["node", os.path.join(HERE, "verify_track.js"),
                      "--track", args.track, "--url", args.dev_url]
        if args.race:
            verify_cmd += ["--race", args.race]
        run(verify_cmd)

    print(f"\n[make_track] {args.track} done → public/data/tracks/{args.track}/scene.glb")


if __name__ == "__main__":
    main()
