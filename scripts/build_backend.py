"""Build the Python desktop service on the target operating system."""

import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
stage = root / "build" / "backend-stage"
subprocess.run(
    [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--name",
        "boss-desktop",
        "--distpath",
        str(stage),
        "--workpath",
        str(root / "build" / "pyinstaller"),
        "--specpath",
        str(root / "build"),
        "--paths",
        str(root / "src"),
        "--exclude-module",
        "PySide6",
        "--exclude-module",
        "pytest",
        str(root / "scripts" / "backend_entry.py"),
    ],
    cwd=root,
    check=True,
)
target = root / "build" / "backend"
if target.exists():
    shutil.rmtree(target)
shutil.copytree(stage / "boss-desktop", target)
(target / "runtime.json").write_text(
    json.dumps(
        {
            "python": platform.python_version(),
            "platform": sys.platform,
            "architecture": platform.machine(),
            "kind": "pyinstaller",
        }
    ),
    encoding="utf-8",
)
print(f"Backend ready: {target}")
