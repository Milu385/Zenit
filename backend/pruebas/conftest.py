import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "ingesta"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
