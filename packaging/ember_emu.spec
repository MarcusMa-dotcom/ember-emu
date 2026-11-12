# PyInstaller recipe for the macOS app "Ember Emu.app".
#
#   pyinstaller --noconfirm packaging/ember_emu.spec
#
# Build with a Python that carries Tk and targets an old enough macOS, for
# example a python.org installer or a python-build-standalone build. The app
# runs on the processor type of the Python it was built with.

import os
import sys

from PyInstaller.utils.hooks import collect_data_files

HERE = SPECPATH  # noqa: F821  (set by PyInstaller)
VERSION = "0.1.0"
ICON = os.path.join(HERE, "Ember Emu.icns")

analysis = Analysis(  # noqa: F821
    [os.path.join(HERE, "launcher.py")],
    datas=collect_data_files("emberemu_app"),      # templates and static files
    hiddenimports=["emberemu"],
    # Pure-Python fallback instead of the compiled speedup: one binary less to trust and sign.
    excludes=["markupsafe._speedups"],
)


def without_build_folders(modules):
    """Keep the folders of the build machine out of the app.

    Python notes in a module of its own (_sysconfigdata...) where it was
    installed and how it was built. The packed app never needs these folders,
    but they would name the account and the folders of whoever built it. The
    copy that goes into the app names a neutral folder instead.
    """
    folders = sorted({sys.base_prefix, os.path.realpath(sys.base_prefix), os.path.expanduser("~")},
                     key=len, reverse=True)
    cleaned = []
    for name, path, kind in modules:
        if name.startswith("_sysconfigdata") and path.endswith(".py"):
            with open(path, encoding="utf-8") as source:
                content = source.read()
            for folder in folders:
                content = content.replace(folder, "/install")
            path = os.path.join(workpath, name + ".py")  # noqa: F821  (set by PyInstaller)
            with open(path, "w", encoding="utf-8") as target:
                target.write(content)
        cleaned.append((name, path, kind))
    return cleaned


analysis.pure = without_build_folders(analysis.pure)
archive = PYZ(analysis.pure)  # noqa: F821
executable = EXE(  # noqa: F821
    archive,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="Ember Emu",
    console=False,
)
collected = COLLECT(executable, analysis.binaries, analysis.datas, name="Ember Emu")  # noqa: F821
app = BUNDLE(  # noqa: F821
    collected,
    name="Ember Emu.app",
    icon=ICON if os.path.exists(ICON) else None,
    bundle_identifier="de.marcusmathy.emberemu",
    version=VERSION,
    info_plist={
        "CFBundleDisplayName": "Ember Emu",
        "CFBundleShortVersionString": VERSION,
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "11.0",
        "NSHumanReadableCopyright": "Copyright (c) 2026 Marcus Mathy. MIT License. Use at your own risk.",
        "LSApplicationCategoryType": "public.app-category.utilities",
        "NSLocalNetworkUsageDescription":
            "Ember Emu serves and reads Ember+ trees on your network.",
    },
)
