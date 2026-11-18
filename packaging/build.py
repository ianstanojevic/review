"""Build the release: release/Review-v<version>-windows.zip

    python packaging/build.py            # exe + bundled key + zip
    python packaging/build.py --no-key   # without the key (friends paste
                                         # their own in Settings)

1. PyInstaller folder build -> release/Review/Review.exe (separate from
   dist/, your own copy that the Desktop shortcut runs)
2. Builds the REVIEW_RIOT_API_KEY from the project's .env INTO the exe
   (a temporary review/_bundled_key.py, deleted right after the build), so
   friends need no setup at all. Use a non-expiring Riot "Personal API
   Key": a 24-hour development key stops working for everyone after a day.
   (A determined person could still dig the key out of the exe.)
3. Zips the folder -> release/Review-v<version>-windows.zip (unzip anywhere, run Review.exe).
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "release"
APP = DIST / "Review"


_STEPS = """REVIEW – så kommer du igång

1. Packa upp hela mappen "Review" var du vill (t.ex. på skrivbordet).
   Allt i mappen behövs – flytta inte ut bara Review.exe.

2. Dubbelklicka på Review.exe.
   Visar Windows "Windows skyddade datorn": klicka på "Mer information"
   och sedan "Kör ändå". (Programmet är inte signerat, därför varningen.)

3. Skriv ditt Riot ID (namn#tagg, t.ex. Spelare#EUW), välj region och
   tryck Analyze. Första gången hämtas dina matcher – det tar några
   minuter. Sen går det snabbt.
{extra}
Tips: välj "VS Gold/Plat/Emerald/Diamond/Master+" uppe till höger för att
se var du ligger jämfört med spelare på den ranken.
"""
INSTALL_KEY = _STEPS.format(extra="")
INSTALL_NO_KEY = _STEPS.format(extra="""
4. Första gången behövs en Riot API-nyckel: skapa en gratis på
   developer.riotgames.com, klicka på kugghjulet (⚙) uppe till höger i
   REVIEW och klistra in den under RIOT API KEY.
""")


def bundled_key() -> str | None:
    sys.path.insert(0, str(ROOT))
    from review.config import parse_dotenv
    env = ROOT / ".env"
    if not env.exists():
        return None
    return parse_dotenv(env.read_text(encoding="utf-8")).get(
        "REVIEW_RIOT_API_KEY") or None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-key", action="store_true")
    a = ap.parse_args()
    keyfile = ROOT / "review" / "_bundled_key.py"
    key = None if a.no_key else bundled_key()
    if key:
        # stored XOR-masked with a random pad, never as plain text, so the
        # key doesn't show up when someone searches the exe for "RGAPI"
        import base64
        import os as _os
        raw = key.encode()
        pad = _os.urandom(len(raw))
        masked = bytes(a ^ b for a, b in zip(raw, pad))
        keyfile.write_text("P = %r\nM = %r\n" % (
            base64.b85encode(pad).decode(), base64.b85encode(masked).decode()),
            encoding="utf-8")
        print("building key …%s into the exe" % key[-4:])
    elif not a.no_key:
        print("no REVIEW_RIOT_API_KEY in .env — building without a key")
    try:
        r = _pyinstaller()
    finally:
        keyfile.unlink(missing_ok=True)       # never left in the source tree
    if r.returncode:
        print("PyInstaller failed — is Review.exe still running?")
        return r.returncode
    (APP / ".env").unlink(missing_ok=True)    # no key file next to the exe
    (APP / "INSTALLERA.txt").write_text(
        INSTALL_KEY if key else INSTALL_NO_KEY, encoding="utf-8-sig")
    sys.path.insert(0, str(ROOT))
    from review import __version__
    ver = ".".join(__version__.split(".")[:2])          # 1.0.0 -> 1.0
    for old in DIST.glob("Review*.zip"):
        old.unlink()
    zip_path = shutil.make_archive(str(DIST / ("Review-v%s-windows" % ver)),
                                   "zip", DIST, "Review")
    mb = Path(zip_path).stat().st_size / 1e6
    print("wrote %s (%.0f MB)" % (zip_path, mb))
    return 0


def _pyinstaller():
    return subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm",
                        "--log-level", "WARN", "--distpath", str(DIST),
                        "--workpath", str(ROOT / "build" / "release"),
                        str(ROOT / "packaging" / "review.spec")],
                          cwd=ROOT)


if __name__ == "__main__":
    sys.exit(main())
