"""fetch_textures.py — explicit opt-in CC0 texture download (ambientCG).

Downloads the PBR maps the Blender driver expects into tracktools-kit/
textures_cc0/ with the exact filenames build_track_blender.py looks for:
    asphalt_albedo.jpg / asphalt_normal.jpg / asphalt_rough.jpg
    grass_albedo.jpg
    concrete_albedo.jpg

Deliberately NOT part of the normal prep/build stages: builds stay
deterministic/offline; network fetching happens only when you run this.
Zips are cached in textures_cc0/.cache/ so re-runs are offline too.

ambientCG assets are CC0 (no attribution required — see
https://docs.ambientcg.com/license/). Change ASSETS to taste.

Usage:  python fetch_textures.py [--res 2K] [--force]
"""
import argparse, os, shutil, sys, urllib.request, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
TEX_DIR = os.path.abspath(os.path.join(HERE, "..", "textures_cc0"))
CACHE = os.path.join(TEX_DIR, ".cache")

# prefix -> (ambientCG asset id, {zip-suffix: output name})
ASSETS = {
    "asphalt": ("Asphalt031", {   # Asphalt025 was delisted (404) 2026-07
        "Color.jpg": "asphalt_albedo.jpg",
        "NormalGL.jpg": "asphalt_normal.jpg",
        "Roughness.jpg": "asphalt_rough.jpg",
    }),
    "grass": ("Grass004", {
        "Color.jpg": "grass_albedo.jpg",
    }),
    "concrete": ("Concrete034", {
        "Color.jpg": "concrete_albedo.jpg",
    }),
}


def fetch(asset_id, res):
    os.makedirs(CACHE, exist_ok=True)
    zip_path = os.path.join(CACHE, f"{asset_id}_{res}-JPG.zip")
    if not os.path.exists(zip_path):
        url = f"https://ambientcg.com/get?file={asset_id}_{res}-JPG.zip"
        print(f"[fetch] {url}")
        req = urllib.request.Request(url, headers={"User-Agent": "ReplayViewer-trackkit/1.0"})
        with urllib.request.urlopen(req, timeout=120) as r, open(zip_path + ".part", "wb") as f:
            shutil.copyfileobj(r, f)
        os.replace(zip_path + ".part", zip_path)
    else:
        print(f"[fetch] cached {os.path.basename(zip_path)}")
    return zip_path


def extract(zip_path, wanted, force):
    got = []
    with zipfile.ZipFile(zip_path) as z:
        for member in z.namelist():
            for suffix, out_name in wanted.items():
                if not member.endswith(suffix):
                    continue
                out = os.path.join(TEX_DIR, out_name)
                if os.path.exists(out) and not force:
                    print(f"[skip]  {out_name} exists")
                else:
                    with z.open(member) as src, open(out, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    print(f"[write] {out_name}  <-  {member}")
                got.append(suffix)
    for suffix in wanted:
        if suffix not in got:
            print(f"[warn]  no *{suffix} in {os.path.basename(zip_path)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--res", default="2K", choices=["1K", "2K", "4K"])
    ap.add_argument("--force", action="store_true", help="overwrite existing files")
    args = ap.parse_args()
    os.makedirs(TEX_DIR, exist_ok=True)
    failed = []
    for prefix, (asset_id, wanted) in ASSETS.items():
        try:
            extract(fetch(asset_id, args.res), wanted, args.force)
        except Exception as exc:
            failed.append(f"{prefix} ({asset_id}): {exc}")
    if failed:
        print("\n[fetch_textures] FAILED:\n  " + "\n  ".join(failed))
        print("Grab replacements by hand from https://ambientcg.com or https://polyhaven.com"
              " and save them under the names listed in this script's header.")
        sys.exit(1)
    print(f"\n[fetch_textures] done -> {TEX_DIR}")


if __name__ == "__main__":
    main()
