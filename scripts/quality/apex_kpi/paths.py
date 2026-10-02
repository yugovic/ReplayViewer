"""Shared paths for the apex/kerb KPI analysis. Local source media are passed by environment variable
(never published): FUJI_WET_XRK, FUJI_DRY_XRK, FUJI_WET_VIDEO, FUJI_DRY_VIDEO. Work files go to APEX_WORK
(default: artifacts/apex-kerb-kpi-2026-10-02/work, git-ignored by convention of not adding it)."""
import os
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
WORK = Path(os.environ.get('APEX_WORK', ROOT / 'artifacts/apex-kerb-kpi-2026-10-02/work'))
WORK.mkdir(parents=True, exist_ok=True)
XRK = {'fuji_aim_01': os.environ.get('FUJI_WET_XRK', ''), 'fuji_aim_2020_07_30': os.environ.get('FUJI_DRY_XRK', '')}
VIDEO = {'fuji_aim_01': os.environ.get('FUJI_WET_VIDEO', ''), 'fuji_aim_2020_07_30': os.environ.get('FUJI_DRY_VIDEO', '')}
