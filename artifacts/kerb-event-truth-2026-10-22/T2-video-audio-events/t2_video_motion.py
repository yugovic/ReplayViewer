"""T2 step 2: per-frame global image motion of the SmartyCam video (no frames are written anywhere).

Decodes the whole video from the start (CFR 30 fps). Both SmartyCam files begin mid-GOP: the first decodable
output frame has PTS 1.000 s (ffmpeg showinfo; packets 0-0.933 s are not decodable), which is also the source of
the earlier "OpenCV returns 1 s later" note. The first output PTS is probed with showinfo and stored; output frame
i has PTS first_pts + i/30. Crops the
windshield + dashboard area (x 200-1100, y 100-470 of 1280x720), halves it, greyscale, and for each pair of
consecutive frames measures:
  * Farneback dense flow, median over textured pixels, in 4 windshield windows:
      FL/FR = far scene near the horizon (left of the air duct / right side below the mirror),
      RL/RR = road band below the horizon.  -> dx (yaw proxy), dy (pitch / shake proxy)
  * cv2.phaseCorrelate translation of the far windows and of a dashboard window (camera-vs-body motion).
  * Laplacian variance (sharpness) of the far windows (motion blur under vibration).
Output: <T2_WORK>/video/<tag>_motion.npz (numbers only, local work dir).
Usage: python t2_video_motion.py <race> [t_end_pts]
"""
import re
import subprocess
import sys

import cv2
import numpy as np

from t2_common import TAG, VIDEO, WORK

X0, Y0, W, H = 200, 100, 900, 370
w, h = W // 2, H // 2
# windows in half-res crop coordinates: (x0, x1, y0, y1)
WIN = {
    "FL": (25, 130, 15, 65),
    "FR": (230, 430, 30, 67),
    "RL": (25, 130, 67, 97),
    "RR": (230, 430, 67, 97),
}
DASH = (15, 280, 115, 180)


def med_flow(flow, mask):
    if mask.sum() < 20:
        return np.nan, np.nan
    return float(np.median(flow[..., 0][mask])), float(np.median(flow[..., 1][mask]))


def first_output_pts(path):
    r = subprocess.run(["ffmpeg", "-v", "info", "-i", path, "-t", "2", "-map", "0:v:0", "-fps_mode", "passthrough",
                        "-vf", "showinfo", "-f", "null", "-"], capture_output=True, text=True, errors="replace")
    m = re.search(r"n:\s*0\s+pts:\s*\d+\s+pts_time:([0-9.]+)", r.stderr)
    return float(m.group(1))


def main():
    race = sys.argv[1]
    t_end = float(sys.argv[2]) if len(sys.argv) > 2 else None
    tag = TAG[race]
    out_dir = WORK / "video"
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-v", "error", "-i", VIDEO[race]]
    if t_end:
        cmd += ["-t", f"{t_end:.3f}"]
    cmd += ["-map", "0:v:0", "-fps_mode", "passthrough", "-vf", f"crop={W}:{H}:{X0}:{Y0},scale={w}:{h},format=gray",
            "-f", "rawvideo", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=w * h * 64)
    hann = {k: cv2.createHanningWindow((x1 - x0, y1 - y0), cv2.CV_64F) for k, (x0, x1, y0, y1) in WIN.items()}
    hann["D"] = cv2.createHanningWindow((DASH[1] - DASH[0], DASH[3] - DASH[2]), cv2.CV_64F)
    keys = ["FL", "FR", "RL", "RR"]
    rec = {f"{k}_{c}": [] for k in keys for c in ("dx", "dy")}
    for k in ("FL", "FR", "D"):
        for c in ("pcx", "pcy", "pcr"):
            rec[f"{k}_{c}"] = []
    rec["FL_sharp"], rec["FR_sharp"], rec["mean"] = [], [], []
    prev = None
    n = 0
    while True:
        buf = proc.stdout.read(w * h)
        if len(buf) < w * h:
            break
        cur = np.frombuffer(buf, np.uint8).reshape(h, w)
        if prev is not None:
            for k in keys:
                x0, x1, y0, y1 = WIN[k]
                a, b = prev[y0:y1, x0:x1], cur[y0:y1, x0:x1]
                fl = cv2.calcOpticalFlowFarneback(a, b, None, 0.5, 3, 15, 3, 5, 1.1, 0)
                gx = cv2.Sobel(a, cv2.CV_32F, 1, 0, ksize=3)
                gy = cv2.Sobel(a, cv2.CV_32F, 0, 1, ksize=3)
                mag = np.hypot(gx, gy)
                mask = mag > max(20.0, float(np.percentile(mag, 60)))
                dx, dy = med_flow(fl, mask)
                rec[f"{k}_dx"].append(dx)
                rec[f"{k}_dy"].append(dy)
            for k, (x0, x1, y0, y1) in list(WIN.items())[:2] + [("D", DASH)]:
                a = prev[y0:y1, x0:x1].astype(np.float64)
                b = cur[y0:y1, x0:x1].astype(np.float64)
                (sx, sy), r = cv2.phaseCorrelate(a, b, hann[k])
                rec[f"{k}_pcx"].append(sx)
                rec[f"{k}_pcy"].append(sy)
                rec[f"{k}_pcr"].append(r)
            for k in ("FL", "FR"):
                x0, x1, y0, y1 = WIN[k]
                rec[f"{k}_sharp"].append(float(cv2.Laplacian(cur[y0:y1, x0:x1], cv2.CV_32F).var()))
            rec["mean"].append(float(cur.mean()))
        prev = cur
        n += 1
        if n % 3000 == 0:
            print(race, "frames", n, flush=True)
    proc.wait()
    arr = {k: np.asarray(v, np.float32) for k, v in rec.items()}
    # value i describes the motion from output frame i to i+1; time stamp = midpoint PTS first_pts + (i + 0.5)/30
    first = first_output_pts(VIDEO[race])
    arr["first_pts"] = np.float64(first)
    arr["pts_mid"] = first + (np.arange(n - 1) + 0.5) / 30.0
    arr["n_frames"] = np.int64(n)
    np.savez_compressed(out_dir / f"{tag}_motion.npz", **arr)
    print(race, "done", n, "frames")


if __name__ == "__main__":
    main()
