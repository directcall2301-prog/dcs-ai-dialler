from __future__ import annotations

import os
import sqlite3
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
import base64
import hmac
from pydantic import BaseModel, Field

DB_PATH = os.getenv("DATABASE_PATH", "./dcs_dialler.db")
MAX_ACTIVE_AGENTS = int(os.getenv("MAX_ACTIVE_AGENTS", "10"))
DIAL_MODE = os.getenv("DIAL_MODE", "simulation").lower()

app = FastAPI(
    title="DCS AI Dialler API",
    version="0.1.0",
    description="Direct Call Solutions-owned dialler foundation. Simulation only; no real calls are placed.",
)


@app.middleware("http")
async def cloud_basic_auth(request: Request, call_next):
    """Protect the cloud demo with HTTP Basic Auth. Health checks remain public."""
    if request.url.path == "/health":
        return await call_next(request)

    expected_user = os.getenv("DCS_ADMIN_USER", "")
    expected_password = os.getenv("DCS_ADMIN_PASSWORD", "")
    production = os.getenv("APP_ENV", "development").lower() == "production"

    # Fail closed in production if credentials were not configured. Local development
    # remains convenient without credentials.
    if not expected_user or not expected_password:
        if production:
            return PlainTextResponse("Cloud login is not configured. Contact the administrator.", status_code=503)
        return await call_next(request)

    auth = request.headers.get("authorization", "")
    try:
        scheme, token = auth.split(" ", 1)
        if scheme.lower() != "basic":
            raise ValueError("Unsupported authentication scheme")
        decoded = base64.b64decode(token, validate=True).decode("utf-8")
        supplied_user, supplied_password = decoded.split(":", 1)
    except Exception:
        supplied_user, supplied_password = "", ""

    user_ok = hmac.compare_digest(supplied_user.encode(), expected_user.encode())
    password_ok = hmac.compare_digest(supplied_password.encode(), expected_password.encode())
    if not (user_ok and password_ok):
        return PlainTextResponse(
            "Authentication required", status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="DCS AI Dialler"'},
        )
    return await call_next(request)

