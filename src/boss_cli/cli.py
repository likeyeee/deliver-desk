from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

import typer
from filelock import FileLock
from pydantic import ValidationError
from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.text import Text
from yaml import YAMLError

from . import __version__
from .browser import SELECTORS, clean_error
from .config import Config, dump_config, load_config
from .control import is_active
from .models import readable_salary
from .runner import run_task
from .storage import ACTIVE, Store, private_dir

app = typer.Typer(
    help="BOSS 直聘 CLI：搜索、预览、打招呼、任务控制和本地历史。",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)
console = Console(highlight=False)
ConfigPath = Annotated[
    Path, typer.Option("--config", "-c", help="YAML 配置文件；相对路径按当前目录解析")
]


def version_callback(value: bool):
    if value:
        console.print(__version__)
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool, typer.Option("--version", callback=version_callback, is_eager=True)
    ] = False,
):
    pass


def settings(path: Path) -> tuple[Config, Path]:
    try:
        cfg = load_config(path)
        unknown = set(cfg.selectors) - SELECTORS.keys()
        if unknown:
            raise ValueError(f"未知 selectors 名称：{', '.join(sorted(unknown))}")
        return cfg, cfg.directory(path)
    except (OSError, ValueError, ValidationError, YAMLError) as error:
        console.print(f"配置错误：{error}", style="red", markup=False)
        raise typer.Exit(2) from error


@contextmanager
def database(path: Path):
    _, directory = settings(path)
    store = Store(directory)
    try:
        yield store
    finally:
        store.close()


@app.command("init")
def initialize(config: ConfigPath = Path("boss.yaml")):
    """生成 AI 职位示例配置；已有配置不覆盖。"""
    if config.exists():
        console.print(f"配置已存在：{config.resolve()}", markup=False)
        raise typer.Exit(2)
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        "# 先运行 boss login，再运行 boss preview。真正发送使用 boss run --send。\n"
        + dump_config(Config()),
        encoding="utf-8",
    )
    console.print(f"已创建 {config.resolve()}", markup=False)


@app.command("config")
def show_config(config: ConfigPath = Path("boss.yaml")):
    """校验并展示当前完整配置。"""
    cfg, _ = settings(config)
    console.print(dump_config(cfg), markup=False)


def run_foreground(
    config: Path,
    mode: str,
    max_jobs: int | None = None,
    max_sends: int | None = None,
    approved_config: Config | None = None,
):
    cfg = approved_config if approved_config is not None else settings(config)[0]
    directory = cfg.directory(config)
    if max_jobs is not None:
        cfg.search.max_jobs = max_jobs
    if max_sends is not None:
        cfg.run.max_sends = max_sends
    try:
        result = asyncio.run(run_task(cfg, directory, mode=mode, console=console))
    except (ValueError, OSError) as error:
        console.print(clean_error(error), markup=False, style="red")
        raise typer.Exit(2) from error
    console.print(
        f"状态：{result['status']}；浏览 {result['scanned']}，匹配 {result['matched']}，已发送 {result['sent']}，跳过 {result['skipped']}。",
        markup=False,
    )
    if result["status"] in {"failed", "needs_attention"}:
        raise typer.Exit(1)


@app.command()
def login(config: ConfigPath = Path("boss.yaml")):
    """打开独立浏览器完成一次登录，后续复用登录状态。"""
    run_foreground(config, "login")


@app.command()
def preview(
    config: ConfigPath = Path("boss.yaml"),
    max_jobs: Annotated[int | None, typer.Option(min=1, max=2000)] = None,
):
    """搜索并预览匹配职位与文案，不执行任何沟通按钮。"""
    run_foreground(config, "preview", max_jobs=max_jobs)


