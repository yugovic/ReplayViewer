"""R3: relation between the XRK GPS-message timecode (logger ms) and the u-blox iTOW (GPS time).
If (timecode - iTOW) is exactly constant, the GPS timecodes are derived from GPS time and carry no
information about the logger's own clock; channels sampled on the logger clock are then tied to GPS
only through an initial offset (no GNFI messages are present in these files).
Env: FUJI_WET_XRK, FUJI_DRY_XRK. Output: gps_timecode_check.json next to this script."""
import json
import os
import re
import struct
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
out = {}
for key in ['FUJI_WET_XRK', 'FUJI_DRY_XRK']:
    p = os.environ.get(key)
    if not p:
        continue
    b = Path(p).read_bytes()
    ts, it = [], []
    for m in re.finditer(rb'<hGPS\x00', b):
        q = m.start()
        ln = struct.unpack_from('<I', b, q + 6)[0]
        pl = b[q + 12:q + 12 + ln]
        for k in range(0, len(pl) - 55, 56):
            t, i = struct.unpack_from('<iI', pl, k)
            ts.append(t)
            it.append(i)
    ts = np.array(ts, float)
    it = np.array(it, float)
    d = ts - it
    A = np.c_[it - it[0], np.ones(len(it))]
    c, *_ = np.linalg.lstsq(A, d, rcond=None)
    r = d - A @ c
    out[key] = dict(file=Path(p).name, n=int(len(ts)), offset_unique_values=int(len(np.unique(d))),
                    drift_ppm=float(c[0] * 1e6), resid_std_ms=float(r.std()),
                    timecode_steps_ms=sorted(set(np.diff(ts).astype(int).tolist()))[:5],
                    gnfi_messages=len(re.findall(rb'<hGNFI', b)))
(HERE / 'gps_timecode_check.json').write_text(json.dumps(out, indent=1), encoding='utf-8')
print(json.dumps(out, indent=1))
