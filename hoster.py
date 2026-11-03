from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import sys
import tarfile
import threading
import time
import uuid

from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters
from telethon import TelegramClient
from telethon.errors import (
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
)
from typing import Any, Iterator, Optional
from lofi_ui import render_cards




def load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#") or "=" not in entry:
            continue
        key, value = entry.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and key.replace("_", "").isalnum():
            os.environ.setdefault(key, value)


@dataclass(frozen=True)
class Settings:
    root: Path
    data_dir: Path
    bot_token: str
    api_id: int
    api_hash: str
    owner_id: int
    database_path: Path
    session_dir: Path
    work_dir: Path
    log_dir: Path
    backup_dir: Path
    temp_dir: Path
    max_workers: int

    @classmethod
    def from_env(cls) -> "Settings":
        root = Path(__file__).resolve().parent
        load_env_file(root / ".env")
        try:
            bot_token = os.environ["BOT_TOKEN"].strip()
            api_id = int(os.environ["API_ID"])
            api_hash = os.environ["API_HASH"].strip()
            owner_id = int(os.environ["OWNER_ID"])
            max_workers = int(os.environ.get("MAX_WORKERS", "8"))
        except (KeyError, ValueError) as exc:
            raise ValueError("BOT_TOKEN, numeric API_ID, API_HASH, and numeric OWNER_ID are required") from exc
        if not bot_token or api_id <= 0 or not api_hash or owner_id <= 0:
            raise ValueError("Host configuration contains an empty or invalid required value")
        if not 1 <= max_workers <= 64:
            raise ValueError("MAX_WORKERS must be between 1 and 64")

        data_dir = Path(os.environ.get(
            "LOFI_DATA_DIR", Path.home() / ".local" / "share" / "lofi-hoster"
        )).expanduser().resolve()
        db_dir = data_dir / "db"
        session_dir = data_dir / "sessions"
        work_dir = data_dir / "accounts"
        log_dir = data_dir / "logs"
        backup_dir = data_dir / "backups"
        temp_dir = data_dir / "tmp"
        data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        for directory in (db_dir, session_dir, work_dir, log_dir, backup_dir, temp_dir):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        return cls(root, data_dir, bot_token, api_id, api_hash, owner_id,
                   db_dir / "hoster.sqlite3", session_dir, work_dir, log_dir,
                   backup_dir, temp_dir, max_workers)




_SECRET = re.compile(r"(?i)(token|api[_ -]?hash|password|phone[_ -]?code|login[_ -]?code|session)(\s*[:=]\s*|\s+)[^\s,;]+")
_PATH = re.compile(r"(?<!\w)/(?:[^\s:]+/)*[^\s:]+")


def sanitize(value: Any, limit: int = 300) -> str:
    text = str(value).replace("\x00", "")
    text = _SECRET.sub(r"\1\2[redacted]", text)
    text = re.sub(r"\+?\d{7,15}", "[redacted-number]", text)
    text = _PATH.sub("[path]", text)
    return text[:limit]


def write_audit(connection: sqlite3.Connection, actor_id: int, action: str,
                account_id: str | None, result: str, metadata: str = "") -> None:
    connection.execute(
        "INSERT INTO audit_logs(actor_user_id, action, account_id, timestamp, result, metadata) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (actor_id, sanitize(action, 80), account_id, datetime.now(timezone.utc).isoformat(),
         sanitize(result, 80), sanitize(metadata, 500)),
    )





