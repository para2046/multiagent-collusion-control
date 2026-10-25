"""Make the upstream SHADE-Arena checkout importable and set its project root."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHADE = ROOT / "environments" / "shade_arena"


def bootstrap() -> Path:
    if not SHADE.exists():
        raise RuntimeError(f"SHADE-Arena checkout not found at {SHADE}")
    if str(SHADE) not in sys.path:
        sys.path.insert(0, str(SHADE))
    from utils.path_utils import set_project_root
    set_project_root(SHADE)
    return SHADE
