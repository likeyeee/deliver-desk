import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from boss_cli.cli import app, export_rows
from boss_cli.config import load_config
from boss_cli.storage import Store

runner = CliRunner()


def test_help_and_init(tmp_path):
    assert runner.invoke(app, ["--help"]).exit_code == 0
    path = tmp_path / "config.yaml"
    assert runner.invoke(app, ["init", "--config", str(path)]).exit_code == 0
    assert runner.invoke(app, ["init", "--config", str(path)]).exit_code == 2
    assert runner.invoke(app, ["config", "--config", str(path)]).exit_code == 0
    assert runner.invoke(app, ["status", "--config", str(path)]).exit_code == 0
    assert runner.invoke(app, ["stop", "--config", str(path)]).exit_code == 2


def test_does_not_send_on_no_confirmation(tmp_path):
    path = tmp_path / "config.yaml"
    runner.invoke(app, ["init", "--config", str(path)])
    result = runner.invoke(app, ["run", "--config", str(path), "--send"], input="n\n")
    assert result.exit_code == 0
    assert not (tmp_path / ".boss-cli").exists()


def test_export_preserves_chinese_and_blocks_formula(tmp_path):
    path = tmp_path / "records.csv"
    export_rows(path, [{"title": "=HYPERLINK(1)", "company": "示例公司", "status": "sent"}])
    text = path.read_text(encoding="utf-8-sig")
    assert "'=HYPERLINK(1)" in text
    assert "示例公司" in text
    with pytest.raises(ValueError):
        export_rows(path, [])
    out = tmp_path / "records.json"
    export_rows(out, [{"title": "AI工程师"}])
    assert json.loads(out.read_text())[0]["title"] == "AI工程师"


def test_background_launch_uses_frozen_config_and_correct_mode(tmp_path, monkeypatch):
    config = tmp_path / "config.yaml"
    runner.invoke(app, ["init", "--config", str(config)])
    captured = {}

    class FakeProcess:
        pid = 12345

        def __init__(self, command, **kwargs):
            captured["command"] = command
            snapshot = Path(command[command.index("--config") + 1])
            cfg = load_config(snapshot)
            captured["cfg"] = cfg
            run_id = command[command.index("--run-id") + 1]
            db = Store(cfg.directory(snapshot))
            db.create_run(run_id, "send" if "--send" in command else "preview")
            db.update_run(run_id, status="running")
            db.close()

        def poll(self):
            return None

    monkeypatch.setattr("boss_cli.cli.subprocess.Popen", FakeProcess)
    result = runner.invoke(app, ["start", "--config", str(config), "--send", "--yes"])
    assert result.exit_code == 0, result.output
    assert "_worker" in captured["command"]
    assert "--send" in captured["command"]
    assert captured["cfg"].state_dir == str(tmp_path / ".boss-cli")
