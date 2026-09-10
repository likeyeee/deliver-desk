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
        CREATE TABLE IF NOT EXISTS replies (
          id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(job_id),
          run_id TEXT REFERENCES runs(run_id), context_hash TEXT NOT NULL,
          context TEXT NOT NULL, model TEXT NOT NULL, message TEXT NOT NULL,
          status TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL, UNIQUE(job_id, context_hash)
        );
        CREATE TABLE IF NOT EXISTS reply_members (
          job_id TEXT PRIMARY KEY REFERENCES jobs(job_id), last_seen_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS auto_reply_checks (
          job_id TEXT NOT NULL REFERENCES jobs(job_id), context_hash TEXT NOT NULL,
          context TEXT NOT NULL, status TEXT NOT NULL, note TEXT NOT NULL,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          PRIMARY KEY(job_id, context_hash)
        );
        CREATE TABLE IF NOT EXISTS greetings (
          id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
          job_id TEXT NOT NULL REFERENCES jobs(job_id), mode TEXT NOT NULL,
          title TEXT NOT NULL, company TEXT NOT NULL, job_json TEXT NOT NULL,
          resume_revision TEXT NOT NULL, resume_name TEXT NOT NULL, profile_json TEXT NOT NULL,
          model TEXT NOT NULL, instructions TEXT NOT NULL, input_hash TEXT NOT NULL,
          reused_from TEXT NOT NULL DEFAULT '', message TEXT NOT NULL DEFAULT '',
          usage TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'generating',
          note TEXT NOT NULL DEFAULT '', delivery_status TEXT NOT NULL DEFAULT '',
          delivery_note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          UNIQUE(run_id, job_id)
        );
        CREATE INDEX IF NOT EXISTS greetings_input ON greetings(job_id, input_hash);
        CREATE TRIGGER IF NOT EXISTS greeting_delivery_insert AFTER INSERT ON deliveries BEGIN
          UPDATE greetings SET delivery_status=NEW.status, delivery_note=NEW.note,
            updated_at=NEW.updated_at
          WHERE run_id=NEW.run_id AND job_id=NEW.job_id AND message=NEW.message;
        END;
        CREATE TRIGGER IF NOT EXISTS greeting_delivery_update AFTER UPDATE ON deliveries BEGIN
          UPDATE greetings SET delivery_status=NEW.status, delivery_note=NEW.note,
            updated_at=NEW.updated_at
          WHERE run_id=NEW.run_id AND job_id=NEW.job_id AND message=NEW.message;
        END;
        """)
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(runs)")}
        for name in ("target", "attempts"):
            if name not in columns:
                self.db.execute(f"ALTER TABLE runs ADD COLUMN {name} INTEGER NOT NULL DEFAULT 0")
        reply_columns = {row[1] for row in self.db.execute("PRAGMA table_info(replies)")}
        for name, definition in (
            ("source", "TEXT NOT NULL DEFAULT 'manual'"),
            ("usage", "TEXT NOT NULL DEFAULT '{}'"),
        ):
            if name not in reply_columns:
                self.db.execute(f"ALTER TABLE replies ADD COLUMN {name} {definition}")
        self.db.execute("PRAGMA user_version=5")

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
            "UPDATE replies SET status='unknown',note=?,updated_at=? WHERE status='sending'",
            ("上次回复期间退出，请到网站核对；不会自动重发", now()),
        )
        self.db.execute(
            "UPDATE auto_reply_checks SET status='failed',note=?,updated_at=? WHERE status='generating'",
            ("上次生成期间退出，未自动发送；重新开启监控后可再次生成", now()),
        )
        self.db.execute(
            "UPDATE greetings SET status='stopped',note=?,updated_at=? WHERE status='generating'",
            ("上次生成期间退出，未发起沟通", now()),
        )
        self.finish_greetings(note="上次任务已退出，未发起沟通")
        self.db.execute(
            "UPDATE runs SET status='interrupted',updated_at=?,note=? WHERE status IN ('starting','running','paused','stopping')",
            (now(), "上次进程已退出"),
        )

    def blocked(self, job_id: str) -> str | None:
        row = self.db.execute("SELECT status FROM deliveries WHERE job_id=?", (job_id,)).fetchone()
        return row[0] if row and row[0] != "not_sent" else None

    def begin_greeting(self, greeting_id, run_id, job, mode, resume, config, input_hash):
        self.save_job(job)
        self.db.execute(
            """INSERT INTO greetings(id,run_id,job_id,mode,title,company,job_json,
            resume_revision,resume_name,profile_json,model,instructions,input_hash,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                greeting_id,
                run_id,
                job.job_id,
                mode,
                job.title,
                job.company,
                json.dumps(job.as_dict(), ensure_ascii=False),
                resume.revision(),
                resume.source_name,
                resume.profile.model_dump_json(),
                config.llm.model,
                config.message.instructions,
                input_hash,
                now(),
                now(),
            ),
        )

    def cached_greeting(self, job_id, input_hash):
        row = self.db.execute(
            """SELECT id,message FROM greetings WHERE job_id=? AND input_hash=?
            AND status='generated' AND message<>'' ORDER BY rowid DESC LIMIT 1""",
            (job_id, input_hash),
        ).fetchone()
        return dict(row) if row else None

    def finish_greeting(
        self, greeting_id, status, *, message="", usage=None, note="", reused_from=""
    ):
        if status not in {"generated", "failed", "stopped"}:
            raise ValueError("无效的招呼生成状态")
        self.db.execute(
            """UPDATE greetings SET status=?,message=?,usage=?,note=?,reused_from=?,updated_at=?
            WHERE id=? AND status='generating'""",
            (
                status,
                message,
                json.dumps(usage or {}, ensure_ascii=False),
                note,
                reused_from,
                now(),
                greeting_id,
            ),
        )

    def skip_greeting(self, run_id, job_id, note):
        self.db.execute(
            """UPDATE greetings SET delivery_status='not_sent',delivery_note=?,updated_at=?
            WHERE run_id=? AND job_id=? AND mode='send' AND status='generated' AND delivery_status=''""",
            (note, now(), run_id, job_id),
        )

    def finish_greetings(self, run_id=None, *, note="任务已结束，未发起沟通"):
        self.db.execute(
            """UPDATE greetings SET delivery_status='not_sent',delivery_note=?,updated_at=?
            WHERE mode='send' AND status='generated' AND delivery_status='' AND (? IS NULL OR run_id=?)""",
            (note, now(), run_id, run_id),
        )

    def greeting(self, greeting_id):
        row = self.db.execute("SELECT * FROM greetings WHERE id=?", (greeting_id,)).fetchone()
        if not row:
            raise ValueError("招呼记录不存在")
        result = dict(row)
        for field in ("job_json", "profile_json", "usage"):
            result[field] = json.loads(result[field])
        return result

    def greetings_overview(self):
        row = self.db.execute(
            "SELECT COUNT(*) AS total,COALESCE(MAX(updated_at),'') AS updated_at FROM greetings"
        ).fetchone()
        # Include state changes within the same second so polling always refreshes the list.
        states = self.db.execute(
            "SELECT status,delivery_status,COUNT(*) FROM greetings GROUP BY status,delivery_status"
        ).fetchall()
        return dict(row) | {"revision": [list(state) for state in states]}

    def greeting_history(self, cursor=0, limit=50):
        cursor, limit = max(0, int(cursor)), max(1, min(100, int(limit)))
        rows = [
            dict(row)
            for row in self.db.execute(
                """SELECT rowid AS cursor,id,job_id,title,company,mode,model,status,note,
            delivery_status,delivery_note,reused_from,created_at,updated_at FROM greetings
            WHERE (?=0 OR rowid<?) ORDER BY rowid DESC LIMIT ?""",
                (cursor, cursor, limit + 1),
            )
        ]
        return {
            "items": rows[:limit],
            "nextCursor": rows[limit - 1]["cursor"] if len(rows) > limit else None,
        }

    def reply_job(self, job_id: str) -> Job:
        row = self.db.execute(
            """SELECT j.data FROM jobs j LEFT JOIN deliveries d USING(job_id)
            LEFT JOIN reply_members m USING(job_id) WHERE j.job_id=?
            AND (d.status IN ('sent','contacted','partial','unknown') OR m.job_id IS NOT NULL)""",
            (job_id,),
        ).fetchone()
        if not row:
            raise ValueError("请选择已沟通的职位，或先扫描网站中的会话")
        return Job(**json.loads(row["data"]))

    def mark_reply_contact(self, job: Job):
        """Discover an existing inbox conversation without inventing a delivery record."""
        known = self.db.execute("SELECT data FROM jobs WHERE job_id=?", (job.job_id,)).fetchone()
        data = job.as_dict()
        if known:
            previous = json.loads(known["data"])
            for field in ("location", "salary", "experience", "education", "tags", "description"):
                if not data[field]:
                    data[field] = previous.get(field, "")
        self.save_job(Job(**data))
        self.db.execute(
            """INSERT INTO reply_members VALUES(?,?)
            ON CONFLICT(job_id) DO UPDATE SET last_seen_at=excluded.last_seen_at""",
            (job.job_id, now()),
        )

    def reply_contacts(self) -> list[dict]:
        result = []
        for row in self.db.execute(
            """SELECT j.data,COALESCE(m.last_seen_at,d.updated_at) AS updated_at FROM jobs j
            LEFT JOIN deliveries d USING(job_id) LEFT JOIN reply_members m USING(job_id)
            WHERE d.status IN ('sent','contacted','partial','unknown') OR m.job_id IS NOT NULL
            ORDER BY updated_at DESC LIMIT 500"""
        ):
            data = json.loads(row["data"])
            result.append(
                {key: data.get(key, "") for key in ("job_id", "title", "company", "recruiter")}
                | {"updated_at": row["updated_at"]}
            )
        return result

    def reply(self, reply_id: str) -> dict:
        row = self.db.execute("SELECT * FROM replies WHERE id=?", (reply_id,)).fetchone()
        if not row:
            raise ValueError("回复草稿不存在，请重新读取会话")
        return dict(row)

    def reply_history(self, limit=100) -> list[dict]:
        return [
            dict(row)
            for row in self.db.execute(
                "SELECT r.id,r.job_id,r.model,r.message,r.status,r.note,r.source,r.usage,r.created_at,r.updated_at,j.title,j.company FROM replies r JOIN jobs j USING(job_id) ORDER BY r.rowid DESC LIMIT ?",
                (limit,),
            )
        ]

    def check_reply_pending(self, job_id: str):
        if self.db.execute(
            "SELECT 1 FROM replies WHERE job_id=? AND status IN ('sending','unknown')", (job_id,)
        ).fetchone():
            raise ValueError("这个会话有待核对的回复，请先在回复记录中人工核实")

    def save_reply(
        self,
        reply_id: str,
        job_id: str,
        context: dict,
        model: str,
        message: str,
        *,
        source="manual",
        usage=None,
    ):
        if source not in {"manual", "auto"}:
            raise ValueError("无效的回复来源")
        self.check_reply_pending(job_id)
        old = self.db.execute(
            "SELECT id,status FROM replies WHERE job_id=? AND context_hash=?",
            (job_id, context["fingerprint"]),
        ).fetchone()
        if old:
            if old["status"] not in {"draft", "not_sent"}:
                raise ValueError("这段会话已经回复，未重复生成")
            reply_id = old["id"]
        self.db.execute(
            """INSERT INTO replies(id,job_id,run_id,context_hash,context,model,message,
            status,note,created_at,updated_at,source,usage) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET model=excluded.model,message=excluded.message,
            context=excluded.context,status='draft',note=excluded.note,updated_at=excluded.updated_at,
            source=excluded.source,usage=excluded.usage""",
            (
                reply_id,
                job_id,
                None,
                context["fingerprint"],
                json.dumps(context, ensure_ascii=False),
                model,
                message,
                "draft",
                "草稿待确认",
                now(),
                now(),
                source,
                json.dumps(usage or {}),
            ),
        )
        return self.reply(reply_id)

    def auto_reply_check(self, job_id: str, context_hash: str) -> dict | None:
        row = self.db.execute(
            "SELECT * FROM auto_reply_checks WHERE job_id=? AND context_hash=?",
            (job_id, context_hash),
        ).fetchone()
        return dict(row) if row else None

    def record_auto_check(self, job_id: str, context: dict, status: str, note: str):
        if status not in {"pending", "generating", "sent", "skipped", "failed", "superseded"}:
            raise ValueError("无效的监控状态")
        self.db.execute(
            """INSERT INTO auto_reply_checks VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(job_id,context_hash) DO UPDATE SET context=excluded.context,
            status=excluded.status,note=excluded.note,updated_at=excluded.updated_at""",
            (
                job_id,
                context.get("replyKey", context["fingerprint"]),
                json.dumps(context, ensure_ascii=False),
                status,
                note,
                now(),
                now(),
            ),
        )

    def reset_auto_failures(self):
        # A new explicit toggle allows generation failures to be retried, never uncertain sends.
        self.db.execute(
            "UPDATE auto_reply_checks SET status='pending',updated_at=? WHERE status='failed'",
            (now(),),
        )

    def reply_events(self, limit=100) -> list[dict]:
        return [
            dict(row)
            for row in self.db.execute(
                """SELECT e.*,j.title,j.company FROM events e
                LEFT JOIN jobs j USING(job_id)
                WHERE e.kind LIKE 'reply_%' OR e.kind LIKE 'auto_%'
                ORDER BY e.id DESC LIMIT ?""",
                (limit,),
            )
        ]

    def reserve_reply(self, reply_id: str, run_id: str, message: str, limit: int):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            record = self.reply(reply_id)
            if record["status"] != "draft":
                raise ValueError("这条回复已处理，未重复发送")
            self.check_reply_pending(record["job_id"])
            if self.attempts_today() >= limit:
                raise ValueError("今日发送上限（含投递和回复）已达到")
            self.db.execute(
                "UPDATE replies SET run_id=?,message=?,status='sending',note=?,updated_at=? WHERE id=?",
                (run_id, message, "回复发送前已登记", now(), reply_id),
            )
            self.event(run_id, "INFO", "send_reserved", "预占回复发送名额", record["job_id"])
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def finish_reply(self, reply_id: str, status: str, note: str):
        if status not in {"sent", "unknown", "not_sent"}:
            raise ValueError("无效的回复状态")
        self.db.execute(
            "UPDATE replies SET status=?,note=?,updated_at=? WHERE id=?",
            (status, note, now(), reply_id),
        )

    def resolve_reply(self, reply_id: str, status: str, note: str):
        if status not in {"sent", "not_sent"} or not note.strip() or len(note) > 1000:
            raise ValueError("请选择核实结果并填写 1–1000 字说明")
        if self.reply(reply_id)["status"] != "unknown":
            raise ValueError("只能人工核实待核对的回复")
        self.finish_reply(reply_id, status, "人工核实：" + note.strip())
        self.event(None, "INFO", "reply_resolved", f"回复人工核实为 {status}")

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
