import os
import subprocess
from pathlib import Path

# Find the AIM folder
aim_dir = None
for root, dirs, files in os.walk('d:\\'):
    if 'AIM_2020_07_30_16_38_48' in dirs:
        aim_dir = Path(root) / 'AIM_2020_07_30_16_38_48'
        break

if not aim_dir:
    raise SystemExit('AIM folder not found')

print('AIM dir:', aim_dir)

# Find Osaki 1741 xrk files
xrk_files = sorted(aim_dir.glob('*Osaki*1741*.xrk'))
print('Found xrk files:', xrk_files)

if not xrk_files:
    raise SystemExit('No Osaki 1741 xrk file found')

# Convert all Osaki 1741 files? Or just the first one.
for xrk in xrk_files:
    out_csv = Path('pipeline/cache') / (xrk.stem.replace(' ', '_').lower() + '_converted.csv')
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    print('Converting', xrk, '->', out_csv)
    subprocess.run([
        'python', 'pipeline/convert_aim.py',
        '--input', str(xrk),
        '--out', str(out_csv),
        '--outing', '0',
    ], check=True)
    print('Done:', out_csv)
    print('Lap times:', out_csv.with_name(out_csv.stem + '_laps.csv'))