def send_confirmation(cfg: Config, yes: bool):
    if cfg.message.mode == "ai":
        raise typer.BadParameter("AI 岗位招呼请在桌面端上传并分析简历后运行")
    console.print(
        f"发送范围：{', '.join(cfg.search.keywords)}；{cfg.search.city}；网站筛选 {cfg.search.filters}。",
        markup=False,
    )
    console.print(
        f"本次最多 {cfg.run.max_sends} 个职位，每日本地上限 {cfg.run.daily_limit}；已有/不确定记录会跳过。",
        markup=False,
    )
    console.print(f"本地匹配：{cfg.match.model_dump(exclude_defaults=True)}", markup=False)
    if cfg.platform == "zhaopin":
        console.print(
            "消息：投递智联在线简历并使用网站当前默认招呼。"
            if cfg.message.mode == "platform"
            else f"消息：逐岗保存以下自定义招呼并设为默认，核对后投递智联在线简历；结束后恢复原默认招呼：\n{cfg.message.template}",
            markup=False,
        )
    elif cfg.message.mode == "platform":
        console.print(
            "消息：仅点击立即沟通；平台可能不发送文字，单纯建会话不计入已发送。", markup=False
        )
    else:
        console.print(
            f"消息：建立并核对会话后发送以下文案（平台也可能先自动发送一条招呼语）：\n{cfg.message.template}",
            markup=False,
        )
    if not yes and not typer.confirm("按以上配置开始向招聘方真实发送？", default=False):
        raise typer.Exit()


@app.command("run")
def run_command(
    config: ConfigPath = Path("boss.yaml"),
    send: Annotated[bool, typer.Option(help="开启真实发送；不传此参数等同于 preview")] = False,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="已检查配置，允许本次任务自动发送")
    ] = False,
    max_jobs: Annotated[int | None, typer.Option(min=1, max=2000)] = None,
    max_sends: Annotated[int | None, typer.Option(min=1, max=200)] = None,
):
    """前台运行，实时输出日志；Ctrl+C 可停止。"""
    cfg, _ = settings(config)
    if max_sends is not None:
        cfg.run.max_sends = max_sends
    if yes and not send:
        console.print("--yes 需与 --send 一起使用", markup=False)
        raise typer.Exit(2)
    if send:
        send_confirmation(cfg, yes)
    run_foreground(config, "send" if send else "preview", max_jobs, max_sends, approved_config=cfg)


