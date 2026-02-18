"""dreamloop-dash — real-time dashboard and visual reports for dreamloop."""

import json
import os
import secrets
import asyncio
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from fastapi.responses import HTMLResponse

app = FastAPI(title="dreamloop-dash")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

# --- Auth ---
security = HTTPBasic()
DASH_USER = os.environ.get("DASH_USER", "")
DASH_PASS = os.environ.get("DASH_PASS", "")


def verify(credentials: HTTPBasicCredentials = Depends(security)):
    if not DASH_USER or not DASH_PASS:
        return  # Auth disabled if env vars not set
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
}

connected_clients: list[WebSocket] = []


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
    await broadcast({"type": "pipeline_start", "data": state})
    return {"ok": True}


@app.post("/api/iteration/start")
async def iteration_start(request: Request):
    body = await request.json()
    num = body.get("number", state["current_iteration"] + 1)
    state["current_iteration"] = num
    state["current_step"] = "starting"
    iteration = {"number": num, "steps": {}, "started_at": datetime.utcnow().isoformat()}
    state["iterations"].append(iteration)
    await broadcast({"type": "iteration_start", "data": {"number": num}})
    return {"ok": True}


@app.post("/api/step/start")
async def step_start(request: Request):
    body = await request.json()
    step = body.get("step", "")
    state["current_step"] = step
    await broadcast({"type": "step_start", "data": {"iteration": state["current_iteration"], "step": step}})
    return {"ok": True}


@app.post("/api/step/complete")
async def step_complete(request: Request):
    body = await request.json()
    step = body.get("step", "")
    result = body.get("result", {})
    # Store in current iteration
    if state["iterations"]:
        state["iterations"][-1]["steps"][step] = result
    await broadcast({"type": "step_complete", "data": {"iteration": state["current_iteration"], "step": step, "result": result}})
    return {"ok": True}


@app.post("/api/pipeline/finish")
async def pipeline_finish(request: Request):
    body = await request.json()
    state["status"] = body.get("status", "finished")
    state["finished_at"] = datetime.utcnow().isoformat()
    state["summary"] = body.get("summary", {})
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
