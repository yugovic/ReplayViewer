import subprocess
from pathlib import Path

cache_stem = 'osaki_hmr_demio_101_fuji_generic_testing_a_1741_converted'
csv = Path('pipeline/cache') / f'{cache_stem}.csv'
laps_csv = Path('pipeline/cache') / f'{cache_stem}_laps.csv'
out_dir = Path('public/data/races/fuji_aim_2020_07_30')

out_dir.mkdir(parents=True, exist_ok=True)

subprocess.run([
    'python', 'pipeline/build_race.py',
    '--telemetry', str(csv),
    '--lap-times', str(laps_csv),
    '--output-dir', str(out_dir),
    '--race-id', 'fuji_aim_2020_07_30',
    '--track-id', 'fuji',
    '--vehicle-id', 'osaki_hmr_demio_101',
    '--lap-start', '1',
    '--lap-end', '6',
], check=True)
print('Done:', out_dir)
