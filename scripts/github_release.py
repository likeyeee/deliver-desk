"""Publish audited native build artifacts through the manual GitHub release workflow."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "release"
PROVENANCE = ROOT / "build" / "release-provenance.json"


def gh_json(*args):
    return json.loads(subprocess.check_output(["gh", *args], cwd=ROOT, text=True))


def validate():
    repository = os.environ["GITHUB_REPOSITORY"]
    run_id = os.environ["BUILD_RUN_ID"]
    sha = os.environ["GITHUB_SHA"]
    if not run_id.isdecimal():
        raise SystemExit("Build run ID must be numeric.")
    run = gh_json("api", f"repos/{repository}/actions/runs/{run_id}")
    if (
        run["status"] != "completed"
        or run["conclusion"] != "success"
        or run["path"] != ".github/workflows/desktop-build.yml"
        or run["head_repository"]["full_name"] != repository
        or run["event"] not in {"push", "workflow_dispatch"}
    ):
        raise SystemExit("Expected a successful native build from this repository.")
    # Documentation and test-only commits may follow a build. Every application,
    # dependency and native-build input must still match the verified build.
    subprocess.run(
        [
            "git",
            "diff",
            "--exit-code",
            run["head_sha"],
            "HEAD",
            "--",
            "src",
            "desktop",
            "package.json",
            "package-lock.json",
            "pyproject.toml",
            "uv.lock",
            "vite.config.mjs",
            ".python-version",
            ".nvmrc",
            "scripts/backend_entry.py",
            "scripts/build_backend.py",
            "scripts/build_windows_backend.py",
            "scripts/build-icons.cjs",
            ".github/workflows/desktop-build.yml",
        ],
        cwd=ROOT,
        check=True,
    )
    runs = gh_json(
        "api", f"repos/{repository}/actions/workflows/ci.yml/runs?head_sha={sha}&event=push"
    )["workflow_runs"]
    if not runs or runs[0]["status"] != "completed" or runs[0]["conclusion"] != "success":
        raise SystemExit("Current commit must pass CI before publishing.")
    PROVENANCE.parent.mkdir(exist_ok=True)
    PROVENANCE.write_text(
        json.dumps(
            {
                "sourceCommit": sha,
                "buildCommit": run["head_sha"],
                "buildRun": run["html_url"],
                "ciRun": runs[0]["html_url"],
                "applicationInputsMatch": True,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print("Verified native build, current CI and application source identity.")


def prepare():
    if not PROVENANCE.exists():
        raise SystemExit("Validate the build first.")
    RELEASE.mkdir(exist_ok=True)
    for platform in ("macOS", "Windows"):
        directory = ROOT / "build" / "ci-artifacts" / f"DeliverDesk-{platform}"
        for line in (directory / "SHA256SUMS.txt").read_text().splitlines():
            digest, name = line.split(maxsplit=1)
            if Path(name).name != name or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise SystemExit("Invalid native checksum manifest.")
            artifact = directory / name
            with artifact.open("rb") as stream:
                actual = hashlib.file_digest(stream, "sha256").hexdigest()
            if actual != digest:
                raise SystemExit(f"Native artifact checksum mismatch: {name}")
            shutil.copyfile(artifact, RELEASE / name)
    version = json.loads((ROOT / "package.json").read_text())["version"]
    expected = (
        f"DeliverDesk-{version}-mac-arm64.dmg",
        f"DeliverDesk-{version}-mac-arm64.zip",
        f"DeliverDesk-{version}-win-x64.exe",
        f"DeliverDesk-{version}-win-x64.zip",
    )
    if not all((RELEASE / name).is_file() for name in expected):
        raise SystemExit("Expected both macOS and Windows native installers and ZIPs.")
    subprocess.run([sys.executable, "scripts/prepare_release.py"], cwd=ROOT, check=True)
    shutil.copyfile(PROVENANCE, RELEASE / "build-provenance.json")


def publish():
    provenance = json.loads((RELEASE / "build-provenance.json").read_text())
    version = json.loads((ROOT / "package.json").read_text())["version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise SystemExit("Unsupported release version.")
    tag = f"v{version}"
    # Assets must be reproducible from this commit; never replace a published release.
    existing = subprocess.run(
        ["gh", "release", "view", tag, "--json", "isDraft,targetCommitish"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    if existing.returncode == 0:
        data = json.loads(existing.stdout)
        if not data["isDraft"] or data["targetCommitish"] != provenance["sourceCommit"]:
            raise SystemExit("Release already exists; published versions are not overwritten.")
    else:
        history = (ROOT / "docs/releases/release-notes.md").read_text()
        latest = history.split("### ", 1)[1].split("\n### ", 1)[0].strip()
        notes = RELEASE / "RELEASE_NOTES.md"
        notes.write_text(
            "macOS Apple Silicon 使用 DMG；Windows x64 使用 EXE，或完整解压 ZIP。"
            "安装包自带运行环境，当前为未签名测试版。文件校验见 SHA256SUMS.txt。\n\n"
            + "### "
            + latest
            + "\n\n"
            + f"[原生构建]({provenance['buildRun']}) · [CI]({provenance['ciRun']})\n",
            encoding="utf-8",
        )
        subprocess.run(
            [
                "gh",
                "release",
                "create",
                tag,
                "--draft",
                "--prerelease",
                "--target",
                provenance["sourceCommit"],
                "--title",
                f"DeliverDesk {version} · 桌面测试版",
                "--notes-file",
                str(notes),
            ],
            cwd=ROOT,
            check=True,
        )
    assets = sorted(RELEASE.glob(f"DeliverDesk-{version}-*")) + [
        RELEASE / "SHA256SUMS.txt",
        RELEASE / "package-audit.json",
        RELEASE / "build-provenance.json",
        RELEASE / "使用说明.md",
    ]
    subprocess.run(
        ["gh", "release", "upload", tag, *map(str, assets), "--clobber"], cwd=ROOT, check=True
    )
    subprocess.run(
        ["gh", "release", "edit", tag, "--draft=false", "--prerelease"], cwd=ROOT, check=True
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("validate", "prepare", "publish"))
    {"validate": validate, "prepare": prepare, "publish": publish}[parser.parse_args().phase]()
