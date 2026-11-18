"""Write packaging/review.ico (REVIEW's radar icon, all Windows sizes).

    python packaging/make_icon.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from review.theme.icon import save_ico  # noqa: E402

if __name__ == "__main__":
    out = Path(__file__).with_name("review.ico")
    save_ico(out)
    print("wrote", out)
