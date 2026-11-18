# Build:  pyinstaller --noconfirm packaging/review.spec
# Output: dist/Review/Review.exe (folder build). The API key is NOT bundled; it is read at runtime
# (REVIEW_RIOT_API_KEY / DRAFT_RIOT_API_KEY env, or a .env next to the exe).
from PyInstaller.utils.hooks import collect_submodules

ROOT = SPECPATH + "/.."

a = Analysis(
    [SPECPATH + "/review_app.py"],
    pathex=[ROOT],
    datas=[(ROOT + "/review/analysis/weights.toml", "review/analysis")],
    hiddenimports=collect_submodules("review"),
    excludes=["pytest", "matplotlib", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)
# onedir: no unpacking to %TEMP% on every launch (the one-file build started
# ~5 s slower). Double-click dist/Review/Review.exe (or the Desktop shortcut).
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Review",
    icon=SPECPATH + "/review.ico",
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, upx=False, name="Review")
