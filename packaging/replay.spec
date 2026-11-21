# Build:  pyinstaller --noconfirm packaging/replay.spec
# Output: dist/Replay/Replay.exe (folder build, like Review). No API key is
# bundled: Replay reads the same key as Review (Settings key, .env,
# REVIEW_RIOT_API_KEY or DRAFT_RIOT_API_KEY) at runtime.
from PyInstaller.utils.hooks import collect_submodules

ROOT = SPECPATH + "/.."

a = Analysis(
    [SPECPATH + "/replay_app.py"],
    pathex=[ROOT],
    datas=[(ROOT + "/review/analysis/weights.toml", "review/analysis"),
           (ROOT + "/tests/fixtures/bot.json.gz", "tests/fixtures")],
    hiddenimports=collect_submodules("review") + collect_submodules("replay"),
    excludes=["pytest", "matplotlib", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Replay",
    icon=SPECPATH + "/replay.ico",
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, upx=False, name="Replay")