@app.command()
def start(
    config: ConfigPath = Path("boss.yaml"),
    send: Annotated[bool, typer.Option(help="后台真实发送")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y")] = False,
):
    """后台启动任务；使用 status / logs -f 观察。"""
    cfg, directory = settings(config)
    private_dir(directory)
    if send:
        send_confirmation(cfg, yes)
    elif yes:
        console.print("--yes 需与 --send 一起使用")
        raise typer.Exit(2)
    with FileLock(directory / "launch.lock", timeout=5):
        if is_active(directory):
            console.print("当前已有任务，请先查看 boss status 或使用 boss stop")
            raise typer.Exit(2)
        run_id = uuid.uuid4().hex[:12]
        snapshot_dir = private_dir(directory / "runs")
        snapshot = snapshot_dir / f"{run_id}.yaml"
        cfg.state_dir = str(directory)
        snapshot.write_text(dump_config(cfg), encoding="utf-8")
        snapshot.chmod(0o600)
        log_path = directory / "worker.log"
        with log_path.open("a", encoding="utf-8") as logfile:
            log_path.chmod(0o600)
            command = [
                sys.executable,
                "-m",
                "boss_cli",
                "_worker",
                "--config",
                str(snapshot),
                "--run-id",
                run_id,
            ]
            if send:
                command.append("--send")
            kwargs = (
                {"start_new_session": True}
                if os.name != "nt"
                else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
            )
            proc = subprocess.Popen(
                command, stdin=subprocess.DEVNULL, stdout=logfile, stderr=logfile, **kwargs
            )
        with database(snapshot) as store:
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                row = store.run(run_id)
                if row:
                    console.print(
                        f"任务 {run_id}，PID {proc.pid}，状态 {row['status']}。\nboss status --watch\nboss logs --follow",
                        markup=False,
                    )
                    if row["status"] in {"failed", "needs_attention"}:
                        console.print(row["note"], markup=False)
                        raise typer.Exit(1)
                    return
                if proc.poll() is not None:
                    console.print(f"后台进程启动失败，详情：{log_path}", markup=False)
                    raise typer.Exit(1)
                time.sleep(0.1)
        console.print(
            f"后台进程已派发（PID {proc.pid}），尚未确认启动；请运行 boss status，日志 {log_path}",
            markup=False,
        )
        raise typer.Exit(1)


@app.command("_worker", hidden=True)
def worker(config: ConfigPath, run_id: Annotated[str, typer.Option()], send: bool = False):
    cfg, directory = settings(config)
    try:
        result = asyncio.run(
            run_task(
                cfg, directory, mode="send" if send else "preview", run_id=run_id, console=console
            )
        )
    except Exception as error:
        console.print(clean_error(error), markup=False)
        raise typer.Exit(1) from error
    if result["status"] in {"failed", "needs_attention"}:
        raise typer.Exit(1)


def control_task(config: Path, command: str):
    with database(config) as store:
        row = store.latest_run()
        if not row or row["status"] not in ACTIVE or not is_active(store.directory):
            console.print("没有正在运行的任务。中断/验证后的任务请重新运行，历史会自动防重。")
            raise typer.Exit(2)
        if row["control"] == "stop" and command != "stop":
            console.print("任务正在停止，请等待退出后重新启动。")
            raise typer.Exit(2)
        store.update_run(row["run_id"], control=command)
        store.event(row["run_id"], "INFO", "control", f"请求：{command}")
        console.print(
            f"已请求 {command}；当前浏览器操作结束后生效，延时和休息期间会立即响应。", markup=False
        )


@app.command()
def stop(config: ConfigPath = Path("boss.yaml")):
    """安全停止，保留所有历史和不确定发送记录。"""
    control_task(config, "stop")


@app.command()
def pause(config: ConfigPath = Path("boss.yaml")):
    """暂停当前任务。"""
    control_task(config, "pause")


@app.command()
def resume(config: ConfigPath = Path("boss.yaml")):
    """继续当前已暂停的进程。"""
    control_task(config, "run")


def status_table(store: Store) -> Table:
    table = Table(title="BOSS 任务状态")
    table.add_column("项目")
    table.add_column("值")
    row = store.latest_run()
    if not row:
        table.add_row("状态", "尚无任务")
        return table
    active = is_active(store.directory)
    status = row["status"]
    if status in ACTIVE and not active:
        status = "interrupted（进程已退出，下次启动自动恢复）"
    for label, value in [
        ("任务", row["run_id"]),
        ("模式", row["mode"]),
        ("状态", status),
        ("控制请求", row["control"]),
        ("浏览 / 匹配 / 发送", f"{row['scanned']} / {row['matched']} / {row['sent']}"),
        ("跳过 / 错误", f"{row['skipped']} / {row['errors']}"),
        ("今日发送尝试", store.attempts_today()),
        ("更新时间", row["updated_at"]),
        ("备注", row["note"]),
    ]:
        table.add_row(str(label), str(value))
    return table


@app.command()
def status(
    config: ConfigPath = Path("boss.yaml"),
    watch: Annotated[bool, typer.Option("--watch", "-w")] = False,
):
    """查看状态；--watch 每秒刷新，Ctrl+C 只退出查看。"""
    with database(config) as store:
        if not watch:
            console.print(status_table(store))
            return
        try:
            with Live(status_table(store), console=console, refresh_per_second=1) as live:
                while True:
                    time.sleep(1)
                    live.update(status_table(store))
        except KeyboardInterrupt:
            return


@app.command()
def logs(
    config: ConfigPath = Path("boss.yaml"),
    follow: Annotated[bool, typer.Option("--follow", "-f")] = False,
    limit: Annotated[int, typer.Option(min=1, max=10000)] = 30,
    json_output: Annotated[bool, typer.Option("--json")] = False,
):
    """查看持久化日志；-f 实时追加，退出查看不停止任务。"""
    with database(config) as store:
        rows = store.recent_events(limit)
        cursor = 0
        try:
            while True:
                for row in rows:
                    cursor = row["id"]
                    if json_output:
                        typer.echo(json.dumps(row, ensure_ascii=False))
                    else:
                        console.print(
                            f"{row['time']} [{row['level']}/{row['kind']}] {row['message']}",
                            markup=False,
                        )
                if not follow:
                    break
                time.sleep(0.4)
                rows = store.events(after=cursor)
        except KeyboardInterrupt:
            return


def export_rows(path: Path, rows: list[dict]):
    if path.exists():
        raise ValueError("导出文件已存在，请使用新文件名")
    if path.suffix.lower() == ".json":
        content = json.dumps(rows, ensure_ascii=False, indent=2)
    elif path.suffix.lower() == ".csv":
        stream = io.StringIO()
        fieldnames = list(rows[0]) if rows else ["job_id", "title", "company", "status", "url"]
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            safe = {
                key: "'" + value
                if isinstance(value, str)
                and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r"))
                else value
                for key, value in row.items()
            }
            writer.writerow(safe)
        content = "\ufeff" + stream.getvalue()
    else:
        raise ValueError("导出扩展名必须为 .csv 或 .json")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="") as file:
        file.write(content)
    path.chmod(0o600)


