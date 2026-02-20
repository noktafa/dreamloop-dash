# dreamloop-dash

Real-time dashboard for the [dreamloop](https://github.com/noktafa/dreamloop) pipeline. Shows live pipeline progress, findings, fixes, and convergence charts via WebSocket.

## Setup

```bash
pip install -r requirements.txt
```

## Usage

```bash
uvicorn app:app --host 0.0.0.0 --port 8500
```

Then point dreamloop at `http://localhost:8500` (the default).

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DASH_USER` | No | HTTP Basic Auth username (auth disabled if unset) |
| `DASH_PASS` | No | HTTP Basic Auth password (auth disabled if unset) |

## Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /` | Dashboard UI |
| `GET /report` | Final report view |
| `GET /api/state` | Current pipeline state (JSON) |
| `WS /ws` | WebSocket for live updates |
| `POST /api/pipeline/start` | Called by dreamloop |
| `POST /api/iteration/start` | Called by dreamloop |
| `POST /api/step/start` | Called by dreamloop |
| `POST /api/step/complete` | Called by dreamloop |
| `POST /api/pipeline/finish` | Called by dreamloop |