class Database:
    SCHEMA_VERSION = 1

    def __init__(self, path: Path, owner_id: int):
        self.path = Path(path)
        self.owner_id = int(owner_id)
        self._lock = threading.RLock()
        self.initialize()

    @contextmanager
    def connect(self, write: bool = False) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = sqlite3.connect(self.path, timeout=15, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 15000")
            try:
                if write:
                    connection.execute("BEGIN IMMEDIATE")
                yield connection
                if write:
                    connection.commit()
            except Exception:
                if write:
                    connection.rollback()
                raise
            finally:
                connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._lock, closing(sqlite3.connect(self.path, timeout=15)) as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS users (
                    telegram_id INTEGER PRIMARY KEY,
                    role TEXT NOT NULL CHECK(role IN ('owner', 'sudo', 'limited')),
                    created_at TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1))
                );
                CREATE TABLE IF NOT EXISTS accounts (
                    account_id TEXT PRIMARY KEY,
                    telegram_user_id INTEGER NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    session_ref TEXT NOT NULL UNIQUE,
                    worker_status TEXT NOT NULL DEFAULT 'stopped',
                    created_at TEXT NOT NULL,
                    last_start TEXT,
                    last_heartbeat TEXT,
                    last_error_summary TEXT,
                    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1))
                );
                CREATE TABLE IF NOT EXISTS account_access (
                    admin_user_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
                    account_id TEXT NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
                    allowed_permissions TEXT NOT NULL,
                    PRIMARY KEY(admin_user_id, account_id)
                );
                CREATE TABLE IF NOT EXISTS settings (
                    account_id TEXT NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
                    setting_key TEXT NOT NULL,
                    setting_value TEXT NOT NULL,
                    PRIMARY KEY(account_id, setting_key)
                );
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    actor_user_id INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    account_id TEXT REFERENCES accounts(account_id) ON DELETE SET NULL,
                    timestamp TEXT NOT NULL,
                    result TEXT NOT NULL,
                    metadata TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS pairing_requests (
                    request_id TEXT PRIMARY KEY,
                    initiating_admin_id INTEGER NOT NULL REFERENCES users(telegram_id),
                    expires_at TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('pending', 'code_sent', 'password_required', 'completed', 'failed', 'cancelled')),
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS worker_state (
                    account_id TEXT PRIMARY KEY REFERENCES accounts(account_id) ON DELETE CASCADE,
                    process_id INTEGER,
                    started_at TEXT,
                    stopped_at TEXT,
                    restart_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    desired_running INTEGER NOT NULL DEFAULT 0 CHECK(desired_running IN (0, 1))
                );
                CREATE INDEX IF NOT EXISTS idx_access_admin ON account_access(admin_user_id);
                CREATE INDEX IF NOT EXISTS idx_access_account ON account_access(account_id);
                CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_logs(timestamp);
                CREATE INDEX IF NOT EXISTS idx_pairing_expiry ON pairing_requests(expires_at, state);
            """)
            row = connection.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
            if row is None:
                connection.execute("INSERT INTO schema_version(version) VALUES (?)", (self.SCHEMA_VERSION,))
            elif row[0] > self.SCHEMA_VERSION:
                raise RuntimeError("Database schema is newer than this hoster version")
            connection.execute(
                "INSERT INTO users(telegram_id, role, created_at, active) VALUES (?, 'owner', ?, 1) "
                "ON CONFLICT(telegram_id) DO UPDATE SET role='owner', active=1",
                (self.owner_id, self._now()),
            )
            connection.commit()
        self.path.chmod(0o600)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def get_role(self, user_id: int) -> str | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT role FROM users WHERE telegram_id=? AND active=1", (int(user_id),)
            ).fetchone()
        return row["role"] if row else None

    def list_users(self) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT telegram_id, role, created_at, active FROM users ORDER BY telegram_id"
            ).fetchall()
        return [dict(row) for row in rows]

    def set_user_role(self, actor_id: int, user_id: int, role: str | None) -> None:
        if int(user_id) == self.owner_id:
            raise ValueError("The configured owner role cannot be changed")
        with self.connect(write=True) as connection:
            if role is None:
                connection.execute("DELETE FROM users WHERE telegram_id=?", (int(user_id),))
            elif role not in {"sudo", "limited"}:
                raise ValueError("Only sudo and limited roles can be assigned")
            else:
                connection.execute(
                    "INSERT INTO users(telegram_id, role, created_at, active) VALUES (?, ?, ?, 1) "
                    "ON CONFLICT(telegram_id) DO UPDATE SET role=excluded.role, active=1",
                    (int(user_id), role, self._now()),
                )
            write_audit(connection, actor_id, "user_role", None, "success", f"user={user_id}; role={role}")

    def next_account_id(self) -> str:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT account_id FROM accounts WHERE account_id GLOB 'U[0-9]*'"
            ).fetchall()
        used = [int(item[0][1:]) for item in row if item[0][1:].isdigit()]
        return f"U{max(used, default=0) + 1:03d}"

    def account_exists_for_telegram_user(self, telegram_user_id: int) -> bool:
        with self.connect() as connection:
            return connection.execute(
                "SELECT 1 FROM accounts WHERE telegram_user_id=? AND active=1", (telegram_user_id,)
            ).fetchone() is not None

    def add_account(self, actor_id: int, telegram_user_id: int, display_name: str,
                    session_ref: str) -> str:
        with self.connect(write=True) as connection:
            if connection.execute(
                "SELECT 1 FROM accounts WHERE telegram_user_id=?", (telegram_user_id,)
            ).fetchone():
                raise ValueError("This Telegram account is already paired")
            rows = connection.execute("SELECT account_id FROM accounts").fetchall()
            used = [int(row[0][1:]) for row in rows if row[0].startswith("U") and row[0][1:].isdigit()]
            account_id = f"U{max(used, default=0) + 1:03d}"
            connection.execute(
                "INSERT INTO accounts(account_id, telegram_user_id, display_name, session_ref, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (account_id, int(telegram_user_id), str(display_name)[:120], session_ref, self._now()),
            )
            connection.execute(
                "INSERT INTO worker_state(account_id) VALUES (?)", (account_id,)
            )
            write_audit(connection, actor_id, "account_pair", account_id, "success")
            return account_id

    def get_account(self, account_id: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT a.*, w.process_id, w.started_at, w.stopped_at, w.restart_count, "
                "w.last_error, w.desired_running FROM accounts a "
                "LEFT JOIN worker_state w USING(account_id) WHERE a.account_id=? AND a.active=1",
                (account_id.upper(),),
            ).fetchone()
        return dict(row) if row else None

    def list_accounts(self) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT a.*, w.process_id, w.started_at, w.stopped_at, w.restart_count, "
                "w.last_error, w.desired_running FROM accounts a "
                "LEFT JOIN worker_state w USING(account_id) WHERE a.active=1 ORDER BY a.account_id"
            ).fetchall()
        return [dict(row) for row in rows]

    def accessible_accounts(self, user_id: int) -> list[dict]:
        if self.get_role(user_id) in {"owner", "sudo"}:
            return self.list_accounts()
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT a.* FROM accounts a JOIN account_access x USING(account_id) "
                "WHERE x.admin_user_id=? AND a.active=1 ORDER BY a.account_id", (int(user_id),)
            ).fetchall()
        return [dict(row) for row in rows]

    def has_account_permission(self, user_id: int, account_id: str, permission: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT u.role, x.allowed_permissions FROM users u "
                "JOIN accounts a ON a.account_id=? AND a.active=1 "
                "LEFT JOIN account_access x ON x.admin_user_id=u.telegram_id "
                "AND x.account_id=a.account_id "
                "WHERE u.telegram_id=? AND u.active=1",
                (account_id.upper(), int(user_id)),
            ).fetchone()
        if not row:
            return False
        if row["role"] in {"owner", "sudo"}:
            return True
        try:
            allowed = json.loads(row["allowed_permissions"])
        except (TypeError, json.JSONDecodeError):
            return False
        return permission in allowed

    def assign_account(self, actor_id: int, user_id: int, account_id: str,
                       permissions: set[str]) -> None:
        allowed = {"status", "start", "stop", "restart", "logs", "settings"}
        if not permissions or not permissions <= allowed:
            raise ValueError("Invalid account permission set")
        with self.connect(write=True) as connection:
            exists_user = connection.execute(
                "SELECT 1 FROM users WHERE telegram_id=? AND role IN ('sudo', 'limited') AND active=1",
                (int(user_id),),
            ).fetchone()
            exists_account = connection.execute(
                "SELECT 1 FROM accounts WHERE account_id=? AND active=1", (account_id.upper(),)
            ).fetchone()
            if not exists_user or not exists_account:
                raise ValueError("Admin or account does not exist")
            connection.execute(
                "INSERT INTO account_access(admin_user_id, account_id, allowed_permissions) VALUES (?, ?, ?) "
                "ON CONFLICT(admin_user_id, account_id) DO UPDATE SET allowed_permissions=excluded.allowed_permissions",
                (int(user_id), account_id.upper(), json.dumps(sorted(permissions))),
            )
            write_audit(connection, actor_id, "account_assign", account_id.upper(), "success", f"user={user_id}")

    def unassign_account(self, actor_id: int, user_id: int, account_id: str) -> None:
        with self.connect(write=True) as connection:
            connection.execute(
                "DELETE FROM account_access WHERE admin_user_id=? AND account_id=?",
                (int(user_id), account_id.upper()),
            )
            write_audit(connection, actor_id, "account_unassign", account_id.upper(), "success", f"user={user_id}")

    def access_for_user(self, user_id: int) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT account_id, allowed_permissions FROM account_access WHERE admin_user_id=? ORDER BY account_id",
                (int(user_id),),
            ).fetchall()
        return [{"account_id": row[0], "permissions": json.loads(row[1])} for row in rows]

    def create_pairing_request(self, user_id: int, expires_in: int = 600) -> str:
        request_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc)
        with self.connect(write=True) as connection:
            connection.execute(
                "UPDATE pairing_requests SET state='failed' WHERE state NOT IN ('completed','cancelled','failed') AND expires_at<?",
                (now.isoformat(),),
            )
            active = connection.execute(
                "SELECT 1 FROM pairing_requests WHERE state IN ('pending','code_sent','password_required') AND expires_at>? LIMIT 1",
                (now.isoformat(),),
            ).fetchone()
            if active:
                raise RuntimeError("A pairing request is already in progress")
            connection.execute(
                "INSERT INTO pairing_requests(request_id, initiating_admin_id, expires_at, state, created_at) "
                "VALUES (?, ?, ?, 'pending', ?)",
                (request_id, int(user_id), (now + timedelta(seconds=expires_in)).isoformat(), now.isoformat()),
            )
        return request_id

    def get_pairing_request(self, request_id: str, user_id: int) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM pairing_requests WHERE request_id=? AND initiating_admin_id=?",
                (request_id, int(user_id)),
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["expired"] = datetime.fromisoformat(result["expires_at"]) <= datetime.now(timezone.utc)
        return result

    def set_pairing_state(self, request_id: str, user_id: int, state: str) -> bool:
        with self.connect(write=True) as connection:
            cursor = connection.execute(
                "UPDATE pairing_requests SET state=? WHERE request_id=? AND initiating_admin_id=? "
                "AND state IN ('pending','code_sent','password_required') AND expires_at>?",
                (state, request_id, int(user_id), self._now()),
            )
            return cursor.rowcount == 1

    def cancel_pairing_requests(self, user_id: int) -> None:
        with self.connect(write=True) as connection:
            connection.execute(
                "UPDATE pairing_requests SET state='cancelled' WHERE initiating_admin_id=? "
                "AND state IN ('pending','code_sent','password_required')",
                (int(user_id),),
            )

    def set_worker_state(self, account_id: str, status: str, *, process_id: int | None = None,
                         desired_running: bool | None = None, error: str | None = None,
                         restart: bool = False) -> None:
        now = self._now()
        with self.connect(write=True) as connection:
            connection.execute(
                "UPDATE accounts SET worker_status=?, last_start=CASE WHEN ?='running' THEN ? ELSE last_start END, "
                "last_heartbeat=CASE WHEN ?='running' THEN ? ELSE last_heartbeat END, last_error_summary=? "
                "WHERE account_id=?",
                (status, status, now, status, now, error, account_id.upper()),
            )
            connection.execute(
                "UPDATE worker_state SET process_id=?, started_at=CASE WHEN ?='running' THEN ? ELSE started_at END, "
                "stopped_at=CASE WHEN ? IN ('stopped','failed') THEN ? ELSE stopped_at END, "
                "last_error=?, desired_running=COALESCE(?, desired_running), "
                "restart_count=restart_count + ? WHERE account_id=?",
                (process_id, status, now, status, now, error,
                 None if desired_running is None else int(desired_running), int(restart), account_id.upper()),
            )

    def recovery_accounts(self) -> list[str]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT account_id FROM worker_state WHERE desired_running=1 ORDER BY account_id"
            ).fetchall()
        return [row[0] for row in rows]

    def update_heartbeat(self, account_id: str) -> None:
        with self.connect(write=True) as connection:
            connection.execute(
                "UPDATE accounts SET last_heartbeat=? WHERE account_id=?",
                (self._now(), account_id.upper()),
            )

    def rename_account(self, actor_id: int, account_id: str, name: str) -> None:
        clean_name = name.strip()[:120]
        if not clean_name:
            raise ValueError("Name cannot be empty")
        with self.connect(write=True) as connection:
            cursor = connection.execute(
                "UPDATE accounts SET display_name=? WHERE account_id=? AND active=1",
                (clean_name, account_id.upper()),
            )
            if not cursor.rowcount:
                raise ValueError("Account not found")
            write_audit(connection, actor_id, "account_rename", account_id.upper(), "success")

    def remove_account(self, actor_id: int, account_id: str) -> dict:
        with self.connect(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM accounts WHERE account_id=? AND active=1", (account_id.upper(),)
            ).fetchone()
            if not row:
                raise ValueError("Account not found")
            account = dict(row)
            write_audit(connection, actor_id, "account_unpair", account_id.upper(), "success")
            connection.execute("DELETE FROM accounts WHERE account_id=?", (account_id.upper(),))
            return account

    def add_setting(self, account_id: str, key: str, value: str) -> None:
        if not key.replace("_", "").isalnum() or len(key) > 40 or len(value) > 500:
            raise ValueError("Invalid setting")
        with self.connect(write=True) as connection:
            connection.execute(
                "INSERT INTO settings(account_id, setting_key, setting_value) VALUES (?, ?, ?) "
                "ON CONFLICT(account_id, setting_key) DO UPDATE SET setting_value=excluded.setting_value",
                (account_id.upper(), key, value),
            )

    def account_settings(self, account_id: str) -> dict[str, str]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT setting_key, setting_value FROM settings WHERE account_id=? ORDER BY setting_key",
                (account_id.upper(),),
            ).fetchall()
        return {row[0]: row[1] for row in rows}

    def log_action(self, actor_id: int, action: str, account_id: str | None,
                   result: str, metadata: str = "") -> None:
        with self.connect(write=True) as connection:
            write_audit(connection, actor_id, action, account_id, result, metadata)

    def recent_audit(self, limit: int = 30) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT actor_user_id, action, account_id, timestamp, result, metadata "
                "FROM audit_logs ORDER BY id DESC LIMIT ?", (max(1, min(limit, 100)),)
            ).fetchall()
        return [dict(row) for row in rows]

    def stats(self) -> dict[str, int]:
        with self.connect() as connection:
            return {
                "accounts": connection.execute("SELECT count(*) FROM accounts WHERE active=1").fetchone()[0],
                "running": connection.execute("SELECT count(*) FROM accounts WHERE worker_status='running'").fetchone()[0],
                "admins": connection.execute("SELECT count(*) FROM users WHERE role IN ('sudo','limited') AND active=1").fetchone()[0],
            }

    def backup_to(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._lock, sqlite3.connect(self.path) as source, sqlite3.connect(destination) as backup:
            source.backup(backup)
        destination.chmod(0o600)


class PermissionDenied(PermissionError):
    pass


def require_access(database: Database, actor_id: int, permission: str,
                   account_id: str | None = None) -> None:
    role = database.get_role(actor_id)
    if role in {"owner", "sudo"}:
        return
    if account_id is None or role not in {"sudo", "limited"}:
        raise PermissionDenied("You are not authorized for this operation.")
    if not database.has_account_permission(actor_id, account_id, permission):
        raise PermissionDenied("You are not authorized for this account action.")






@dataclass
class PairFlow:
    request_id: str
    client: Optional[TelegramClient]
    phone: str
    code_hash: str
    session_temp: Path
    expires_at: float
    phase: str = "code"


def normalize_login_code(raw_code: str) -> str:
    candidate = raw_code.strip()
    if not re.fullmatch(r"[0-9](?:[\s|]*[0-9]){3,9}", candidate):
        raise ValueError(
            "Enter 4-10 digits in order. Paste the code or separate digits with spaces, newlines, or |."
        )
    return re.sub(r"[\s|]", "", candidate)


class PairingManager:
    def __init__(self, database: Database, api_id: int, api_hash: str,
                 session_dir: Path, temp_dir: Path, ttl_seconds: int = 600):
        self.database = database
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_dir = Path(session_dir)
        self.temp_dir = Path(temp_dir)
        self.ttl_seconds = ttl_seconds
        self._flows: dict[int, PairFlow] = {}
        self._lock = asyncio.Lock()

    def phase(self, user_id: int) -> str | None:
        flow = self._flows.get(user_id)
        if not flow:
            return None
        if asyncio.get_running_loop().time() >= flow.expires_at:
            asyncio.create_task(self.cancel(user_id))
            return "expired"
        return flow.phase

    async def begin(self, user_id: int) -> None:
        async with self._lock:
            if self._flows:
                raise RuntimeError("A pairing request is already active")
            request_id = self.database.create_pairing_request(user_id, self.ttl_seconds)
            self._flows[user_id] = PairFlow(
                request_id=request_id,
                client=None,
                phone="",
                code_hash="",
                session_temp=self.temp_dir / f"pair-{uuid.uuid4().hex}",
                expires_at=asyncio.get_running_loop().time() + self.ttl_seconds,
                phase="phone",
            )

    async def submit_phone(self, user_id: int, phone: str) -> str:
        phone = phone.strip().replace(" ", "").replace("-", "")
        if not re.fullmatch(r"\+[1-9]\d{6,14}", phone):
            raise ValueError("Enter a phone number in international format, for example +15551234567.")
        async with self._lock:
            flow = self._get_flow(user_id, "phone")
            flow.phone = phone
            flow.client = TelegramClient(str(flow.session_temp), self.api_id, self.api_hash)
            try:
                await flow.client.connect()
                sent = await flow.client.send_code_request(phone)
                flow.code_hash = sent.phone_code_hash
                flow.phase = "code"
                self.database.set_pairing_state(flow.request_id, user_id, "code_sent")
                return (
                    "Login code sent. Enter the digits in order, either pasted together or separated "
                    "by spaces, newlines, or | (example: 4 | 5 | 6 | 7 | 8 | 3). "
                    "Your message will be deleted after receipt."
                )
            except Exception as exc:
                await self._fail(user_id, flow)
                raise RuntimeError("Telegram could not start pairing. Check the phone number and try again.") from exc

    async def submit_code(self, user_id: int, code: str) -> tuple[str, str | None]:
        code = normalize_login_code(code)
        async with self._lock:
            flow = self._get_flow(user_id, "code")
            try:
                await flow.client.sign_in(phone=flow.phone, code=code, phone_code_hash=flow.code_hash)
            except SessionPasswordNeededError:
                flow.phase = "password"
                self.database.set_pairing_state(flow.request_id, user_id, "password_required")
                return "Two-step verification is enabled. Send its password in this private chat; the message will be deleted.", None
            except PhoneCodeInvalidError as exc:
                raise ValueError("That login code is incorrect. Try again or use /cancelpair.") from exc
            except PhoneCodeExpiredError as exc:
                try:
                    sent = await flow.client.send_code_request(flow.phone)
                    flow.code_hash = sent.phone_code_hash
                except Exception as request_error:
                    await self._fail(user_id, flow)
                    raise RuntimeError(
                        "The code expired and Telegram could not send a replacement. Start /pair again."
                    ) from request_error
                raise ValueError("That code expired. Telegram sent a new code; enter the new code now.") from exc
            except Exception as exc:
                await self._fail(user_id, flow)
                raise RuntimeError("Telegram could not verify the code. Start /pair again.") from exc
            account_id = await self._complete(user_id, flow)
            return f"Account paired as {account_id}.", account_id

    async def submit_password(self, user_id: int, password: str) -> str:
        if not password or len(password) > 1024:
            raise ValueError("The verification password is invalid.")
        async with self._lock:
            flow = self._get_flow(user_id, "password")
            try:
                await flow.client.sign_in(password=password)
            except PasswordHashInvalidError as exc:
                raise ValueError("That two-step password was not accepted. Try again or use /cancelpair.") from exc
            except Exception as exc:
                await self._fail(user_id, flow)
                raise RuntimeError("Two-step verification failed. Start a new pairing request.") from exc
            account_id = await self._complete(user_id, flow)
            return f"Account paired as {account_id}."

    async def cancel(self, user_id: int) -> bool:
        async with self._lock:
            flow = self._flows.pop(user_id, None)
            self.database.cancel_pairing_requests(user_id)
            if not flow:
                return False
            await self._dispose(flow)
            return True

    def _get_flow(self, user_id: int, phase: str) -> PairFlow:
        flow = self._flows.get(user_id)
        if not flow:
            raise ValueError("No active pairing request. Use /pair to begin.")
        if asyncio.get_running_loop().time() >= flow.expires_at:
            asyncio.create_task(self.cancel(user_id))
            raise ValueError("Pairing request expired. Use /pair to begin again.")
        persisted = self.database.get_pairing_request(flow.request_id, user_id)
        if not persisted or persisted["expired"] or persisted["state"] in {"completed", "cancelled", "failed"}:
            self._flows.pop(user_id, None)
            raise ValueError("Pairing request expired. Use /pair to begin again.")
        if flow.phase != phase:
            raise ValueError("That input is not expected now. Use /cancelpair to restart pairing.")
        return flow

    async def _complete(self, user_id: int, flow: PairFlow) -> str:
        destination: Path | None = None
        session_moved = False
        account_saved = False
        try:
            me = await flow.client.get_me()
            if not me or not me.id:
                raise RuntimeError("Account identity could not be verified")
            if self.database.account_exists_for_telegram_user(me.id):
                raise ValueError("This Telegram account is already paired")
            account_id = self.database.next_account_id()
            destination = self.session_dir / f"{account_id}.session"
            if destination.exists():
                raise RuntimeError("Session destination already exists")
            await flow.client.disconnect()
            source = Path(f"{flow.session_temp}.session")
            if not source.is_file():
                raise RuntimeError("Session was not saved")
            os.chmod(source, 0o600)
            os.replace(source, destination)
            session_moved = True
            destination.chmod(0o600)
            self.database.add_account(user_id, me.id, " ".join(filter(None, [me.first_name, me.last_name])) or str(me.id), account_id)
            account_saved = True
            self.database.set_pairing_state(flow.request_id, user_id, "completed")
            self._flows.pop(user_id, None)
            await self._dispose(flow)
            return account_id
        except Exception:
            if destination and session_moved and not account_saved:
                try:
                    destination.unlink(missing_ok=True)
                except OSError:
                    pass
            await self._fail(user_id, flow)
            raise

    async def _fail(self, user_id: int, flow: PairFlow) -> None:
        self._flows.pop(user_id, None)
        self.database.set_pairing_state(flow.request_id, user_id, "failed")
        await self._dispose(flow)

    @staticmethod
    async def _dispose(flow: PairFlow) -> None:
        if flow.client is not None:
            try:
                if flow.client.is_connected():
                    await flow.client.disconnect()
            except Exception:
                pass
        flow.phone = ""
        flow.code_hash = ""
        for suffix in (".session", ".session-journal", ".session-wal", ".session-shm"):
            path = Path(f"{flow.session_temp}{suffix}")
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass





@dataclass
class Worker:
    task: asyncio.Task
    process: asyncio.subprocess.Process | None = None
    stopping: bool = False
    started: asyncio.Event | None = None


class ProcessManager:
    def __init__(self, settings: Settings, database: Database):
        self.settings = settings
        self.database = database
        self._workers: dict[str, Worker] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._semaphore = asyncio.Semaphore(settings.max_workers)
        self._closed = False

    def _lock(self, account_id: str) -> asyncio.Lock:
        return self._locks.setdefault(account_id, asyncio.Lock())

    async def start(self, account_id: str, actor_id: int = 0) -> str:
        account_id = account_id.upper()
        account = self.database.get_account(account_id)
        if not account:
            raise ValueError("Account not found")
        account_work_dir = self.settings.work_dir / account_id
        account_work_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        account_work_dir.chmod(0o700)
        async with self._lock(account_id):
            current = self._workers.get(account_id)
            if current and not current.task.done():
                raise RuntimeError("Worker is already running or starting")
            if self._closed:
                raise RuntimeError("Hoster is shutting down")
            started = asyncio.Event()
            worker = Worker(task=None, started=started)
            self._workers[account_id] = worker
            self.database.set_worker_state(account_id, "starting", desired_running=True)
            worker.task = asyncio.create_task(self._supervise(account_id, worker), name=f"worker-{account_id}")
        try:
            await asyncio.wait_for(started.wait(), timeout=12)
        except asyncio.TimeoutError as exc:
            await self.stop(account_id, actor_id)
            raise RuntimeError("Worker did not start in time") from exc
        if worker.process is None or worker.process.returncode is not None:
            raise RuntimeError("Worker exited during startup")
        self.database.log_action(actor_id, "worker_start", account_id, "started")
        return "started"

    async def stop(self, account_id: str, actor_id: int = 0,
                   preserve_desired: bool = False) -> str:
        account_id = account_id.upper()
        async with self._lock(account_id):
            worker = self._workers.get(account_id)
            if not worker or worker.task.done():
                self.database.set_worker_state(account_id, "stopped", process_id=None,
                                               desired_running=preserve_desired)
                return "already stopped"
            worker.stopping = True
            self.database.set_worker_state(account_id, "stopping",
                                           desired_running=preserve_desired)
            process = worker.process
            if process and process.returncode is None:
                process.terminate()
        try:
            await asyncio.wait_for(worker.task, timeout=15)
        except asyncio.TimeoutError:
            process = worker.process
            if process and process.returncode is None:
                process.kill()
            await worker.task
        self.database.set_worker_state(account_id, "stopped", process_id=None,
                                       desired_running=preserve_desired)
        self.database.log_action(actor_id, "worker_stop", account_id, "stopped")
        return "stopped"

    async def restart(self, account_id: str, actor_id: int = 0) -> str:
        await self.stop(account_id, actor_id)
        return await self.start(account_id, actor_id)

    async def shutdown(self) -> None:
        self._closed = True
        await asyncio.gather(*(self.stop(account_id, preserve_desired=True)
                       for account_id in list(self._workers)),
                             return_exceptions=True)

    async def recover(self) -> None:
        semaphore = asyncio.Semaphore(min(self.settings.max_workers, 4))

        async def recover_one(account_id: str) -> None:
            async with semaphore:
                try:
                    await self.start(account_id)
                except (RuntimeError, ValueError):
                    self.database.set_worker_state(account_id, "failed", desired_running=True,
                                                   error="recovery start failed")

        await asyncio.gather(*(recover_one(account_id) for account_id in self.database.recovery_accounts()))

    async def _supervise(self, account_id: str, worker: Worker) -> None:
        restarts: list[float] = []
        attempt = 0
        async with self._semaphore:
            while not worker.stopping and not self._closed:
                account = self.database.get_account(account_id)
                if not account:
                    return
                now = time.monotonic()
                restarts = [stamp for stamp in restarts if now - stamp < 900]
                if len(restarts) >= 5:
                    self.database.set_worker_state(account_id, "failed", process_id=None,
                                                   desired_running=True, error="restart limit reached")
                    worker.started.set()
                    return
                try:
                    process = await asyncio.create_subprocess_exec(
                        sys.executable,
                        str(self.settings.root / "worker.py"),
                        account_id,
                        cwd=str(self.settings.work_dir / account_id),
                        env=self._worker_environment(account, account_id),
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.STDOUT,
                    )
                    worker.process = process
                    log_task = asyncio.create_task(self._pump_logs(account_id, process))
                    self.database.set_worker_state(account_id, "running", process_id=process.pid,
                                                   desired_running=True, error=None,
                                                   restart=bool(restarts))
                    worker.started.set()
                    return_code = await self._wait_with_heartbeats(account_id, process)
                    await log_task
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    return_code = -1
                    self.database.set_worker_state(account_id, "failed", process_id=None,
                                                   desired_running=True, error=sanitize(exc, 160))
                finally:
                    worker.process = None
                if worker.stopping or self._closed:
                    return
                restarts.append(time.monotonic())
                attempt += 1
                self.database.set_worker_state(account_id, "restarting", process_id=None,
                                               desired_running=True,
                                               error=f"worker exited ({return_code})")
                worker.started.set()
                await asyncio.sleep(min(60, 2 ** min(attempt, 6)))

    async def _wait_with_heartbeats(self, account_id: str,
                                    process: asyncio.subprocess.Process) -> int:
        while True:
            try:
                return await asyncio.wait_for(process.wait(), timeout=30)
            except asyncio.TimeoutError:
                self.database.update_heartbeat(account_id)

    async def _pump_logs(self, account_id: str,
                         process: asyncio.subprocess.Process) -> None:
        handler = RotatingFileHandler(
            self.settings.log_dir / f"{account_id}.log",
            maxBytes=1_000_000,
            backupCount=4,
            encoding="utf-8",
        )
        os.chmod(handler.baseFilename, 0o600)
        logger = logging.getLogger(f"hoster.worker.{account_id}.{process.pid}")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger.addHandler(handler)
        try:
            if process.stdout:
                while line := await process.stdout.readline():
                    logger.info("%s", sanitize(line.decode("utf-8", errors="replace").rstrip(), 800))
        finally:
            logger.removeHandler(handler)
            handler.close()

    def _worker_environment(self, account: dict, account_id: str) -> dict[str, str]:
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", str(self.settings.root)),
            "PYTHONPATH": str(self.settings.root),
            "API_ID": str(self.settings.api_id),
            "API_HASH": self.settings.api_hash,
            "OWNER_ID": str(self.settings.owner_id),
            "LOFI_HOSTER_WORKER": "1",
            "LOFI_ACCOUNT_ID": account_id,
            "LOFI_DATA_DIR": str(self.settings.data_dir),
            "LOFI_WORKER_DIR": str(self.settings.work_dir / account_id),
            "LOFI_SESSION_PATH": str(self.settings.session_dir / account["session_ref"]),
            "PYTHONUNBUFFERED": "1",
        }
        return env


async def post_init(application: Application) -> None:
    service = application.bot_data["service"]
    await service.process_manager.recover()


async def post_shutdown(application: Application) -> None:
    service = application.bot_data.get("service")
    if service:
        await service.pairing.cancel(service.settings.owner_id)
        await service.process_manager.shutdown()


def build_application(settings: Settings) -> Application:
    database = Database(settings.database_path, settings.owner_id)
    manager = ProcessManager(settings, database)
    pairing = PairingManager(
        database, settings.api_id, settings.api_hash, settings.session_dir, settings.temp_dir
    )
    app_service = HosterService(settings, database, manager, pairing)
    application = (
        Application.builder()
        .token(settings.bot_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    application.bot_data["service"] = app_service
    register_handlers(application)
    return application


def configure_logging(settings: Settings) -> None:
    handler = RotatingFileHandler(
        settings.log_dir / "hoster.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    Path(handler.baseFilename).chmod(0o600)
    logging.basicConfig(
        level=logging.INFO,
        handlers=[handler],
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)





def create_backup(database: Database, session_dir: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = datetime.now(timezone.utc).strftime("lofi-backup-%Y%m%dT%H%M%SZ.tar.gz")
    destination = backup_dir / name
    db_copy = backup_dir / ".hoster-backup.sqlite3"
    try:
        database.backup_to(db_copy)
        with tarfile.open(destination, "w:gz") as archive:
            archive.add(db_copy, arcname="hoster.sqlite3")
            if session_dir.exists():
                for account in database.list_accounts():
                    session = session_dir / f"{account['session_ref']}.session"
                    if session.is_file():
                        archive.add(session, arcname=f"sessions/{session.name}")
        destination.chmod(0o600)
        return destination
    finally:
        db_copy.unlink(missing_ok=True)
        for suffix in ("-wal", "-shm"):
            Path(f"{db_copy}{suffix}").unlink(missing_ok=True)




STARTED_AT = time.time()


def host_health(root: Path, data_path: Path) -> dict[str, str]:
    total, used, free = shutil.disk_usage(root)
    return {
        "uptime_seconds": str(max(0, int(time.time() - STARTED_AT))),
        "pid": str(os.getpid()),
        "disk_free_mb": str(free // (1024 * 1024)),
        "database_exists": str(data_path.exists()),
        "root": str(root),
    }




def panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("𐙚 Accounts", callback_data="panel:accounts"),
         InlineKeyboardButton("✧ Health", callback_data="panel:health")],
        [InlineKeyboardButton("୨୧ Pair account", callback_data="panel:pair")],
    ])


def account_keyboard(accounts: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for account in accounts[:40]:
        account_id = account["account_id"]
        rows.append([InlineKeyboardButton(
            f"𐙚 {account_id} · {account['display_name']} · {account['worker_status']}",
            callback_data=f"account:{account_id}",
        )])
    return InlineKeyboardMarkup(rows or [[InlineKeyboardButton("✧ No accounts", callback_data="noop")]])


def actions_keyboard(account_id: str, running: bool) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton("✧ Status", callback_data=f"action:status:{account_id}"),
        InlineKeyboardButton("𓂃 Logs", callback_data=f"action:logs:{account_id}"),
    ]
    buttons.append(InlineKeyboardButton(
        "⏸ Stop" if running else "▶ Start",
        callback_data=f"action:{'stop' if running else 'start'}:{account_id}",
    ))
    buttons.append(InlineKeyboardButton("↻ Restart", callback_data=f"confirm:restart:{account_id}"))
    return InlineKeyboardMarkup([buttons])






@dataclass
class HosterService:
    settings: Settings
    database: Database
    process_manager: ProcessManager
    pairing: PairingManager
    selected: dict[int, str] = field(default_factory=dict)
    confirmations: dict[str, tuple[int, str, str, float]] = field(default_factory=dict)


def service(context: ContextTypes.DEFAULT_TYPE) -> HosterService:
    return context.application.bot_data["service"]


def actor_id(update: Update) -> int:
    user = update.effective_user
    return int(user.id) if user else 0


def is_owner(svc: HosterService, user_id: int) -> bool:
    return svc.database.get_role(user_id) == "owner"


def is_privileged_admin(svc: HosterService, user_id: int) -> bool:
    return svc.database.get_role(user_id) in {"owner", "sudo"}


def require_owner(svc: HosterService, user_id: int) -> None:
    if not is_owner(svc, user_id):
        raise PermissionDenied("This operation is owner-only.")


def require_admin(svc: HosterService, user_id: int) -> None:
    if not is_privileged_admin(svc, user_id):
        raise PermissionDenied("This operation is available to owner and sudo admins.")


def account_id_arg(context: ContextTypes.DEFAULT_TYPE) -> str:
    if len(context.args) != 1 or not re.fullmatch(r"U\d{3,8}", context.args[0].upper()):
        raise ValueError("Usage: /command ACCOUNT_ID")
    return context.args[0].upper()


async def respond(update: Update, text: str, **kwargs) -> None:
    message = update.effective_message
    if message:
        cards = render_cards(text)
        for index, card in enumerate(cards):
            options = kwargs.copy() if index == 0 else {
                key: value for key, value in kwargs.items() if key != "reply_markup"
            }
            await message.reply_text(card, **options)


async def edit_callback_response(query, text: str, **kwargs) -> None:
    cards = render_cards(text)
    await query.edit_message_text(cards[0], **kwargs)
    message = getattr(query, "message", None)
    if message:
        for card in cards[1:]:
            await message.reply_text(card)


def account_summary(account: dict) -> str:
    return (
        f"{account['account_id']} · {account['display_name']}\n"
        f"Telegram ID: {account['telegram_user_id']}\n"
        f"Worker: {account['worker_status']}\n"
        f"Last start: {account.get('last_start') or 'never'}"
    )


def confirmation_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("Confirm", callback_data=f"confirm:{token}"),
        InlineKeyboardButton("Cancel", callback_data=f"cancel:{token}"),
    ]])


def make_confirmation(svc: HosterService, user_id: int, operation: str,
                      target: str) -> str:
    now = time.monotonic()
    svc.confirmations = {
        token: item for token, item in svc.confirmations.items() if item[3] > now
    }
    token = secrets.token_urlsafe(12)
    svc.confirmations[token] = (user_id, operation, target, now + 120)
    return token


async def account_action(svc: HosterService, user_id: int, action: str,
                         account_id: str) -> str:
    require_access(svc.database, user_id, action, account_id)
    account = svc.database.get_account(account_id)
    if not account:
        raise ValueError("Account not found")
    if action == "status":
        return account_summary(account)
    if action == "start":
        await svc.process_manager.start(account_id, user_id)
    elif action == "stop":
        await svc.process_manager.stop(account_id, user_id)
    elif action == "restart":
        await svc.process_manager.restart(account_id, user_id)
    elif action == "logs":
        log_path = svc.settings.log_dir / f"{account_id}.log"
        try:
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]
        except FileNotFoundError:
            lines = []
        content = "\n".join(sanitize(line, 250) for line in lines)
        return f"Recent logs for {account_id}:\n{content or '(no logs yet)'}"
    else:
        raise ValueError("Unknown account action")
    return f"{account_id}: {action} requested."


async def command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    svc = service(context)
    user_id = actor_id(update)
    message = update.effective_message
    if not message or not user_id:
        return
    name = message.text.split(maxsplit=1)[0].split("@", 1)[0].lstrip("/").lower()
    args = context.args
    try:
        if name in {"start", "help"}:
            role = svc.database.get_role(user_id)
            if role:
                await respond(update, "LOFI GWD Hoster is ready. Use /panel, /accounts, /status, or /help.\n"
                                      "Admin operations: /pair, /assign, /backup, /audit.\n"
                                      "Only the configured owner can use /addsudo and /remsudo.\n"
                                      "Account actions: /startuser, /stopuser, /restartuser, /logs, /settings.")
            else:
                await respond(update, "You are not authorized to manage this hoster.")
            return
        if name == "panel":
            if not svc.database.get_role(user_id):
                raise PermissionDenied("You are not authorized.")
            await respond(update, "LOFI GWD Hoster", reply_markup=panel_keyboard())
            return
        if name == "pair":
            require_admin(svc, user_id)
            if update.effective_chat.type != "private":
                raise PermissionDenied("Pairing is available only in a private chat.")
            await svc.pairing.begin(user_id)
            await respond(update, "Send the account phone number in international format. Never send pairing details in a group.")
            return
        if name == "cancelpair":
            require_admin(svc, user_id)
            cancelled = await svc.pairing.cancel(user_id)
            await respond(update, "Pairing cancelled." if cancelled else "No active pairing request.")
            return
        if name == "accounts":
            if not svc.database.get_role(user_id):
                raise PermissionDenied("You are not authorized.")
            accounts = svc.database.accessible_accounts(user_id)
            await respond(update, "Accounts:", reply_markup=account_keyboard(accounts))
            return
        if name == "account":
            if len(args) > 1:
                raise ValueError("Usage: /account [ACCOUNT_ID]")
            account_id = args[0].upper() if args else svc.selected.get(user_id)
            if not account_id:
                raise ValueError("Usage: /account ACCOUNT_ID")
            require_access(svc.database, user_id, "status", account_id)
            account = svc.database.get_account(account_id)
            if not account:
                raise ValueError("Account not found")
            await respond(update, account_summary(account), reply_markup=actions_keyboard(account_id, account["worker_status"] == "running"))
            return
        if name == "select":
            account_id = account_id_arg(context)
            require_access(svc.database, user_id, "status", account_id)
            if not svc.database.get_account(account_id):
                raise ValueError("Account not found")
            svc.selected[user_id] = account_id
            await respond(update, f"Selected {account_id}.")
            return
        if name in {"status", "startuser", "stopuser", "restartuser", "unpair", "rename", "logs", "settings"}:
            if name == "status" and not args:
                accounts = svc.database.accessible_accounts(user_id)
                if not accounts and not svc.database.get_role(user_id):
                    raise PermissionDenied("You are not authorized.")
                text = "\n\n".join(account_summary(account) for account in accounts) or "No accounts paired."
                await respond(update, text)
                return
            if name in {"restartuser", "unpair"}:
                account_id = account_id_arg(context)
                if name == "unpair":
                    require_admin(svc, user_id)
                else:
                    require_access(svc.database, user_id, "restart", account_id)
                token = make_confirmation(svc, user_id, name, account_id)
                await respond(update, f"Confirm {name} for {account_id}?", reply_markup=confirmation_keyboard(token))
                return
            if name == "rename":
                require_admin(svc, user_id)
                if len(args) < 2:
                    raise ValueError("Usage: /rename ACCOUNT_ID NAME")
                account_id = args[0].upper()
                svc.database.rename_account(user_id, account_id, " ".join(args[1:]))
                await respond(update, f"Renamed {account_id}.")
                return
            if name == "settings":
                account_id = account_id_arg(context)
                require_access(svc.database, user_id, "settings", account_id)
                values = svc.database.account_settings(account_id)
                await respond(update, f"Settings for {account_id}:\n" + ("\n".join(f"{key}: {value}" for key, value in values.items()) or "No account settings."))
                return
            action = {"startuser": "start", "stopuser": "stop", "logs": "logs", "status": "status"}[name]
            account_id = account_id_arg(context) if args else svc.selected.get(user_id)
            if not account_id:
                raise ValueError(f"Usage: /{name} ACCOUNT_ID")
            await respond(update, await account_action(svc, user_id, action, account_id))
            return
        if name == "health":
            require_admin(svc, user_id)
            values = host_health(svc.settings.root, svc.settings.database_path)
            await respond(update, "Host health\n" + "\n".join(f"{key}: {value}" for key, value in values.items()))
            return
        if name == "stats":
            require_admin(svc, user_id)
            counts = svc.database.stats()
            await respond(update, "Stats\n" + "\n".join(f"{key}: {value}" for key, value in counts.items()))
            return
        if name == "version":
            await respond(update, "LOFI GWD Hoster 1.0.0")
            return
        if name == "addsudo":
            require_owner(svc, user_id)
            if len(args) != 1 or not args[0].isdigit():
                raise ValueError("Usage: /addsudo TELEGRAM_USER_ID")
            svc.database.set_user_role(user_id, int(args[0]), "sudo")
            await respond(
                update,
                "Sudo admin added with full Hoster access. Only the configured owner can add or remove sudo admins.",
            )
            return
        if name == "addlimited":
            require_admin(svc, user_id)
            if len(args) != 1 or not args[0].isdigit():
                raise ValueError("Usage: /addlimited TELEGRAM_USER_ID")
            target_id = int(args[0])
            if svc.database.get_role(target_id) == "sudo":
                require_owner(svc, user_id)
            svc.database.set_user_role(user_id, target_id, "limited")
            await respond(update, "Limited admin added. Assign only the required account permissions with /assign.")
            return
        if name == "sudos":
            require_admin(svc, user_id)
            rows = svc.database.list_users()
            text = "\n".join(f"{row['telegram_id']} · {row['role']}" for row in rows if row["role"] in {"sudo", "limited"})
            await respond(update, text or "No sudo admins.")
            return
        if name == "remsudo":
            require_owner(svc, user_id)
            if len(args) != 1 or not args[0].isdigit():
                raise ValueError("Usage: /remsudo TELEGRAM_USER_ID")
            token = make_confirmation(svc, user_id, "remsudo", args[0])
            await respond(update, f"Confirm removal of admin {args[0]}?", reply_markup=confirmation_keyboard(token))
            return
        if name == "assign":
            require_admin(svc, user_id)
            if len(args) not in {2, 3} or not args[0].isdigit():
                raise ValueError("Usage: /assign TELEGRAM_USER_ID ACCOUNT_ID [permission,permission]")
            permissions = set(args[2].split(",")) if len(args) == 3 else {"status", "start", "stop", "restart", "logs", "settings"}
            svc.database.assign_account(user_id, int(args[0]), args[1].upper(), permissions)
            await respond(update, f"Assigned {args[1].upper()} to {args[0]}.")
            return
        if name == "unassign":
            require_admin(svc, user_id)
            if len(args) != 2 or not args[0].isdigit():
                raise ValueError("Usage: /unassign TELEGRAM_USER_ID ACCOUNT_ID")
            svc.database.unassign_account(user_id, int(args[0]), args[1].upper())
            await respond(update, "Account assignment removed.")
            return
        if name == "permissions":
            if len(args) != 1 or not args[0].isdigit():
                raise ValueError("Usage: /permissions TELEGRAM_USER_ID")
            target = int(args[0])
            if user_id != target:
                require_admin(svc, user_id)
            role = svc.database.get_role(target)
            assignments = svc.database.access_for_user(target)
            text = f"Role: {role or 'none'}\n" + "\n".join(f"{row['account_id']}: {', '.join(row['permissions'])}" for row in assignments)
            await respond(update, text)
            return
        if name == "audit":
            require_admin(svc, user_id)
            rows = svc.database.recent_audit()
            text = "\n".join(f"{row['timestamp']} · {row['actor_user_id']} · {row['action']} · {row['account_id'] or '-'} · {row['result']}" for row in rows)
            await respond(update, text or "No audit entries.")
            return
        if name == "backup":
            require_admin(svc, user_id)
            path = create_backup(svc.database, svc.settings.session_dir, svc.settings.backup_dir)
            await respond(update, f"Protected backup created ({path.stat().st_size} bytes). Keep it confidential; it contains account sessions.")
            return
        if name == "maintenance":
            require_admin(svc, user_id)
            removed = 0
            for path in svc.settings.temp_dir.glob("pair-*"):
                if path.is_file() and time.time() - path.stat().st_mtime > 3600:
                    path.unlink(missing_ok=True)
                    removed += 1
            await respond(update, f"Maintenance complete. Removed {removed} stale pairing files.")
            return
        await respond(update, "Unknown command. Use /help.")
    except (PermissionDenied, ValueError, RuntimeError) as exc:
        await respond(update, sanitize(exc, 250))
    except Exception as exc:
        svc.database.log_action(user_id, name, None, "error", sanitize(exc))
        await respond(update, "Operation failed. Check hoster logs for a sanitized diagnostic.")


async def pairing_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    svc = service(context)
    user_id = actor_id(update)
    message = update.effective_message
    if not message or not update.effective_chat or update.effective_chat.type != "private":
        return
    phase = svc.pairing.phase(user_id)
    if not phase:
        return
    if not is_privileged_admin(svc, user_id):
        await svc.pairing.cancel(user_id)
        await respond(update, "You are not authorized to continue pairing.")
        return
    if phase == "expired":
        try:
            await message.delete()
        except Exception:
            await svc.pairing.cancel(user_id)
            await respond(update, "Pairing expired; this input could not be securely removed. Delete it manually.")
            return
        await respond(update, "Pairing expired. Use /pair to start again.")
        return
    try:
        text = message.text or ""
        try:
            await message.delete()
        except Exception as exc:
            await svc.pairing.cancel(user_id)
            await respond(update, "Could not securely remove the pairing input; pairing was cancelled.")
            return
        if phase == "phone":
            response = await svc.pairing.submit_phone(user_id, text)
        elif phase == "code":
            response, account_id = await svc.pairing.submit_code(user_id, text)
            if account_id:
                try:
                    await svc.process_manager.start(account_id, user_id)
                    response += " Worker started."
                except Exception:
                    response += " The account is paired, but its worker did not start; use /startuser " + account_id + "."
        elif phase == "password":
            response = await svc.pairing.submit_password(user_id, text)
            account_id = response.split()[3].rstrip(".")
            try:
                await svc.process_manager.start(account_id, user_id)
                response += " Worker started."
            except Exception:
                response += " The account is paired, but its worker did not start; use /startuser " + account_id + "."
        else:
            return
        await respond(update, response)
    except (ValueError, RuntimeError) as exc:
        await respond(update, sanitize(exc, 240))
    except Exception:
        await svc.pairing.cancel(user_id)
        await respond(update, "Pairing failed and temporary authentication state was cleared.")


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    svc = service(context)
    query = update.callback_query
    user_id = actor_id(update)
    if not query:
        return
    await query.answer()
    data = query.data or ""
    try:
        if data == "noop":
            return
        if data == "panel:accounts":
            if not svc.database.get_role(user_id):
                raise PermissionDenied("You are not authorized.")
            await edit_callback_response(query, "Accounts:", reply_markup=account_keyboard(svc.database.accessible_accounts(user_id)))
            return
        if data == "panel:health":
            require_admin(svc, user_id)
            values = host_health(svc.settings.root, svc.settings.database_path)
            await edit_callback_response(query, "Host health\n" + "\n".join(f"{key}: {value}" for key, value in values.items()))
            return
        if data == "panel:pair":
            require_admin(svc, user_id)
            if query.message.chat.type != "private":
                raise PermissionDenied("Pairing is available only in a private chat.")
            await svc.pairing.begin(user_id)
            await edit_callback_response(query, "Send the account phone number in international format in this private chat.")
            return
        if data.startswith("account:"):
            account_id = data.split(":", 1)[1].upper()
            require_access(svc.database, user_id, "status", account_id)
            account = svc.database.get_account(account_id)
            if not account:
                raise ValueError("Account not found")
            await edit_callback_response(query, account_summary(account), reply_markup=actions_keyboard(account_id, account["worker_status"] == "running"))
            return
        if data.startswith("action:"):
            _, action, account_id = data.split(":", 2)
            result = await account_action(svc, user_id, action, account_id.upper())
            await edit_callback_response(query, result)
            return
        if data.startswith("confirm:") and len(data.split(":")) == 2:
            token = data.split(":", 1)[1]
            confirmation = svc.confirmations.get(token)
            if not confirmation:
                raise ValueError("Confirmation expired or already used.")
            initiator, operation, target, expires = confirmation
            if user_id != initiator or time.monotonic() > expires:
                raise PermissionDenied("This confirmation is not valid for you.")
            svc.confirmations.pop(token, None)
            if operation == "restartuser":
                result = await account_action(svc, user_id, "restart", target)
            elif operation == "unpair":
                require_admin(svc, user_id)
                await svc.process_manager.stop(target, user_id)
                account = svc.database.remove_account(user_id, target)
                for suffix in (".session", ".session-journal", ".session-wal", ".session-shm"):
                    (svc.settings.session_dir / f"{account['session_ref']}{suffix}").unlink(missing_ok=True)
                result = f"{target} unpaired; its session was removed."
            elif operation == "remsudo":
                require_owner(svc, user_id)
                svc.database.set_user_role(user_id, int(target), None)
                result = f"Admin {target} removed."
            else:
                raise ValueError("Unknown confirmation operation")
            await edit_callback_response(query, result)
            return
        if data.startswith("cancel:"):
            token = data.split(":", 1)[1]
            confirmation = svc.confirmations.get(token)
            if confirmation and confirmation[0] == user_id:
                svc.confirmations.pop(token, None)
                await edit_callback_response(query, "Cancelled.")
            else:
                raise PermissionDenied("This confirmation is not valid for you.")
            return
        raise ValueError("Unknown action")
    except (PermissionDenied, ValueError, RuntimeError) as exc:
        await edit_callback_response(query, sanitize(exc, 250))
    except Exception as exc:
        svc.database.log_action(user_id, "callback", None, "error", sanitize(exc))
        await edit_callback_response(query, "Operation failed. Check hoster logs.")


COMMANDS = (
    "start", "help", "panel", "accounts", "account", "status", "health", "stats", "version",
    "pair", "cancelpair", "select", "startuser", "stopuser", "restartuser", "unpair", "rename",
    "logs", "settings", "addsudo", "sudos", "remsudo", "assign", "unassign", "permissions",
    "addlimited", "audit", "backup", "maintenance",
)


def register_handlers(application: Application) -> None:
    for name in COMMANDS:
        application.add_handler(CommandHandler(name, command))
    application.add_handler(CallbackQueryHandler(callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, pairing_input))
