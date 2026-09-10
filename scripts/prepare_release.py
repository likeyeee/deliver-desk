"""Archive the committed source and audit distributables without unpacked build directories."""

import hashlib
import json
import shutil
import struct
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

root = Path(__file__).resolve().parents[1]
release = root / "release"
version = json.loads((root / "package.json").read_text())["version"]
source = release / f"DeliverDesk-{version}-source.zip"
if subprocess.check_output(
    ["git", "status", "--porcelain", "--untracked-files=no"], cwd=root
).strip():
    raise SystemExit("Commit tracked changes before preparing a release.")
release.mkdir(exist_ok=True)
subprocess.run(
    [
        "git",
        "archive",
        "--format=zip",
        f"--prefix=DeliverDesk-{version}/",
        f"--output={source}",
        "HEAD",
    ],
    cwd=root,
    check=True,
)
shutil.copyfile(root / "docs/desktop-guide.md", release / "使用说明.md")
blocked = {
    ".boss-cli",
    ".env",
    "history.db",
    "history.db-shm",
    "history.db-wal",
    "Cookies",
    "Login Data",
    "login.png",
    "desktop.yaml",
    "deepseek-key.enc",
    "resume.json",
    "real-history.csv",
}
audits = []
for archive in sorted(release.glob(f"DeliverDesk-{version}-*.zip")):
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        bad = [name for name in names if blocked.intersection(PurePosixPath(name).parts)]
        if bad:
            raise RuntimeError(f"Private data found in {archive.name}: {bad}")
        record = {"archive": archive.name, "entries": len(names), "privateFiles": 0}
        if archive == source:
            audits.append(record)
            continue
        asars = [name for name in names if name.lower().endswith("resources/app.asar")]
        runtimes = [
            name for name in names if name.lower().endswith("resources/backend/runtime.json")
        ]
        if len(asars) != 1 or len(runtimes) != 1:
            raise RuntimeError(f"Expected one application and runtime in {archive.name}")
        runtime = json.loads(bundle.read(runtimes[0]))
        expected_platform, expected_arch = (
            ("win32", "x86_64") if "-win-" in archive.name else ("darwin", "arm64")
        )
        architecture = {"AMD64": "x86_64", "aarch64": "arm64"}.get(
            runtime["architecture"], runtime["architecture"]
        )
        if (runtime["platform"], architecture) != (expected_platform, expected_arch):
            raise RuntimeError(f"Wrong runtime platform or architecture in {archive.name}")
        prefix = str(PurePosixPath(runtimes[0]).parent)
        if expected_platform == "win32":
            executable = (
                "python.exe" if runtime["kind"] == "cpython-embedded" else "boss-desktop.exe"
            )
            binary = bundle.read(f"{prefix}/{executable}")
            pe = struct.unpack_from("<I", binary, 0x3C)[0]
            if binary[:2] != b"MZ" or struct.unpack_from("<H", binary, pe + 4)[0] != 0x8664:
                raise RuntimeError("Expected a Windows x64 executable")
            if runtime["kind"] == "cpython-embedded":
                for file in (root / "src/boss_cli").glob("*.py"):
                    if file.read_bytes() != bundle.read(
                        f"{prefix}/Lib/site-packages/boss_cli/{file.name}"
                    ):
                        raise RuntimeError(f"Packaged source differs: {file.name}")
        else:
            binary = bundle.read(f"{prefix}/boss-desktop")
            if struct.unpack_from("<II", binary) != (0xFEEDFACF, 0x0100000C):
                raise RuntimeError("Expected a macOS arm64 executable")
        with tempfile.TemporaryDirectory(prefix="deliverdesk-audit-") as directory:
            asar_path = Path(directory) / "app.asar"
            asar_path.write_bytes(bundle.read(asars[0]))
            result = subprocess.check_output(
                [
                    "node",
                    "-e",
                    "const asar=require('@electron/asar');"
                    "const names=asar.listPackage(process.argv[1]);"
                    "const bad=names.filter(n=>n.split('/').some(p=>process.argv.slice(2).includes(p)));"
                    "if(bad.length)throw Error('Private data in application archive');"
                    "process.stdout.write(JSON.stringify({entries:names.length,privateFiles:0}));",
                    str(asar_path),
                    *sorted(blocked),
                ],
                cwd=root,
                text=True,
            )
        record.update({"runtime": runtime, "asar": json.loads(result)})
        audits.append(record)
(release / "package-audit.json").write_text(
    json.dumps(
        {
            "version": version,
            "commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=root, text=True
            ).strip(),
            "archives": audits,
        },
        ensure_ascii=False,
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
hashes = []
for file in sorted(release.glob(f"DeliverDesk-{version}-*")):
    if file.is_file() and file.suffix in {".dmg", ".exe", ".zip"}:
        with file.open("rb") as stream:
            hashes.append(f"{hashlib.file_digest(stream, 'sha256').hexdigest()}  {file.name}")
(release / "SHA256SUMS.txt").write_text("\n".join(hashes) + "\n", encoding="utf-8")
print(f"Prepared committed source, checksums and content audit: {release}")
