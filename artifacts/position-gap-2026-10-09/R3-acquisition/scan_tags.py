"""R3 forensics (read-only): scan AiM session sibling files (.xrk/.drk/.bak/.gpk/.rrk) for
record tags, sizes and magic bytes. Never writes next to the source media.

Usage:
  set FUJI_WET_XRK / FUJI_DRY_XRK to the .xrk paths (same convention as
  scripts/quality/apex_kpi/paths.py). Siblings are found by replacing the extension.
  python scan_tags.py > tags.json
Output contains file names only (no absolute paths)."""
import hashlib
import json
import os
import re
import sys
from collections import Counter, OrderedDict
from pathlib import Path

EXTS = ['.xrk', '.drk', '.bak', '.gpk', '.rrk']


def sha256(b):
    return hashlib.sha256(b).hexdigest()


def scan_xrk(b):
    # XRK: '<h' + 3/4-char token + ... + '>' headers; footer '<' + token + ... '>'
    toks = Counter()
    for m in re.finditer(rb'<h([A-Za-z0-9+]{3,4})', b):
        t = m.group(1)
        toks[t.decode('latin1').rstrip('\x00')] += 1
    # data messages inside XRK are '(' + channel idx ... ')' for channels and
    # '<hGPS' etc. Count raw ascii markers of interest anywhere.
    interest = {}
    for name in [b'GPS', b'GPS1', b'GNFI', b'RAWX', b'SFRB', b'RXM', b'RAW', b'NAV', b'SBAS', b'GPSR', b'SVIN', b'NSOL', b'PSOL']:
        interest[name.decode()] = len(re.findall(re.escape(b'<h' + name), b))
    return {'header_tokens': dict(toks.most_common()), 'tagged_header_counts': interest}


def scan_tagged(b):
    """.gpk/.rrk style: 4 uppercase ascii tag + uint32 + payload. Find every tag occurrence
    at 4-byte alignment and infer record lengths from successive positions."""
    pos = [(m.start(), m.group(0).decode()) for m in re.finditer(rb'[A-Z]{4}', b) if m.start() % 4 == 0]
    tags = Counter(t for _, t in pos)
    # keep only tags that occur often enough to be record tags or are in the first 64 bytes
    rec_tags = {t for t, c in tags.items() if c >= 3}
    seq = [(p, t) for p, t in pos if t in rec_tags]
    lens = {}
    for (p0, t0), (p1, _t1) in zip(seq, seq[1:]):
        lens.setdefault(t0, Counter())[p1 - p0] += 1
    return {
        'tag_counts': {t: tags[t] for t in sorted(rec_tags, key=lambda x: -tags[x])},
        'record_stride_bytes': {t: dict(c.most_common(4)) for t, c in lens.items()},
        'first_tags': [t for _, t in seq[:12]],
    }


def scan_drk(b):
    strs = re.findall(rb'[ -~]{6,}', b[:4096])
    return {'ascii_strings_first4k': [s.decode('latin1') for s in strs[:20]]}


def main():
    out = OrderedDict()
    for key in ['FUJI_WET_XRK', 'FUJI_DRY_XRK']:
        p = os.environ.get(key)
        if not p:
            continue
        p = Path(p)
        sess = OrderedDict()
        for e in EXTS:
            f = p.with_suffix(e)
            if not f.exists():
                sess[e] = None
                continue
            b = f.read_bytes()
            info = OrderedDict(name=f.name, size=len(b), sha256=sha256(b), magic_hex=b[:16].hex(),
                               magic_ascii=b[:8].decode('latin1').replace('\x00', '.'))
            if e == '.xrk':
                info.update(scan_xrk(b))
            elif e in ('.gpk', '.rrk'):
                info.update(scan_tagged(b))
            else:
                info.update(scan_drk(b))
            sess[e] = info
        dk = p.with_suffix('.drk')
        bk = p.with_suffix('.bak')
        if dk.exists() and bk.exists():
            a, c = dk.read_bytes(), bk.read_bytes()
            diff = [i for i in range(min(len(a), len(c))) if a[i] != c[i]]
            sess['drk_vs_bak'] = dict(identical=(a == c), n_diff_bytes=len(diff),
                                      first_diff_offsets=diff[:20])
        out[key] = sess
    json.dump(out, sys.stdout, indent=1, ensure_ascii=False)


if __name__ == '__main__':
    main()
