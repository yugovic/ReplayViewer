import os
import json
from pathlib import Path
from libxrk import aim_xrk

# Find the Osaki 1741.xrk file
for root, dirs, files in os.walk('d:\\'):
    if 'AIM_2020_07_30_16_38_48' in dirs:
        target_dir = Path(root) / 'AIM_2020_07_30_16_38_48'
        for f in sorted(target_dir.iterdir()):
            if 'Osaki' in f.name and '1741' in f.name and f.suffix == '.xrk':
                print('FILE:', f)
                log = aim_xrk(str(f))
                print('metadata:', json.dumps(dict(log.metadata), ensure_ascii=False, indent=2))
                print('channels:', list(log.channels.keys()))
                print('laps:', log.laps.to_pandas().to_string())
                break
        break
