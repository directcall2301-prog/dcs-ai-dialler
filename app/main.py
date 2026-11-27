import os, sqlite3, csv, io, json, secrets, hashlib, hmac, time, base64, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import FastAPI, Request, Form, Depends, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

DB=os.getenv("DCS_DATABASE_PATH","/var/data/dcs_dialler.sqlite3" if os.path.isdir("/var/data") else "dcs_dialler.sqlite3")
MODE=os.getenv("DCS_MODE","simulation").lower()
SECRET=os.getenv("DCS_SECRET_KEY") or secrets.token_urlsafe(48)
ADMIN_USER=os.getenv("DCS_ADMIN_USERNAME","admin")
ADMIN_PASS=os.getenv("DCS_ADMIN_PASSWORD","")
DISPOSITIONS=["SALE","QUALIFIED","CALLBACK","NO ANSWER","BUSY","VOICEMAIL","NOT INTERESTED","DNC","INVALID","WRONG NUMBER"]
app=FastAPI(title="DCS AI Dialler",version="4.0.0")
app.add_middleware(SessionMiddleware,secret_key=SECRET,same_site="lax",https_only=os.getenv("DCS_COOKIE_SECURE","true").lower()=="true")
app.mount("/static",StaticFiles(directory="static"),name="static")
_ari_worker_started=False

def now(): return datetime.now(timezone.utc).isoformat()
def db():
    folder=os.path.dirname(DB)
    if folder: os.makedirs(folder,exist_ok=True)
    c=sqlite3.connect(DB,timeout=30); c.row_factory=sqlite3.Row; c.execute("PRAGMA foreign_keys=ON"); return c
def q(sql,args=()):
    with db() as c: return [dict(x) for x in c.execute(sql,args).fetchall()]
def one(sql,args=()):
    a=q(sql,args); return a[0] if a else None
def write(sql,args=()):
    with db() as c:
        cur=c.execute(sql,args); c.commit(); return cur.lastrowid
def pw_hash(p,s=None):
    s=s or secrets.token_bytes(16)
    return s.hex()+":"+hashlib.pbkdf2_hmac("sha256",p.encode(),s,240000).hex()
def pw_ok(p,stored):
    try:
        salt,expected=stored.split(":")
        return hmac.compare_digest(hashlib.pbkdf2_hmac("sha256",p.encode(),bytes.fromhex(salt),240000).hex(),expected)
    except Exception: return False

