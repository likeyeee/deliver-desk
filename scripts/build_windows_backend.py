"""Prepare an offline Windows runtime on any OS, verifying official CPython and locked wheels."""

import hashlib
import json
import shutil
import subprocess
import urllib.request
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
version = "3.14.7"
sha256 = "d297e5ff019966817ad8502465176139f2d3d840fa4ed84b13bed399a6ab1f15"
url = f"https://www.python.org/ftp/python/{version}/python-{version}-embed-amd64.zip"
build = root / "build"
build.mkdir(exist_ok=True)
archive = build / f"python-{version}-embed-amd64.zip"
if not archive.exists():
    print("Downloading the official Windows embedded Python runtime", flush=True)
    urllib.request.urlretrieve(url, archive)
if hashlib.sha256(archive.read_bytes()).hexdigest() != sha256:
    raise RuntimeError("Official Python archive checksum mismatch")
target = build / "windows-backend"
if target.exists():
    shutil.rmtree(target)
with zipfile.ZipFile(archive) as bundle:
    bundle.extractall(target)
requirements = build / "windows-requirements.txt"
subprocess.run(
    [
        "uv",
        "export",
        "--frozen",
        "--no-dev",
        "--no-emit-project",
        "--format",
        "requirements-txt",
        "--output-file",
        str(requirements),
    ],
    check=True,
    cwd=root,
    stdout=subprocess.DEVNULL,
)
subprocess.run(
    [
        "uv",
        "pip",
        "install",
        "--python-platform",
        "x86_64-pc-windows-msvc",
        "--python-version",
        "3.14",
        "--target",
        str(target / "Lib/site-packages"),
        "--only-binary",
        ":all:",
        "--require-hashes",
        "-r",
        str(requirements),
    ],
    check=True,
    cwd=root,
)
shutil.copytree(
    root / "src/boss_cli",
    target / "Lib/site-packages/boss_cli",
    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
)
(target / "python314._pth").write_text(
    "python314.zip\n.\nLib/site-packages\nimport site\n", encoding="utf-8"
)
(target / "runtime.json").write_text(
    json.dumps(
        {
            "python": version,
            "platform": "win32",
            "architecture": "x86_64",
            "kind": "cpython-embedded",
            "sha256": sha256,
        }
    ),
    encoding="utf-8",
)
print(f"Windows runtime prepared: {target}; execution still requires Windows.")
