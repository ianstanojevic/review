# Build:  pyinstaller --noconfirm packaging/highlights.spec
# Output: dist/Highlights/Highlights.exe (folder build, like Review). No API key is
# bundled: Highlights reads the same key as Review (Settings key, .env,
# REVIEW_RIOT_API_KEY or DRAFT_RIOT_API_KEY) at runtime.
from PyInstaller.utils.hooks import collect_submodules

ROOT = SPECPATH + "/.."

a = Analysis(
    [SPECPATH + "/highlights_app.py"],
    pathex=[ROOT],
    datas=[(ROOT + "/review/analysis/weights.toml", "review/analysis"),
           (ROOT + "/tests/fixtures/bot.json.gz", "tests/fixtures"),
           (ROOT + "/tests/fixtures/support.json.gz", "tests/fixtures")],
    hiddenimports=collect_submodules("review") + collect_submodules("replay") + collect_submodules("highlights"),
    excludes=["pytest", "matplotlib", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Highlights",
    icon=SPECPATH + "/highlights.ico",
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, upx=False, name="Highlights")