CAMPAIGNS = [
    ("DCS-LIFE", "UK Life Insurance"),
    ("DCS-ENERGY", "UK Energy"),
    ("DCS-BROADBAND", "UK Broadband"),
    ("DCS-MOBILE", "UK Mobile SIMs"),
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS campaigns (
            code TEXT PRIMARY KEY, name TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS agents (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'offline', active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS leads (
            id TEXT PRIMARY KEY, campaign_code TEXT NOT NULL, first_name TEXT NOT NULL,
            last_name TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL,
            email TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT 'manual',
            status TEXT NOT NULL DEFAULT 'new', callback_at TEXT,
            qualification_json TEXT NOT NULL DEFAULT '{}',
            consent_type TEXT NOT NULL DEFAULT 'unknown', consent_text_version TEXT NOT NULL DEFAULT '',
            consent_timestamp TEXT, consent_source TEXT NOT NULL DEFAULT '',
            tps_screened_at TEXT, ctps_screened_at TEXT,
            suppressed INTEGER NOT NULL DEFAULT 0, suppression_reason TEXT NOT NULL DEFAULT '',
            calling_window_ok INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY(campaign_code) REFERENCES campaigns(code)
        );
        CREATE TABLE IF NOT EXISTS audit_events (
            id TEXT PRIMARY KEY, event_type TEXT NOT NULL, entity_id TEXT NOT NULL,
            details_json TEXT NOT NULL, created_at TEXT NOT NULL
        );
        """)
        for code, name in CAMPAIGNS:
            conn.execute("INSERT OR IGNORE INTO campaigns(code,name) VALUES(?,?)", (code, name))


@app.on_event("startup")
def startup() -> None:
    init_db()


class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=200)


class AgentStatus(BaseModel):
    status: str = Field(pattern="^(offline|available|on_call|break|wrap)$")


class LeadCreate(BaseModel):
    campaign_code: str
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = ""
    phone: str = Field(min_length=6, max_length=30)
    email: str = ""
    source: str = "manual"
    consent_type: str = "unknown"  # unknown | live_call | automated_call | requested_callback
    consent_text_version: str = ""
    consent_timestamp: str | None = None
    consent_source: str = ""
    tps_screened_at: str | None = None
    ctps_screened_at: str | None = None
    suppressed: bool = False
    suppression_reason: str = ""
    calling_window_ok: bool = False


class Qualification(BaseModel):
    answers: dict[str, Any] = Field(default_factory=dict)
    callback_at: str | None = None
    customer_requested_callback: bool = False
    notes: str = ""


class DiallerPreview(BaseModel):
    campaign_code: str | None = None
    limit: int = Field(default=20, ge=1, le=100)


def audit(event_type: str, entity_id: str, details: dict[str, Any]) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO audit_events VALUES(?,?,?,?,?)",
            (str(uuid4()), event_type, entity_id, json.dumps(details), now()),
        )


@app.get("/", include_in_schema=False)
def dashboard():
    return FileResponse(Path(__file__).resolve().parent.parent / "static" / "index.html")


@app.get("/health")
def health():
    return {"status": "ok", "product": "DCS AI Dialler", "version": app.version, "dial_mode": DIAL_MODE}


@app.get("/campaigns")
def list_campaigns():
    with db() as conn:
        rows = conn.execute("SELECT code,name,active FROM campaigns ORDER BY code").fetchall()
    return [dict(r) for r in rows]


@app.post("/agents", status_code=201)
def create_agent(payload: AgentCreate):
    with db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM agents WHERE active=1").fetchone()[0]
        if count >= MAX_ACTIVE_AGENTS:
            raise HTTPException(409, f"Initial capacity is {MAX_ACTIVE_AGENTS} active agents")
        agent_id = str(uuid4())
        try:
            conn.execute("INSERT INTO agents(id,name,email,status,active,created_at) VALUES(?,?,?,'offline',1,?)",
                         (agent_id, payload.name, payload.email.lower(), now()))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "An agent with this email already exists")
    audit("agent.created", agent_id, payload.model_dump())
    return {"id": agent_id, **payload.model_dump(), "status": "offline", "active": True}


@app.get("/agents")
def list_agents():
    with db() as conn:
        rows = conn.execute("SELECT id,name,email,status,active,created_at FROM agents ORDER BY name").fetchall()
    return [dict(r) for r in rows]


@app.patch("/agents/{agent_id}/status")
def set_agent_status(agent_id: str, payload: AgentStatus):
    with db() as conn:
        cur = conn.execute("UPDATE agents SET status=? WHERE id=? AND active=1", (payload.status, agent_id))
        if cur.rowcount == 0:
            raise HTTPException(404, "Active agent not found")
    audit("agent.status_changed", agent_id, payload.model_dump())
    return {"id": agent_id, "status": payload.status}


@app.post("/leads", status_code=201)
def create_lead(payload: LeadCreate):
    with db() as conn:
        campaign = conn.execute("SELECT code FROM campaigns WHERE code=? AND active=1", (payload.campaign_code,)).fetchone()
        if not campaign:
            raise HTTPException(400, "Unknown or inactive campaign")
        lead_id = str(uuid4())
        data = payload.model_dump()
        conn.execute("""INSERT INTO leads(
            id,campaign_code,first_name,last_name,phone,email,source,status,
            consent_type,consent_text_version,consent_timestamp,consent_source,
            tps_screened_at,ctps_screened_at,suppressed,suppression_reason,calling_window_ok,created_at
        ) VALUES(?,?,?,?,?,?,?,'new',?,?,?,?,?,?,?,?,?,?)""",
        (lead_id, data["campaign_code"], data["first_name"], data["last_name"], data["phone"],
         data["email"], data["source"], data["consent_type"], data["consent_text_version"],
         data["consent_timestamp"], data["consent_source"], data["tps_screened_at"],
         data["ctps_screened_at"], int(data["suppressed"]), data["suppression_reason"],
         int(data["calling_window_ok"]), now()))
    audit("lead.created", lead_id, {"campaign_code": payload.campaign_code, "source": payload.source})
    return {"id": lead_id, "status": "new", "campaign_code": payload.campaign_code}


@app.get("/leads")
def list_leads(campaign_code: str | None = None, status: str | None = None, limit: int = 100):
    limit = max(1, min(limit, 500))
    sql = "SELECT * FROM leads WHERE 1=1"
    args: list[Any] = []
    if campaign_code:
        sql += " AND campaign_code=?"; args.append(campaign_code)
    if status:
        sql += " AND status=?"; args.append(status)
    sql += " ORDER BY created_at DESC LIMIT ?"; args.append(limit)
    with db() as conn:
        rows = conn.execute(sql, args).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["qualification"] = json.loads(item.pop("qualification_json"))
        item["suppressed"] = bool(item["suppressed"])
        item["calling_window_ok"] = bool(item["calling_window_ok"])
        result.append(item)
    return result


@app.post("/leads/{lead_id}/qualify")
def qualify_lead(lead_id: str, payload: Qualification):
    with db() as conn:
        row = conn.execute("SELECT id FROM leads WHERE id=?", (lead_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Lead not found")
        conn.execute("UPDATE leads SET status='qualified', callback_at=?, qualification_json=? WHERE id=?",
                     (payload.callback_at, json.dumps({"answers": payload.answers, "notes": payload.notes,
                      "customer_requested_callback": payload.customer_requested_callback}), lead_id))
    audit("lead.qualified", lead_id, payload.model_dump())
    return {"id": lead_id, "status": "qualified", "callback_at": payload.callback_at}


@app.post("/dialler/preview")
def dialler_preview(payload: DiallerPreview):
    """Returns a safe preview only. It never originates calls."""
    sql = "SELECT id,campaign_code,first_name,last_name,phone,status,consent_type,tps_screened_at,ctps_screened_at,suppressed,calling_window_ok FROM leads WHERE status IN ('new','qualified') AND suppressed=0 AND calling_window_ok=1 AND tps_screened_at IS NOT NULL"
    args: list[Any] = []
    if payload.campaign_code:
        sql += " AND campaign_code=?"; args.append(payload.campaign_code)
    sql += " ORDER BY CASE status WHEN 'qualified' THEN 0 ELSE 1 END, created_at ASC LIMIT ?"
    args.append(payload.limit)
    with db() as conn:
        rows = conn.execute(sql, args).fetchall()
        available_agents = conn.execute("SELECT COUNT(*) FROM agents WHERE active=1 AND status='available'").fetchone()[0]
    eligible = []
    blocked_count = conn.execute("SELECT COUNT(*) FROM leads WHERE suppressed=1 OR calling_window_ok=0 OR tps_screened_at IS NULL").fetchone()[0] if False else None
    for r in rows:
        item = dict(r)
        # This is a demonstration gate, not a legal determination. Automated voice marketing
        # needs specific consent; campaign owners must configure and validate requirements.
        if item["consent_type"] not in ("live_call", "requested_callback", "automated_call"):
            continue
        eligible.append({"lead_id": item["id"], "campaign_code": item["campaign_code"],
                         "customer": f"{item['first_name']} {item['last_name']}".strip(),
                         "status": item["status"], "queue_action": "would_queue_for_agent"})
    return {"mode": "simulation_only", "real_calls_placed": 0,
            "available_agents": available_agents, "eligible_preview": eligible,
            "preview_count": len(eligible),
            "note": "No calls are placed. Production requires a telephony adapter and approved compliance controls."}


@app.get("/dashboard/summary")
def dashboard_summary():
    with db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        qualified = conn.execute("SELECT COUNT(*) FROM leads WHERE status='qualified'").fetchone()[0]
        callbacks = conn.execute("SELECT COUNT(*) FROM leads WHERE callback_at IS NOT NULL").fetchone()[0]
        agents = conn.execute("SELECT COUNT(*) FROM agents WHERE active=1").fetchone()[0]
        available = conn.execute("SELECT COUNT(*) FROM agents WHERE active=1 AND status='available'").fetchone()[0]
        by_campaign = conn.execute("SELECT campaign_code,COUNT(*) AS leads FROM leads GROUP BY campaign_code").fetchall()
    return {"agents_active": agents, "agents_available": available, "lead_count": total,
            "qualified_count": qualified, "callback_count": callbacks,
            "campaign_leads": [dict(r) for r in by_campaign], "dial_mode": DIAL_MODE}