def records_table(rows: list[dict]) -> Table:
    table = Table(title="职位与沟通记录")
    for name in ["职位 ID", "公司 / 职位", "城市 / 薪资", "状态"]:
        table.add_column(name)
    for row in rows:
        table.add_row(
            Text(row["job_id"]),
            Text(f"{row['company']}\n{row['title']}"),
            Text(f"{row.get('location', '')}\n{readable_salary(row.get('salary', ''))}"),
            Text(row.get("status", "已发现")),
        )
    return table


def display_rows(rows: list[dict], export: Path | None, json_output: bool):
    for row in rows:
        row["salary"] = readable_salary(row.get("salary", ""))
    if export:
        try:
            export_rows(export, rows)
        except (OSError, ValueError) as error:
            console.print(str(error), markup=False, style="red")
            raise typer.Exit(2) from error
        console.print(f"已导出 {len(rows)} 条：{export.resolve()}", markup=False)
    elif json_output:
        typer.echo(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        console.print(records_table(rows))
        console.print(f"共 {len(rows)} 条；使用 --json 查看消息、原因和链接。")


@app.command()
def history(
    config: ConfigPath = Path("boss.yaml"),
    state: Annotated[str | None, typer.Option("--status")] = None,
    limit: Annotated[int, typer.Option(min=1, max=100000)] = 100,
    export: Annotated[Path | None, typer.Option(help="导出 .csv 或 .json")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    watch: Annotated[bool, typer.Option("--watch", "-w", help="实时刷新投递记录")] = False,
):
    """投递历史；preview 不会产生“已投递”记录。"""
    if watch and (export or json_output):
        console.print("--watch 不能与 --export / --json 同时使用")
        raise typer.Exit(2)
    with database(config) as store:
        if not watch:
            display_rows(store.history(state, limit), export, json_output)
            return
        try:
            with Live(
                records_table(store.history(state, limit)), console=console, refresh_per_second=1
            ) as live:
                while True:
                    time.sleep(1)
                    live.update(records_table(store.history(state, limit)))
        except KeyboardInterrupt:
            return


@app.command()
def jobs(
    config: ConfigPath = Path("boss.yaml"),
    limit: Annotated[int, typer.Option(min=1, max=100000)] = 100,
    export: Annotated[Path | None, typer.Option()] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
):
    """查看已发现的职位，包括仅预览的结果。"""
    with database(config) as store:
        display_rows(store.jobs(limit), export, json_output)


@app.command()
def resolve(
    job_id: Annotated[str, typer.Argument()],
    result: Annotated[str, typer.Option(help="sent 或 not_sent；not_sent 会允许下次重新发送")],
    note: Annotated[str, typer.Option(help="在网页核对后的说明")],
    config: ConfigPath = Path("boss.yaml"),
):
    """在网站人工核实后处理 unknown / partial / sending 记录。"""
    if not note.strip():
        console.print("请填写核实说明")
        raise typer.Exit(2)
    with database(config) as store:
        if is_active(store.directory):
            console.print("请先停止任务，避免修改正在处理的投递记录")
            raise typer.Exit(2)
        try:
            store.resolve(job_id, result, note)
        except ValueError as error:
            console.print(str(error), markup=False)
            raise typer.Exit(2) from error
    console.print("已记录人工核实结果。")


@app.command()
def diagnose(config: ConfigPath = Path("boss.yaml")):
    """只读检查登录与选择器，生成不含 Cookie 和 URL 查询参数的诊断文件。"""
    run_foreground(config, "diagnose")
