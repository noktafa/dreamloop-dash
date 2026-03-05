"""dreamloop-dash — real-time dashboard and visual reports for dreamloop."""

import json
import os
import secrets
import asyncio
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from fastapi.responses import HTMLResponse

app = FastAPI(title="dreamloop-dash")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
STATE_FILE = Path(__file__).parent / "state.json"

# --- Auth ---
DASH_USER = os.environ.get("DASH_USER", "")
DASH_PASS = os.environ.get("DASH_PASS", "")
_auth_enabled = bool(DASH_USER and DASH_PASS)
security = HTTPBasic(auto_error=False)


def verify(credentials: HTTPBasicCredentials | None = Depends(security)):
    if not _auth_enabled:
        return  # Auth disabled if env vars not set
    if not credentials:
        raise HTTPException(status_code=401, detail="Unauthorized", headers={"WWW-Authenticate": "Basic"})
    user_ok = secrets.compare_digest(credentials.username.encode(), DASH_USER.encode())
    pass_ok = secrets.compare_digest(credentials.password.encode(), DASH_PASS.encode())
    if not (user_ok and pass_ok):
        raise HTTPException(status_code=401, detail="Unauthorized", headers={"WWW-Authenticate": "Basic"})


# In-memory state
state = {
    "status": "idle",           # idle | running | converged | max_reached
    "current_iteration": 0,
    "current_step": "",
    "iterations": [],
    "started_at": None,
    "finished_at": None,
    "servers": {},
    "step_timings": {},
    "safety_summary": None,
    "tool_calls": [],
    "mode": None,
    "step_labels": None,
}

connected_clients: list[WebSocket] = []


def _save_state():
    try:
        STATE_FILE.write_text(json.dumps(state, default=str))
    except Exception:
        pass


def _load_state():
    try:
        if STATE_FILE.exists():
            saved = json.loads(STATE_FILE.read_text())
            state.update(saved)
    except Exception:
        pass


_load_state()


async def broadcast(message: dict) -> None:
    dead = []
    for ws in connected_clients:
        try:
            await ws.send_json(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        connected_clients.remove(ws)


# --- WebSocket for live updates ---

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    connected_clients.append(ws)
    # Send current state on connect
    await ws.send_json({"type": "state", "data": state})
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        connected_clients.remove(ws)


# --- API endpoints (called by dreamloop pipeline) ---

@app.post("/api/pipeline/start")
async def pipeline_start(request: Request):
    body = await request.json()
    state["status"] = "running"
    state["current_iteration"] = 0
    state["current_step"] = ""
    state["iterations"] = []
    state["started_at"] = datetime.utcnow().isoformat()
    state["finished_at"] = None
    state["max_iterations"] = body.get("max_iterations", 5)
    state["servers"] = body.get("servers", {})
    state["step_timings"] = {}
    state["mode"] = body.get("mode")
    state["step_labels"] = body.get("step_labels")
    state["safety_summary"] = None
    state["tool_calls"] = []
    _save_state()
    await broadcast({"type": "pipeline_start", "data": state})
    return {"ok": True}


@app.post("/api/iteration/start")
async def iteration_start(request: Request):
    body = await request.json()
    num = body.get("number", state["current_iteration"] + 1)
    state["current_iteration"] = num
    state["current_step"] = "starting"
    state["step_timings"] = {}
    iteration = {"number": num, "steps": {}, "started_at": datetime.utcnow().isoformat()}
    state["iterations"].append(iteration)
    await broadcast({"type": "iteration_start", "data": {"number": num}})
    return {"ok": True}


@app.post("/api/step/start")
async def step_start(request: Request):
    body = await request.json()
    step = body.get("step", "")
    state["current_step"] = step
    state["step_timings"][step] = {"started_at": body.get("started_at", datetime.utcnow().isoformat())}
    await broadcast({"type": "step_start", "data": {"iteration": state["current_iteration"], "step": step}})
    return {"ok": True}


@app.post("/api/step/complete")
async def step_complete(request: Request):
    body = await request.json()
    step = body.get("step", "")
    result = body.get("result", {})
    elapsed = body.get("elapsed_seconds", 0)
    safety = body.get("safety_summary")

    # Store timing
    if step in state["step_timings"]:
        state["step_timings"][step]["elapsed_seconds"] = elapsed
    else:
        state["step_timings"][step] = {"elapsed_seconds": elapsed}

    # Store safety summary
    if safety:
        state["safety_summary"] = safety

    # Store in current iteration
    if state["iterations"]:
        state["iterations"][-1]["steps"][step] = result
        state["iterations"][-1]["step_timings"] = dict(state["step_timings"])
        if safety:
            state["iterations"][-1]["safety_summary"] = safety

    _save_state()
    await broadcast({"type": "step_complete", "data": {
        "iteration": state["current_iteration"],
        "step": step,
        "result": result,
        "elapsed_seconds": elapsed,
        "safety_summary": safety,
    }})
    return {"ok": True}


@app.post("/api/tool_call")
async def tool_call(request: Request):
    body = await request.json()
    state["tool_calls"].append(body)
    # Cap at 200 entries
    if len(state["tool_calls"]) > 200:
        state["tool_calls"] = state["tool_calls"][-200:]
    _save_state()
    await broadcast({"type": "tool_call", "data": body})
    return {"ok": True}


@app.post("/api/pipeline/finish")
async def pipeline_finish(request: Request):
    body = await request.json()
    state["status"] = body.get("status", "finished")
    state["finished_at"] = datetime.utcnow().isoformat()
    state["summary"] = body.get("summary", {})
    _save_state()
    await broadcast({"type": "pipeline_finish", "data": state})
    return {"ok": True}


# --- Dashboard pages ---

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, _=Depends(verify)):
    return templates.TemplateResponse("dashboard.html", {"request": request})


@app.get("/report", response_class=HTMLResponse)
async def report(request: Request, _=Depends(verify)):
    return templates.TemplateResponse("report.html", {"request": request, "state": json.dumps(state)})


@app.get("/api/state")
async def get_state(_=Depends(verify)):
    return state