def init():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,role TEXT NOT NULL DEFAULT 'agent',agent_id INTEGER);
        CREATE TABLE IF NOT EXISTS campaigns(id INTEGER PRIMARY KEY,name TEXT NOT NULL UNIQUE,description TEXT DEFAULT '',active INTEGER DEFAULT 1,dial_mode TEXT DEFAULT 'predictive',max_attempts INTEGER DEFAULT 4, pacing REAL DEFAULT 1.5,abandon_limit REAL DEFAULT 3,caller_id TEXT DEFAULT '',created_at TEXT);
        CREATE TABLE IF NOT EXISTS agents(id INTEGER PRIMARY KEY,name TEXT NOT NULL,username TEXT UNIQUE,status TEXT DEFAULT 'offline',campaign_id INTEGER,extension TEXT DEFAULT '',last_state_at TEXT,FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE SET NULL);
        CREATE TABLE IF NOT EXISTS leads(id INTEGER PRIMARY KEY,first_name TEXT DEFAULT '',last_name TEXT DEFAULT '',phone TEXT NOT NULL,email TEXT DEFAULT '',campaign_id INTEGER,status TEXT DEFAULT 'NEW',attempts INTEGER DEFAULT 0,consent_status TEXT DEFAULT 'UNVERIFIED',timezone TEXT DEFAULT 'Europe/London',next_callback TEXT,notes TEXT DEFAULT '',created_at TEXT,updated_at TEXT,FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE SET NULL);
        CREATE TABLE IF NOT EXISTS calls(id INTEGER PRIMARY KEY,lead_id INTEGER,campaign_id INTEGER,agent_id INTEGER,outcome TEXT,duration INTEGER DEFAULT 0,started_at TEXT,ended_at TEXT,notes TEXT DEFAULT '',call_mode TEXT DEFAULT 'SIMULATION',FOREIGN KEY(lead_id) REFERENCES leads(id) ON DELETE SET NULL);
        CREATE TABLE IF NOT EXISTS qualifications(id INTEGER PRIMARY KEY,lead_id INTEGER,campaign_id INTEGER,agent_id INTEGER,answers TEXT NOT NULL,qualified INTEGER DEFAULT 0,created_at TEXT,FOREIGN KEY(lead_id) REFERENCES leads(id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
        CREATE TABLE IF NOT EXISTS sip_trunks(id INTEGER PRIMARY KEY,name TEXT NOT NULL,host TEXT NOT NULL,port INTEGER DEFAULT 5060,transport TEXT DEFAULT 'udp',username TEXT DEFAULT '',secret_enc TEXT DEFAULT '',from_user TEXT DEFAULT '',from_domain TEXT DEFAULT '',context TEXT DEFAULT 'from-dcs',codecs TEXT DEFAULT 'ulaw,alaw',active INTEGER DEFAULT 1,created_at TEXT);
        CREATE TABLE IF NOT EXISTS sip_extensions(id INTEGER PRIMARY KEY,agent_id INTEGER UNIQUE,extension TEXT UNIQUE NOT NULL,secret_hash TEXT NOT NULL,secret_enc TEXT DEFAULT '',caller_id TEXT DEFAULT '',context TEXT DEFAULT 'from-dcs',enabled INTEGER DEFAULT 1,created_at TEXT,FOREIGN KEY(agent_id) REFERENCES agents(id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS telephony_calls(id INTEGER PRIMARY KEY,lead_id INTEGER,agent_id INTEGER,customer_channel TEXT,agent_channel TEXT,bridge_id TEXT,state TEXT DEFAULT 'created',created_at TEXT,updated_at TEXT,detail TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,user_id INTEGER,action TEXT,detail TEXT,created_at TEXT);
        CREATE INDEX IF NOT EXISTS idx_leads_queue ON leads(campaign_id,status,next_callback,attempts);
        CREATE INDEX IF NOT EXISTS idx_calls_date ON calls(started_at);
        """)
        cols=[r[1] for r in c.execute("PRAGMA table_info(sip_extensions)").fetchall()]
        if "secret_enc" not in cols: c.execute("ALTER TABLE sip_extensions ADD COLUMN secret_enc TEXT DEFAULT ''")
        if not c.execute("SELECT id FROM users WHERE username=?",(ADMIN_USER,)).fetchone():
            if ADMIN_PASS:
                c.execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,?)",(ADMIN_USER,pw_hash(ADMIN_PASS),"admin"))
            else:
                temp=secrets.token_urlsafe(15)
                c.execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,?)",(ADMIN_USER,pw_hash(temp),"admin"))
                print("DCS INITIAL ADMIN PASSWORD (save it and set DCS_ADMIN_PASSWORD):",temp)
        defaults={"dialler_paused":"1","max_concurrent_calls":"10","abandon_rate_limit":"3","predictive_enabled":"1","company_name":"Direct Call Solutions","timezone":"Africa/Johannesburg","recording_notice":"This call may be recorded for quality and training purposes.","asterisk_ari_url":"","asterisk_ari_user":"","asterisk_stasis_app":"dcs-dialler","asterisk_endpoint_prefix":"dcs"}
        for k,v in defaults.items(): c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)",(k,v))
        c.commit()
@app.on_event("startup")
def startup():
    init()
    start_ari_event_worker()

def current(req:Request):
    uid=req.session.get("uid")
    if not uid: raise HTTPException(401,"Login required")
    u=one("SELECT id,username,role,agent_id FROM users WHERE id=?",(uid,))
    if not u: raise HTTPException(401,"Session expired")
    return u
def admin(u=Depends(current)):
    if u["role"]!="admin": raise HTTPException(403,"Administrator permission required")
    return u
def audit(u,action,detail=""):
    write("INSERT INTO audit(user_id,action,detail,created_at) VALUES(?,?,?,?)",(u["id"],action,detail[:500],now()))
def setting(k,default=""):
    x=one("SELECT value FROM settings WHERE key=?",(k,)); return x["value"] if x else default
def set_setting(k,v): write("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(k,str(v)))
def lead_allowed(l):
    return l["consent_status"] not in ("DO_NOT_CALL","NO_CONSENT") and l["status"]!="DNC" and l["attempts"]<4

@app.get("/health")
def health(): return {"ok":True,"service":"DCS AI Dialler","version":"4.0.0","mode":MODE}
@app.get("/",response_class=HTMLResponse)
def home(req:Request):
    if not req.session.get("uid"): return RedirectResponse("/login",303)
    return HTMLResponse(open("static/index.html",encoding="utf-8").read())
@app.get("/login",response_class=HTMLResponse)
def login_page(): return HTMLResponse(open("static/login.html",encoding="utf-8").read())
@app.post("/login")
def login(req:Request,username:str=Form(...),password:str=Form(...)):
    u=one("SELECT * FROM users WHERE username=?",(username,))
    if not u or not pw_ok(password,u["password_hash"]):
        return HTMLResponse(open("static/login.html",encoding="utf-8").read().replace("{{ERROR}}","Invalid username or password"),401)
    req.session.clear(); req.session["uid"]=u["id"]; return RedirectResponse("/",303)
@app.post("/logout")
def logout(req:Request): req.session.clear(); return RedirectResponse("/login",303)
@app.get("/api/me")
def me(u=Depends(current)): return {k:u[k] for k in ("id","username","role","agent_id")}

@app.get("/api/overview")
def overview(u=Depends(current)):
    totals={k:one(sql)["n"] for k,sql in {
      "leads":"SELECT COUNT(*) n FROM leads","qualified":"SELECT COUNT(*) n FROM leads WHERE status IN ('QUALIFIED','SALE')",
      "callbacks":"SELECT COUNT(*) n FROM leads WHERE next_callback IS NOT NULL","calls_today":"SELECT COUNT(*) n FROM calls WHERE date(started_at)=date('now')",
      "agents_ready":"SELECT COUNT(*) n FROM agents WHERE status='ready'","agents_total":"SELECT COUNT(*) n FROM agents",
      "campaigns":"SELECT COUNT(*) n FROM campaigns WHERE active=1"}.items()}
    totals["paused"]=setting("dialler_paused","1")=="1"
    totals["campaign_rows"]=q("SELECT c.*,COUNT(DISTINCT l.id) lead_count,COUNT(DISTINCT ca.id) call_count FROM campaigns c LEFT JOIN leads l ON l.campaign_id=c.id LEFT JOIN calls ca ON ca.campaign_id=c.id GROUP BY c.id ORDER BY c.id DESC")
    totals["latest"]=q("SELECT l.id,l.first_name,l.last_name,l.phone,l.status,l.created_at,c.name campaign FROM leads l LEFT JOIN campaigns c ON c.id=l.campaign_id ORDER BY l.id DESC LIMIT 8")
    return totals

@app.get("/api/campaigns")
def campaigns(u=Depends(current)): return q("SELECT c.*,COUNT(l.id) lead_count FROM campaigns c LEFT JOIN leads l ON l.campaign_id=c.id GROUP BY c.id ORDER BY c.name")
@app.post("/api/campaigns")
def campaign_add(x:dict,u=Depends(admin)):
    name=(x.get("name") or "").strip()
    if not name: raise HTTPException(400,"Campaign name is required")
    try:
        i=write("INSERT INTO campaigns(name,description,active,dial_mode,max_attempts,pacing,abandon_limit,caller_id,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
          (name,x.get("description",""),1 if x.get("active",True) else 0,x.get("dial_mode","predictive"),int(x.get("max_attempts",4)),float(x.get("pacing",1.5)),float(x.get("abandon_limit",3)),x.get("caller_id",""),now()))
    except sqlite3.IntegrityError: raise HTTPException(409,"Campaign name already exists")
    audit(u,"campaign.create",name); return {"id":i}
@app.patch("/api/campaigns/{cid}")
def campaign_edit(cid:int,x:dict,u=Depends(admin)):
    allowed={"name","description","active","dial_mode","max_attempts","pacing","abandon_limit","caller_id"}
    f={k:v for k,v in x.items() if k in allowed}
    if not f: raise HTTPException(400,"No editable fields supplied")
    if "active" in f: f["active"]=1 if f["active"] else 0
    if "max_attempts" in f: f["max_attempts"]=int(f["max_attempts"])
    if "pacing" in f: f["pacing"]=float(f["pacing"])
    if "abandon_limit" in f: f["abandon_limit"]=float(f["abandon_limit"])
    write("UPDATE campaigns SET "+",".join(k+"=?" for k in f)+" WHERE id=?",tuple(f.values())+(cid,))
    audit(u,"campaign.update",str(cid)); return {"ok":True}
@app.delete("/api/campaigns/{cid}")
def campaign_delete(cid:int,u=Depends(admin)):
    if not one("SELECT id FROM campaigns WHERE id=?",(cid,)): raise HTTPException(404,"Campaign not found")
    write("UPDATE leads SET campaign_id=NULL,status='UNASSIGNED' WHERE campaign_id=?",(cid,))
    write("UPDATE agents SET campaign_id=NULL WHERE campaign_id=?",(cid,))
    write("DELETE FROM campaigns WHERE id=?",(cid,)); audit(u,"campaign.delete",str(cid)); return {"ok":True}

@app.get("/api/leads")
def leads(qs:str="",campaign_id:Optional[int]=None,status:Optional[str]=None,u=Depends(current)):
    sql="SELECT l.*,c.name campaign FROM leads l LEFT JOIN campaigns c ON c.id=l.campaign_id WHERE (l.first_name LIKE ? OR l.last_name LIKE ? OR l.phone LIKE ? OR l.email LIKE ?)"
    args=[f"%{qs}%"]*4
    if campaign_id: sql+=" AND l.campaign_id=?"; args.append(campaign_id)
    if status: sql+=" AND l.status=?"; args.append(status)
    return q(sql+" ORDER BY l.id DESC LIMIT 1000",args)
@app.post("/api/leads")
def lead_add(x:dict,u=Depends(current)):
    phone=(x.get("phone") or "").strip()
    if not phone: raise HTTPException(400,"Phone number is required")
    cid=x.get("campaign_id")
    if cid and not one("SELECT id FROM campaigns WHERE id=?",(cid,)): raise HTTPException(400,"Campaign not found")
    i=write("""INSERT INTO leads(first_name,last_name,phone,email,campaign_id,consent_status,timezone,notes,created_at,updated_at)
    VALUES(?,?,?,?,?,?,?,?,?,?)""",(x.get("first_name",""),x.get("last_name",""),phone,x.get("email",""),cid,x.get("consent_status","UNVERIFIED"),x.get("timezone","Europe/London"),x.get("notes",""),now(),now()))
    audit(u,"lead.create",str(i)); return {"id":i}
@app.patch("/api/leads/{lid}")
def lead_edit(lid:int,x:dict,u=Depends(current)):
    allowed={"first_name","last_name","phone","email","campaign_id","status","consent_status","timezone","next_callback","notes"}
    f={k:v for k,v in x.items() if k in allowed}
    if not f: raise HTTPException(400,"No editable fields supplied")
    if f.get("status")=="DNC": f["consent_status"]="DO_NOT_CALL"
    f["updated_at"]=now()
    write("UPDATE leads SET "+",".join(k+"=?" for k in f)+" WHERE id=?",tuple(f.values())+(lid,))
    audit(u,"lead.update",str(lid)); return {"ok":True}
@app.post("/api/leads/import")
async def lead_import(file:UploadFile=File(...),campaign_id:int=Form(...),u=Depends(current)):
    if not one("SELECT id FROM campaigns WHERE id=?",(campaign_id,)): raise HTTPException(400,"Campaign not found")
    raw=(await file.read()).decode("utf-8-sig")
    reader=csv.DictReader(io.StringIO(raw)); n=0
    for r in reader:
        phone=(r.get("phone") or r.get("Phone") or r.get("telephone") or "").strip()
        if phone:
            write("INSERT INTO leads(first_name,last_name,phone,email,campaign_id,consent_status,timezone,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
              (r.get("first_name",r.get("First Name","")),r.get("last_name",r.get("Last Name","")),phone,r.get("email",r.get("Email","")),campaign_id,r.get("consent_status","UNVERIFIED"),r.get("timezone","Europe/London"),now(),now())); n+=1
    audit(u,"lead.import",f"{n} rows"); return {"imported":n}
@app.get("/api/export/leads")
def lead_export(u=Depends(current)):
    rows=q("SELECT first_name,last_name,phone,email,campaign_id,status,attempts,consent_status,timezone,next_callback,notes FROM leads ORDER BY id")
    out=io.StringIO(); w=csv.DictWriter(out,fieldnames=list(rows[0]) if rows else ["first_name","last_name","phone","email","campaign_id","status","attempts","consent_status","timezone","next_callback","notes"]); w.writeheader(); w.writerows(rows)
    return StreamingResponse(iter([out.getvalue()]),media_type="text/csv",headers={"Content-Disposition":"attachment; filename=dcs_leads.csv"})

@app.get("/api/agents")
def agents(u=Depends(current)): return q("SELECT a.*,c.name campaign FROM agents a LEFT JOIN campaigns c ON c.id=a.campaign_id ORDER BY a.id")
@app.post("/api/agents")
def agent_add(x:dict,u=Depends(admin)):
    name=(x.get("name") or "").strip(); username=(x.get("username") or "").strip(); password=x.get("password") or ""
    if not name or not username or len(password)<10: raise HTTPException(400,"Name, username and password (10+ characters) are required")
    try:
        aid=write("INSERT INTO agents(name,username,status,campaign_id,extension,last_state_at) VALUES(?,?,'offline',?,?,?)",(name,username,x.get("campaign_id"),x.get("extension",""),now()))
        write("INSERT INTO users(username,password_hash,role,agent_id) VALUES(?,?,?,?)",(username,pw_hash(password),"agent",aid))
    except sqlite3.IntegrityError: raise HTTPException(409,"Agent username already exists")
    audit(u,"agent.create",username); return {"id":aid}
@app.patch("/api/agents/{aid}")
def agent_edit(aid:int,x:dict,u=Depends(current)):
    a=one("SELECT * FROM agents WHERE id=?",(aid,))
    if not a: raise HTTPException(404,"Agent not found")
    if u["role"]!="admin" and u.get("agent_id")!=aid: raise HTTPException(403,"Not your agent profile")
    status=x.get("status")
    if status:
        if status not in ("offline","ready","paused","on_call","wrap"): raise HTTPException(400,"Invalid agent state")
        write("UPDATE agents SET status=?,last_state_at=? WHERE id=?",(status,now(),aid))
    if u["role"]=="admin":
        if "campaign_id" in x: write("UPDATE agents SET campaign_id=? WHERE id=?",(x["campaign_id"],aid))
        if "extension" in x: write("UPDATE agents SET extension=? WHERE id=?",(x["extension"],aid))
    return {"ok":True}

@app.get("/api/dialler/status")
def dial_status(u=Depends(current)):
    ready=one("SELECT COUNT(*) n FROM agents WHERE status='ready'")["n"]
    active=one("SELECT COUNT(*) n FROM calls WHERE ended_at IS NULL")["n"]
    return {"mode":MODE,"paused":setting("dialler_paused","1")=="1","predictive_enabled":setting("predictive_enabled","1")=="1","ready_agents":ready,"active_calls":active,"max_concurrent":int(setting("max_concurrent_calls","10")),"asterisk_configured":bool(os.getenv("ASTERISK_ARI_URL") and os.getenv("ASTERISK_ARI_USER") and os.getenv("ASTERISK_ARI_PASSWORD"))}
@app.post("/api/dialler/control")
def dial_control(x:dict,u=Depends(admin)):
    action=x.get("action")
    if action not in ("pause","resume"): raise HTTPException(400,"Action must be pause or resume")
    set_setting("dialler_paused","1" if action=="pause" else "0"); audit(u,"dialler."+action)
    return {"paused":action=="pause"}
@app.get("/api/dialler/next")
def dial_next(campaign_id:int,u=Depends(current)):
    if setting("dialler_paused","1")=="1": raise HTTPException(409,"Dialler is paused")
    camp=one("SELECT * FROM campaigns WHERE id=? AND active=1",(campaign_id,))
    if not camp: raise HTTPException(404,"Active campaign not found")
    agent=one("SELECT * FROM agents WHERE status='ready' AND (campaign_id=? OR campaign_id IS NULL) ORDER BY id LIMIT 1",(campaign_id,))
    if not agent: raise HTTPException(409,"No ready agent is available")
    lead=one("""SELECT * FROM leads WHERE campaign_id=? AND status IN ('NEW','NO ANSWER','BUSY','VOICEMAIL','CALLBACK')
      AND consent_status='CONSENTED' AND attempts<? AND (next_callback IS NULL OR next_callback<=?)
      ORDER BY CASE WHEN next_callback IS NOT NULL THEN 0 ELSE 1 END,attempts, id LIMIT 1""",(campaign_id,camp["max_attempts"],now()))
    if not lead: raise HTTPException(404,"No eligible leads in queue")
    return {"lead":lead,"agent":{"id":agent["id"],"name":agent["name"]},"campaign":camp["name"],"call_mode":MODE.upper()}
@app.post("/api/calls")
def call_log(x:dict,u=Depends(current)):
    lid=int(x.get("lead_id",0)); outcome=x.get("outcome","NO ANSWER")
    if outcome not in DISPOSITIONS: raise HTTPException(400,"Invalid disposition")
    lead=one("SELECT * FROM leads WHERE id=?",(lid,))
    if not lead: raise HTTPException(404,"Lead not found")
    if lead["consent_status"]=="DO_NOT_CALL" or lead["status"]=="DNC": raise HTTPException(409,"Suppressed lead cannot be called")
    aid=x.get("agent_id") or u.get("agent_id")
    if aid and not one("SELECT id FROM agents WHERE id=?",(aid,)): raise HTTPException(400,"Agent not found")
    start=x.get("started_at") or now()
    duration=max(0,min(int(x.get("duration",0)),86400))
    cb=x.get("next_callback") if outcome=="CALLBACK" else None
    status="DNC" if outcome=="DNC" else outcome
    write("UPDATE leads SET attempts=attempts+1,status=?,next_callback=?,updated_at=? WHERE id=?",(status,cb,now(),lid))
    cid=write("INSERT INTO calls(lead_id,campaign_id,agent_id,outcome,duration,started_at,ended_at,notes,call_mode) VALUES(?,?,?,?,?,?,?,?,?)",(lid,lead["campaign_id"],aid,outcome,duration,start,now(),x.get("notes",""),"SIMULATION" if MODE=="simulation" else "MANUAL"))
    if aid: write("UPDATE agents SET status='wrap',last_state_at=? WHERE id=?",(now(),aid))
    audit(u,"call.log",f"call={cid}; lead={lid}; outcome={outcome}")
    return {"id":cid,"ok":True,"outcome":outcome}
@app.post("/api/qualifications")
def qualification(x:dict,u=Depends(current)):
    lid=int(x.get("lead_id",0)); lead=one("SELECT * FROM leads WHERE id=?",(lid,))
    if not lead: raise HTTPException(404,"Lead not found")
    answers=x.get("answers")
    if not isinstance(answers,dict): raise HTTPException(400,"answers must be an object")
    qualified=1 if x.get("qualified") else 0
    i=write("INSERT INTO qualifications(lead_id,campaign_id,agent_id,answers,qualified,created_at) VALUES(?,?,?,?,?,?)",(lid,lead["campaign_id"],x.get("agent_id") or u.get("agent_id"),json.dumps(answers),qualified,now()))
    if qualified: write("UPDATE leads SET status='QUALIFIED',updated_at=? WHERE id=?",(now(),lid))
    return {"id":i,"ok":True}
@app.get("/api/callbacks")
def callbacks(u=Depends(current)): return q("SELECT l.*,c.name campaign FROM leads l LEFT JOIN campaigns c ON c.id=l.campaign_id WHERE next_callback IS NOT NULL ORDER BY next_callback LIMIT 500")
@app.get("/api/reports")
def reports(u=Depends(current)):
    return {"dispositions":q("SELECT outcome,COUNT(*) total FROM calls GROUP BY outcome ORDER BY total DESC"),
      "campaigns":q("SELECT c.name,COUNT(ca.id) calls,SUM(CASE WHEN ca.outcome IN ('SALE','QUALIFIED') THEN 1 ELSE 0 END) qualified FROM campaigns c LEFT JOIN calls ca ON ca.campaign_id=c.id GROUP BY c.id"),
      "agents":q("SELECT a.name,a.status,COUNT(c.id) calls FROM agents a LEFT JOIN calls c ON c.agent_id=a.id GROUP BY a.id"),
      "daily":q("SELECT date(started_at) day,COUNT(*) calls FROM calls WHERE started_at>=date('now','-13 days') GROUP BY date(started_at) ORDER BY day")}
@app.get("/api/settings")
def settings_get(u=Depends(admin)):
    return {k:setting(k) for k in ("company_name","timezone","max_concurrent_calls","abandon_rate_limit","predictive_enabled","recording_notice","asterisk_ari_url","asterisk_ari_user","asterisk_stasis_app","asterisk_endpoint_prefix")}
@app.patch("/api/settings")
def settings_edit(x:dict,u=Depends(admin)):
    allowed={"company_name","timezone","max_concurrent_calls","abandon_rate_limit","predictive_enabled","recording_notice"}
    for k,v in x.items():
        if k in allowed: set_setting(k,v)
    audit(u,"settings.update","admin settings changed"); return {"ok":True}
@app.get("/api/system")
def system(u=Depends(current)):
    return {"version":"4.0.0","mode":MODE,"telephony":"Asterisk ARI integration available; configure credentials to connect","features":["campaign CRUD","predictive pacing engine","lead queue","DNC suppression","callbacks","agent states","qualification capture","call dispositions","CSV import/export","reports","audit log"]}
@app.get("/api/audit")
def audit_log(u=Depends(admin)): return q("SELECT a.created_at,u.username,a.action,a.detail FROM audit a LEFT JOIN users u ON u.id=a.user_id ORDER BY a.id DESC LIMIT 200")


def ari_request(method,path,payload=None):
    base=setting("asterisk_ari_url","").rstrip("/")
    user=setting("asterisk_ari_user",""); password=os.getenv("ASTERISK_ARI_PASSWORD","")
    if not base or not user or not password: raise HTTPException(409,"Asterisk ARI is not configured. Save the ARI URL and username, then set ASTERISK_ARI_PASSWORD as a server secret.")
    url=base+"/ari/"+path.lstrip("/")
    data=None if payload is None else urllib.parse.urlencode(payload).encode()
    req=urllib.request.Request(url,data=data,method=method,headers={"Authorization":"Basic "+base64.b64encode((user+":"+password).encode()).decode(),"Content-Type":"application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req,timeout=12) as r:
            raw=r.read().decode(); return json.loads(raw) if raw else {"ok":True}
    except urllib.error.HTTPError as e:
        raise HTTPException(e.code,"Asterisk ARI error: "+e.read().decode()[:300])
    except Exception as e: raise HTTPException(502,"Asterisk ARI connection failed: "+str(e)[:250])

def encryption_key(): return os.getenv("DCS_ENCRYPTION_KEY") or base64.urlsafe_b64encode(hashlib.sha256(SECRET.encode()).digest())
def secret_masked(x): return {k:v for k,v in x.items() if k!="secret_enc"}

@app.get("/api/telephony/status")
def telephony_status(u=Depends(current)):
    configured=bool(setting("asterisk_ari_url") and setting("asterisk_ari_user") and os.getenv("ASTERISK_ARI_PASSWORD"))
    out={"configured":configured,"trunks":q("SELECT id,name,host,port,transport,username,from_user,from_domain,context,codecs,active,created_at FROM sip_trunks ORDER BY id"),"extensions":q("SELECT e.id,e.agent_id,e.extension,e.caller_id,e.context,e.enabled,a.name agent_name FROM sip_extensions e LEFT JOIN agents a ON a.id=e.agent_id ORDER BY e.extension"),"active_calls":q("SELECT * FROM telephony_calls WHERE state NOT IN ('ended','failed') ORDER BY id DESC LIMIT 50"),"ari":"unknown"}
    if configured:
        try: ari_request("GET","asterisk/info"); out["ari"]="connected"
        except HTTPException as e: out["ari"]="error: "+str(e.detail)
    return out

@app.patch("/api/telephony/ari")
def telephony_ari(x:dict,u=Depends(admin)):
    for k in ("asterisk_ari_url","asterisk_ari_user","asterisk_stasis_app","asterisk_endpoint_prefix"):
        if k in x: set_setting(k,str(x[k]).strip())
    if "ari_password" in x:
        # Password is intentionally accepted only as a deployment secret; never persisted or returned.
        raise HTTPException(400,"Set ASTERISK_ARI_PASSWORD in your hosting provider's environment/secrets panel, not in the browser.")
    audit(u,"telephony.ari.update","ARI connection settings updated")
    return {"ok":True,"password_required":"ASTERISK_ARI_PASSWORD"}

@app.post("/api/telephony/trunks")
def trunk_add(x:dict,u=Depends(admin)):
    name=(x.get("name") or "").strip(); host=(x.get("host") or "").strip(); username=(x.get("username") or "").strip(); password=x.get("password") or ""
    if not name or not host: raise HTTPException(400,"Trunk name and SIP server host are required")
    # Credential is stored encrypted with a deployment-derived key; set DCS_ENCRYPTION_KEY for stable production deployments.
    from cryptography.fernet import Fernet
    enc=Fernet(encryption_key()).encrypt(password.encode()).decode() if password else ""
    try:
        tid=write("INSERT INTO sip_trunks(name,host,port,transport,username,secret_enc,from_user,from_domain,context,codecs,active,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(name,host,int(x.get("port",5060)),x.get("transport","udp"),username,enc,x.get("from_user",""),x.get("from_domain",host),x.get("context","from-dcs"),x.get("codecs","ulaw,alaw"),1 if x.get("active",True) else 0,now()))
    except sqlite3.IntegrityError: raise HTTPException(409,"Could not save SIP trunk")
    audit(u,"telephony.trunk.create",name); return {"id":tid,"ok":True}

@app.delete("/api/telephony/trunks/{tid}")
def trunk_delete(tid:int,u=Depends(admin)):
    write("DELETE FROM sip_trunks WHERE id=?",(tid,)); audit(u,"telephony.trunk.delete",str(tid)); return {"ok":True}

@app.post("/api/telephony/extensions")
def extension_create(x:dict,u=Depends(admin)):
    aid=int(x.get("agent_id",0)); ext=str(x.get("extension") or "").strip(); secret=x.get("secret") or secrets.token_urlsafe(18)
    if not one("SELECT id FROM agents WHERE id=?",(aid,)): raise HTTPException(404,"Agent not found")
    if not ext.isdigit() or len(ext)<3 or len(ext)>8: raise HTTPException(400,"Extension must be 3–8 digits")
    try:
        from cryptography.fernet import Fernet
        enc=Fernet(encryption_key()).encrypt(secret.encode()).decode()
        eid=write("INSERT INTO sip_extensions(agent_id,extension,secret_hash,secret_enc,caller_id,context,enabled,created_at) VALUES(?,?,?,?,?,?,1,?)",(aid,ext,pw_hash(secret),enc,x.get("caller_id", ""),x.get("context","from-dcs"),now()))
        write("UPDATE agents SET extension=? WHERE id=?",(ext,aid))
    except sqlite3.IntegrityError: raise HTTPException(409,"Agent already has an extension or extension number is in use")
    audit(u,"telephony.extension.create",f"agent={aid}; extension={ext}")
    return {"id":eid,"extension":ext,"secret":secret,"warning":"Copy this SIP secret now. It is not retrievable later."}

@app.delete("/api/telephony/extensions/{eid}")
def extension_delete(eid:int,u=Depends(admin)):
    e=one("SELECT * FROM sip_extensions WHERE id=?",(eid,))
    if not e: raise HTTPException(404,"Extension not found")
    write("DELETE FROM sip_extensions WHERE id=?",(eid,)); write("UPDATE agents SET extension='' WHERE id=?",(e["agent_id"],)); audit(u,"telephony.extension.delete",str(eid)); return {"ok":True}

@app.get("/api/telephony/asterisk-config")
def asterisk_config(u=Depends(admin)):
    trunks=q("SELECT * FROM sip_trunks WHERE active=1 ORDER BY id"); exts=q("SELECT e.*,a.name FROM sip_extensions e JOIN agents a ON a.id=e.agent_id WHERE e.enabled=1 ORDER BY e.extension")
    lines=["; Generated by DCS AI Dialler. Review with your SIP carrier before production.","[transport-udp]","type=transport","protocol=udp","bind=0.0.0.0:5060","", "[transport-tcp]", "type=transport", "protocol=tcp", "bind=0.0.0.0:5060", "", "[transport-tls]", "type=transport", "protocol=tls", "bind=0.0.0.0:5061", "cert_file=/etc/asterisk/keys/asterisk.crt", "priv_key_file=/etc/asterisk/keys/asterisk.key", "method=tlsv1_2", ""]
    for t in trunks:
        tag="trunk"+str(t["id"]); lines += [f"[{tag}]", "type=endpoint", "transport=transport-udp", f"context={t['context']}", "disallow=all"] + ["allow="+codec.strip() for codec in t['codecs'].split(',') if codec.strip()] + ["outbound_auth="+tag+"-auth", "aors="+tag, "rtp_symmetric=yes", "force_rport=yes", "rewrite_contact=yes"] + (["from_user="+t['from_user']] if t['from_user'] else []) + (["from_domain="+t['from_domain']] if t['from_domain'] else []) + ["", f"[{tag}-auth]", "type=auth", "auth_type=userpass", f"username={t['username']}", "password="+( __import__('cryptography.fernet',fromlist=['Fernet']).Fernet(encryption_key()).decrypt(t['secret_enc'].encode()).decode() if t['secret_enc'] else "CHANGE_ME"), "", f"[{tag}]", "type=aor", f"contact=sip:{t['host']}:{t['port']}", "", f"[{tag}-reg]", "type=registration", f"transport=transport-{t['transport']}", f"outbound_auth={tag}-auth", f"server_uri=sip:{t['host']}:{t['port']}", f"client_uri=sip:{t['username']}@{t['host']}", f"contact_user={t['from_user'] or t['username']}", "retry_interval=60", "", f"[{tag}-identify]", "type=identify", f"endpoint={tag}", f"match={t['host']}", ""]
    for e in exts:
        lines += [f"[{e['extension']}]", "type=endpoint", "context=from-dcs", "disallow=all", "allow=ulaw,alaw", "auth="+e["extension"]+"-auth", "aors="+e["extension"], "", f"[{e['extension']}-auth]", "type=auth", "auth_type=userpass", f"username={e['extension']}", "password="+(__import__('cryptography.fernet',fromlist=['Fernet']).Fernet(encryption_key()).decrypt(e['secret_enc'].encode()).decode() if e['secret_enc'] else "CHANGE_ME"), "", f"[{e['extension']}]", "type=aor", "max_contacts=1", "remove_existing=yes", ""]
    return {"filename":"pjsip_dcs_generated.conf","config":"\n".join(lines),"extensions":len(exts),"trunks":len(trunks),"note":"Secrets are intentionally excluded. Use generated extension secrets from creation response and carrier credentials directly on the PBX."}

@app.post("/api/telephony/calls/originate")
def telephony_originate(x:dict,u=Depends(current)):
    if MODE!="live": raise HTTPException(409,"Set DCS_MODE=live only after Asterisk, trunk, legal calling controls and agent registration are validated.")
    lid=int(x.get("lead_id",0)); aid=int(x.get("agent_id") or u.get("agent_id") or 0)
    lead=one("SELECT * FROM leads WHERE id=?",(lid,)); agent=one("SELECT a.*,e.extension FROM agents a JOIN sip_extensions e ON e.agent_id=a.id WHERE a.id=? AND a.status='ready' AND e.enabled=1",(aid,))
    if not lead: raise HTTPException(404,"Lead not found")
    if not lead_allowed(lead) or lead["consent_status"]!="CONSENTED": raise HTTPException(409,"Lead is not eligible: consent, DNC or attempt-limit check failed")
    if not agent: raise HTTPException(409,"Agent must be ready and have an enabled SIP extension")
    appname=setting("asterisk_stasis_app","dcs-dialler")
    cid=write("INSERT INTO telephony_calls(lead_id,agent_id,state,created_at,updated_at) VALUES(?,?,'originating',?,?)",(lid,aid,now(),now()))
    # Agent channel is originated first; dialler application must answer/bridge via Stasis event handler.
    agent_ch=ari_request("POST","channels",{"endpoint":"PJSIP/"+agent["extension"],"app":appname,"appArgs":f"agent,{cid}","callerId":lead["phone"]})
    write("UPDATE telephony_calls SET agent_channel=?,state='agent_ringing',updated_at=? WHERE id=?",(agent_ch.get("id"),now(),cid))
    audit(u,"telephony.call.originate",f"call={cid}; lead={lid}; agent={aid}")
    return {"id":cid,"agent_channel":agent_ch.get("id"),"state":"agent_ringing","message":"Agent leg originated. A Stasis event worker must originate the customer leg and bridge both channels after the agent answers."}

@app.post("/api/telephony/calls/{call_id}/customer")
def telephony_customer_leg(call_id:int,x:dict,u=Depends(admin)):
    c=one("SELECT * FROM telephony_calls WHERE id=?",(call_id,))
    if not c or not c["agent_channel"]: raise HTTPException(404,"Call or agent channel not found")
    lead=one("SELECT * FROM leads WHERE id=?",(c["lead_id"],)); agent=one("SELECT e.extension FROM sip_extensions e WHERE e.agent_id=? AND e.enabled=1",(c["agent_id"],))
    if not lead or not agent: raise HTTPException(409,"Call data or agent extension missing")
    trunk=one("SELECT id FROM sip_trunks WHERE active=1 ORDER BY id LIMIT 1")
    if not trunk: raise HTTPException(409,"No active SIP trunk profile exists")
    ch=ari_request("POST","channels",{"endpoint":"PJSIP/"+lead["phone"]+"@trunk"+str(trunk["id"]),"app":setting("asterisk_stasis_app","dcs-dialler"),"appArgs":f"customer,{call_id}","callerId":agent["extension"]})
    write("UPDATE telephony_calls SET customer_channel=?,state='customer_ringing',updated_at=? WHERE id=?",(ch.get("id"),now(),call_id)); return {"customer_channel":ch.get("id"),"state":"customer_ringing"}

@app.post("/api/telephony/calls/{call_id}/bridge")
def telephony_bridge(call_id:int,u=Depends(admin)):
    c=one("SELECT * FROM telephony_calls WHERE id=?",(call_id,))
    if not c or not c["agent_channel"] or not c["customer_channel"]: raise HTTPException(409,"Both agent and customer channels must exist before bridging")
    b=ari_request("POST","bridges",{"type":"mixing","name":"dcs-"+str(call_id)})
    bid=b.get("id"); ari_request("POST",f"bridges/{bid}/addChannel",{"channel":[c["agent_channel"],c["customer_channel"]]})
    write("UPDATE telephony_calls SET bridge_id=?,state='bridged',updated_at=? WHERE id=?",(bid,now(),call_id)); write("UPDATE agents SET status='on_call',last_state_at=? WHERE id=?",(now(),c["agent_id"]))
    return {"bridge_id":bid,"state":"bridged"}

@app.post("/api/telephony/calls/{call_id}/hangup")
def telephony_hangup(call_id:int,u=Depends(current)):
    c=one("SELECT * FROM telephony_calls WHERE id=?",(call_id,))
    if not c: raise HTTPException(404,"Call not found")
    for ch in (c["customer_channel"],c["agent_channel"]):
        if ch:
            try: ari_request("DELETE",f"channels/{ch}")
            except HTTPException: pass
    if c["bridge_id"]:
        try: ari_request("DELETE",f"bridges/{c['bridge_id']}")
        except HTTPException: pass
    write("UPDATE telephony_calls SET state='ended',updated_at=? WHERE id=?",(now(),call_id)); write("UPDATE agents SET status='wrap',last_state_at=? WHERE id=?",(now(),c["agent_id"]))
    return {"ok":True,"state":"ended"}


def start_ari_event_worker():
    """Reconnectable ARI Stasis event worker: answer agent, dial customer, bridge answered legs."""
    global _ari_worker_started
    if _ari_worker_started or MODE!="live": return
    _ari_worker_started=True
    def loop():
        try:
            import websocket
        except Exception as e:
            print("DCS ARI worker unavailable; install websocket-client:",e); return
        while True:
            base=setting("asterisk_ari_url","").rstrip("/"); user=setting("asterisk_ari_user",""); password=os.getenv("ASTERISK_ARI_PASSWORD","")
            if not (base and user and password): time.sleep(10); continue
            try:
                from urllib.parse import urlparse, urlencode
                u=urlparse(base); scheme="wss" if u.scheme=="https" else "ws"; appname=setting("asterisk_stasis_app","dcs-dialler")
                url=f"{scheme}://{u.netloc}/ari/events?{urlencode({'app':appname,'api_key':user+':'+password,'subscribeAll':'true'})}"
                ws=websocket.create_connection(url,timeout=20)
                while True:
                    msg=ws.recv()
                    if not msg: continue
                    ev=json.loads(msg); typ=ev.get("type"); ch=ev.get("channel",{}); chid=ch.get("id")
                    if typ=="StasisStart" and chid:
                        args=ev.get("args",[]); role=args[0] if args else ""; call_id=int(args[1]) if len(args)>1 and str(args[1]).isdigit() else 0
                        call=one("SELECT * FROM telephony_calls WHERE id=?",(call_id,)) if call_id else None
                        if not call: continue
                        if role=="agent":
                            write("UPDATE telephony_calls SET agent_channel=?,state='agent_answering',updated_at=? WHERE id=?",(chid,now(),call_id))
                            ari_request("POST",f"channels/{chid}/answer")
                            lead=one("SELECT * FROM leads WHERE id=?",(call["lead_id"],)); trunk=one("SELECT id FROM sip_trunks WHERE active=1 ORDER BY id LIMIT 1")
                            if not lead or not trunk: continue
                            agent=one("SELECT e.extension FROM sip_extensions e WHERE e.agent_id=? AND e.enabled=1",(call["agent_id"],))
                            if not agent: continue
                            customer=ari_request("POST","channels",{"endpoint":"PJSIP/"+lead["phone"]+"@trunk"+str(trunk["id"]),"app":appname,"appArgs":f"customer,{call_id}","callerId":agent["extension"]})
                            write("UPDATE telephony_calls SET customer_channel=?,state='customer_ringing',updated_at=? WHERE id=?",(customer.get("id"),now(),call_id))
                        elif role=="customer":
                            ari_request("POST",f"channels/{chid}/answer")
                            fresh=one("SELECT * FROM telephony_calls WHERE id=?",(call_id,))
                            if fresh and fresh["agent_channel"]:
                                bridge=ari_request("POST","bridges",{"type":"mixing","name":"dcs-"+str(call_id)})
                                bid=bridge.get("id"); ari_request("POST",f"bridges/{bid}/addChannel",{"channel":[fresh["agent_channel"],chid]})
                                write("UPDATE telephony_calls SET bridge_id=?,state='bridged',updated_at=? WHERE id=?",(bid,now(),call_id))
                                write("UPDATE agents SET status='on_call',last_state_at=? WHERE id=?",(now(),fresh["agent_id"]))
                    elif typ=="ChannelDestroyed" and chid:
                        c=one("SELECT * FROM telephony_calls WHERE (agent_channel=? OR customer_channel=?) AND state NOT IN ('ended','failed') ORDER BY id DESC LIMIT 1",(chid,chid))
                        if c:
                            other=c["customer_channel"] if c["agent_channel"]==chid else c["agent_channel"]
                            if other:
                                try: ari_request("DELETE",f"channels/{other}")
                                except Exception: pass
                            if c["bridge_id"]:
                                try: ari_request("DELETE",f"bridges/{c['bridge_id']}")
                                except Exception: pass
                            write("UPDATE telephony_calls SET state='ended',updated_at=? WHERE id=?",(now(),c["id"]))
                            write("UPDATE agents SET status='wrap',last_state_at=? WHERE id=?",(now(),c["agent_id"]))
                
            except Exception as e:
                print("DCS ARI event worker reconnecting:",str(e)[:200]); time.sleep(5)
    threading.Thread(target=loop,name="dcs-ari-events",daemon=True).start()

@app.get("/login-check")
def login_check(): return {"ok":True}
