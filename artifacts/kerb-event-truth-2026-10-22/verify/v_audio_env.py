"""Verifier step 2b: own audio envelopes for one lap window (no prior offset used anywhere).

Extracts audio stream index 1 (cabin microphone) and 2 by decoding the PCM stream from PTS 0
WITHOUT any seek (stream start_time = 0, start_pts = 0 per ffprobe) and cutting the window by
sample index = PTS * 48000, so no seek semantics are involved.
Computes band energies (20-120 Hz, 120-400 Hz, 400-2000 Hz) on 10 ms hops with a 50 ms
centred boxcar window (mean power). Saved to $VERIFY_WORK/audio_<tag>.npz (envelopes only; no audio stored).

usage: python v_audio_env.py <video> <pts_start> <pts_end> <tag>
"""
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
from scipy.signal import butter, sosfiltfilt

sys.dont_write_bytecode = True
video, p0, p1, tag = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), sys.argv[4]
WORK = Path(os.environ['VERIFY_WORK'])
FS = 48000
out = {}
for idx in (1, 2):
    s0 = max(0.0, p0 - 1.0)
    cmd = ['ffmpeg', '-hide_banner', '-nostdin', '-v', 'error', '-i', video, '-t', f'{p1:.6f}',
           '-map', f'0:{idx}', '-ac', '1', '-f', 's16le', '-acodec', 'pcm_s16le', 'pipe:1']
    raw = subprocess.run(cmd, stdout=subprocess.PIPE, check=True).stdout
    i0 = int(round(s0 * FS))
    x = np.frombuffer(raw, np.int16)[i0:].astype(np.float64) / 32768.0
    t = (i0 + np.arange(len(x))) / FS
    hop = 480
    win = 2400
    env = {}
    for name, lo, hi in (('b20_120', 20, 120), ('b120_400', 120, 400), ('b400_2000', 400, 2000)):
        sos = butter(4, [lo, hi], btype='band', fs=FS, output='sos')
        y = sosfiltfilt(sos, x)
        e = y * y
        c = np.concatenate([[0.0], np.cumsum(e)])
        centres = np.arange(win // 2, len(x) - win // 2, hop)
        env[name] = (c[centres + win // 2] - c[centres - win // 2]) / win
    tc = t[np.arange(win // 2, len(x) - win // 2, hop)]
    out[f's{idx}_t'] = tc
    for k, v in env.items():
        out[f's{idx}_{k}'] = v
    out[f's{idx}_rms'] = float(np.sqrt(np.mean(x * x)))
    print(tag, 'stream', idx, 'samples', len(x), 'rms', out[f's{idx}_rms'])
np.savez_compressed(WORK / f'audio_{tag}.npz', **out)
