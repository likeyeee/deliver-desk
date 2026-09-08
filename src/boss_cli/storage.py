from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from .models import Job

ACTIVE = ("starting", "running", "paused", "stopping")
DELIVERY_STATES = {"sending", "sent", "unknown", "partial", "contacted", "not_sent"}


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def day() -> str:
    return datetime.now().astimezone().date().isoformat()


def private_dir(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    return directory


class Store:
    def __init__(self, directory: Path):
        self.directory = private_dir(directory)
        db_path = directory / "history.db"
        self.db = sqlite3.connect(db_path, timeout=10, isolation_level=None)
        db_path.chmod(0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (
          job_id TEXT PRIMARY KEY, title TEXT NOT NULL, company TEXT NOT NULL,
          url TEXT NOT NULL, location TEXT NOT NULL, salary TEXT NOT NULL,
          data TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS runs (
          run_id TEXT PRIMARY KEY, status TEXT NOT NULL, mode TEXT NOT NULL,
          pid INTEGER, started_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          control TEXT NOT NULL DEFAULT 'run', scanned INTEGER NOT NULL DEFAULT 0,
          matched INTEGER NOT NULL DEFAULT 0, sent INTEGER NOT NULL DEFAULT 0,
          skipped INTEGER NOT NULL DEFAULT 0, errors INTEGER NOT NULL DEFAULT 0,
          note TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS deliveries (
          job_id TEXT PRIMARY KEY REFERENCES jobs(job_id), run_id TEXT REFERENCES runs(run_id),
          status TEXT NOT NULL, message TEXT NOT NULL, note TEXT NOT NULL,
          attempted_day TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY AUTOINCREMENT, time TEXT NOT NULL,
          run_id TEXT, level TEXT NOT NULL, kind TEXT NOT NULL,
          job_id TEXT, message TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS events_run ON events(run_id, id);
        CREATE INDEX IF NOT EXISTS deliveries_day ON deliveries(attempted_day);
        """)
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(runs)")}
        for name in ("target", "attempts"):
            if name not in columns:
                self.db.execute(f"ALTER TABLE runs ADD COLUMN {name} INTEGER NOT NULL DEFAULT 0")
        self.db.execute("PRAGMA user_version=2")

    def close(self):
        self.db.close()

    def save_job(self, job: Job):
        self.db.execute(
            """INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)
          ON CONFLICT(job_id) DO UPDATE SET title=excluded.title,company=excluded.company,
          url=excluded.url,location=excluded.location,salary=excluded.salary,
          data=excluded.data,updated_at=excluded.updated_at""",
            (
                job.job_id,
                job.title,
                job.company,
                job.url,
                job.location,
                job.salary,
                json.dumps(job.as_dict(), ensure_ascii=False),
                now(),
            ),
        )

    def create_run(self, run_id: str, mode: str):
        self.db.execute(
            "INSERT INTO runs(run_id,status,mode,pid,started_at,updated_at) VALUES(?,?,?,?,?,?)",
            (run_id, "starting", mode, os.getpid(), now(), now()),
        )

    def update_run(self, run_id: str, **values):
        allowed = {
            "status",
            "pid",
            "control",
            "scanned",
            "matched",
            "sent",
            "skipped",
            "errors",
            "note",
            "target",
            "attempts",
        }
        if not values or set(values) - allowed:
            raise ValueError("非法任务字段")
        values["updated_at"] = now()
        assignments = ",".join(f"{key}=?" for key in values)
        self.db.execute(f"UPDATE runs SET {assignments} WHERE run_id=?", (*values.values(), run_id))

    def run(self, run_id: str) -> dict | None:
        row = self.db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def latest_run(self) -> dict | None:
        row = self.db.execute("SELECT * FROM runs ORDER BY rowid DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def control(self, run_id: str) -> str:
        row = self.db.execute("SELECT control FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return row[0] if row else "stop"

    def recover(self):
        """Called only under the exclusive worker lock. Never retry an ambiguous submission."""
        self.db.execute(
            "UPDATE deliveries SET status='unknown',note=?,updated_at=? WHERE status='sending'",
            ("上次任务在发送期间退出，请到网站核对；不会自动重发", now()),
        )
        self.db.execute(
            "UPDATE runs SET status='interrupted',updated_at=?,note=? WHERE status IN ('starting','running','paused','stopping')",
            (now(), "上次进程已退出"),
        )

    def blocked(self, job_id: str) -> str | None:
        row = self.db.execute("SELECT status FROM deliveries WHERE job_id=?", (job_id,)).fetchone()
        return row[0] if row and row[0] != "not_sent" else None

    def pending_message(self, job_id: str) -> tuple[Job, str]:
        row = self.db.execute(
            "SELECT j.data,d.message,d.status FROM deliveries d JOIN jobs j USING(job_id) WHERE job_id=?",
            (job_id,),
        ).fetchone()
        if not row or row["status"] not in {"partial", "unknown"}:
            raise ValueError("只能核对待处理的历史消息")
        return Job(**json.loads(row["data"])), row["message"]

    def attempts_today(self) -> int:
        # Counting reservations, including unknown outcomes, is deliberately conservative.
        return self.db.execute(
            "SELECT COUNT(*) FROM events WHERE kind='send_reserved' AND substr(time,1,10)=?",
            (day(),),
        ).fetchone()[0]

    def reserve(self, job: Job, run_id: str, message: str, limit: int) -> str | None:
        """Atomic dedup + daily quota + write-ahead record before the first external click."""
        self.db.execute("BEGIN IMMEDIATE")
        try:
            previous = self.blocked(job.job_id)
            if previous:
                self.db.execute("ROLLBACK")
                return f"历史状态：{previous}"
            if self.attempts_today() >= limit:
                self.db.execute("ROLLBACK")
                return "今日发送上限"
            self.db.execute(
                """INSERT INTO deliveries VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(job_id) DO UPDATE SET run_id=excluded.run_id,status=excluded.status,
                message=excluded.message,note=excluded.note,attempted_day=excluded.attempted_day,
                updated_at=excluded.updated_at""",
                (job.job_id, run_id, "sending", message, "发送前已登记", day(), now(), now()),
            )
            self.db.execute(
                "INSERT INTO events(time,run_id,level,kind,job_id,message) VALUES(?,?,?,?,?,?)",
                (now(), run_id, "INFO", "send_reserved", job.job_id, "预占发送名额"),
            )
            self.db.execute("COMMIT")
            return None
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def delivery(self, job_id: str, status: str, note: str):
        if status not in DELIVERY_STATES:
            raise ValueError("非法投递状态")
        self.db.execute(
            "UPDATE deliveries SET status=?,note=?,updated_at=? WHERE job_id=?",
            (status, note, now(), job_id),
        )

    def mark_contacted(self, job: Job, run_id: str):
        self.db.execute(
            """INSERT INTO deliveries VALUES(?,?,?,?,?,?,?,?)
          ON CONFLICT(job_id) DO NOTHING""",
            (job.job_id, run_id, "contacted", "", "网页显示已沟通", None, now(), now()),
        )

    def resolve(self, job_id: str, status: str, note: str):
        if status not in {"sent", "not_sent"}:
            raise ValueError("只能人工核实为 sent 或 not_sent")
        row = self.db.execute("SELECT status FROM deliveries WHERE job_id=?", (job_id,)).fetchone()
        if not row or row[0] not in {"unknown", "partial", "sending"}:
            raise ValueError("只能核实 sending / unknown / partial 记录")
        self.delivery(job_id, status, f"人工核实：{note}")
        self.event(None, "INFO", "manual_resolve", f"人工核实为 {status}：{note}", job_id)

    def event(
        self, run_id: str | None, level: str, kind: str, message: str, job_id: str | None = None
    ):
        cursor = self.db.execute(
            "INSERT INTO events(time,run_id,level,kind,job_id,message) VALUES(?,?,?,?,?,?)",
            (now(), run_id, level, kind, job_id, message),
        )
        return cursor.lastrowid

    def events(self, after: int = 0, limit: int = 100, run_id: str | None = None) -> list[dict]:
        where = "id>?" + (" AND run_id=?" if run_id else "")
        params = (after, run_id, limit) if run_id else (after, limit)
        return [
            dict(r)
            for r in self.db.execute(
                f"SELECT * FROM events WHERE {where} ORDER BY id LIMIT ?", params
            )
        ]

    def recent_events(self, limit: int = 20) -> list[dict]:
        return list(
            reversed(
                [
                    dict(r)
                    for r in self.db.execute(
                        "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
                    )
                ]
            )
        )

    def history(self, status: str | None = None, limit: int = 100) -> list[dict]:
        condition = "WHERE d.status=?" if status else ""
        params = (status, limit) if status else (limit,)
        return [
            dict(row)
            for row in self.db.execute(
                f"""SELECT d.*,j.title,j.company,j.url,j.location,j.salary
          FROM deliveries d JOIN jobs j USING(job_id) {condition} ORDER BY d.updated_at DESC LIMIT ?""",
                params,
            )
        ]

    def jobs(self, limit: int = 100) -> list[dict]:
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT job_id,title,company,location,salary,url,updated_at FROM jobs ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            )
        ]
