"""Prepare distributable source, checksums, and a package-content audit."""

import hashlib
import json
import shutil
import struct
import subprocess
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
release = root / "release"
version = json.loads((root / "package.json").read_text())["version"]
source = release / f"DeliverDesk-{version}-source.zip"
selected = [
    "src",
    "tests",
    "desktop",
    "scripts",
    "docs",
    ".github",
    "examples",
    "pyproject.toml",
    "uv.lock",
    "package.json",
    "package-lock.json",
    "README.md",
    ".gitignore",
    ".gitattributes",
    ".editorconfig",
    ".prettierignore",
    ".nvmrc",
    ".python-version",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "vite.config.mjs",
]
if (root / "LICENSE").exists():
    selected.append("LICENSE")
release.mkdir(exist_ok=True)
with zipfile.ZipFile(source, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
    for name in selected:
        entry = root / name
        for file in sorted(entry.rglob("*")) if entry.is_dir() else [entry]:
            if file.is_file() and "__pycache__" not in file.parts and file.suffix != ".pyc":
                bundle.write(file, Path(f"DeliverDesk-{version}") / file.relative_to(root))
shutil.copyfile(root / "docs/desktop-guide.md", release / "使用说明.md")
blocked = {".boss-cli", "history.db", "Cookies", "login.png", "desktop.yaml", "real-history.csv"}
audits = []
for archive in release.glob(f"DeliverDesk-{version}-*.zip"):
    with zipfile.ZipFile(archive) as bundle:
        bad = [name for name in bundle.namelist() if blocked.intersection(Path(name).parts)]
        if bad:
            raise RuntimeError(f"Private data found in {archive.name}: {bad}")
        audits.append(
            {"archive": archive.name, "entries": len(bundle.namelist()), "privateFiles": 0}
        )
asar_audits = []
for archive in (
    release / "mac-arm64/投递工作台.app/Contents/Resources/app.asar",
    release / "win-unpacked/resources/app.asar",
):
    result = subprocess.check_output(
        [
            "node",
            "-e",
            "const asar=require('@electron/asar');const names=asar.listPackage(process.argv[1]);"
            "const bad=names.filter(n=>n.split('/').some(p=>process.argv.slice(2).includes(p)));"
            "if(bad.length)throw Error('Private application data found in asar');"
            "process.stdout.write(JSON.stringify({entries:names.length,privateFiles:0}));",
            str(archive),
            *sorted(blocked),
        ],
        cwd=root,
        text=True,
    )
    asar_audits.append({"archive": str(archive.relative_to(release)), **json.loads(result)})
backend = release / "win-unpacked/resources/backend"
runtime = json.loads((backend / "runtime.json").read_text())
assert runtime["platform"] == "win32" and runtime["architecture"] == "x86_64"
for file in (root / "src/boss_cli").glob("*.py"):
    assert file.read_bytes() == (backend / "Lib/site-packages/boss_cli" / file.name).read_bytes(), (
        file.name
    )
binary = (backend / "python.exe").read_bytes()
pe = struct.unpack_from("<I", binary, 0x3C)[0]
assert struct.unpack_from("<H", binary, pe + 4)[0] == 0x8664
(release / "package-audit.json").write_text(
    json.dumps(
        {
            "version": version,
            "archives": audits,
            "asarArchives": asar_audits,
            "windowsBackend": runtime,
            "windowsSourceMatches": True,
            "windowsRuntimeExecuted": False,
        },
        ensure_ascii=False,
        indent=2,
    ),
    encoding="utf-8",
)
hashes = []
for file in sorted(release.glob(f"DeliverDesk-{version}-*")):
    if file.suffix in {".dmg", ".exe", ".zip"}:
        hashes.append(f"{hashlib.file_digest(file.open('rb'), 'sha256').hexdigest()}  {file.name}")
(release / "SHA256SUMS.txt").write_text("\n".join(hashes) + "\n", encoding="utf-8")
print(f"Prepared source, checksums and content audit: {release}")
