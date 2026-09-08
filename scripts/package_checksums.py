"""Write checksums for the distributable installers built on this machine."""

import hashlib
from pathlib import Path

release = Path(__file__).resolve().parents[1] / "release"
files = sorted(
    file
    for file in release.glob("DeliverDesk-*")
    if file.is_file() and file.suffix in {".dmg", ".exe", ".zip"}
)
if not files:
    raise SystemExit("No installers found in release/")
lines = []
for file in files:
    with file.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    lines.append(f"{digest}  {file.name}")
(release / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"Checksummed {len(files)} release files")
