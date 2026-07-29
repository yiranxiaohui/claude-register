"""FastAPI：路由 + SSE + 静态托管。薄，活派给 config_store/db/runner/auth。"""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from claude_register import flow
from server import auth, db
from server.config_store import save_config, to_redacted_dict
from server.deps import AppState, default_now
from server.runner import RunnerBusy

WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"


def create_app(*, data_dir, config_path, now_fn=None) -> FastAPI:
    state = AppState(Path(data_dir), Path(config_path), now_fn or default_now)
    app = FastAPI()
    app.state.cr = state

    def require_auth(request: Request):
        cfg = state.config()
        if not cfg.panel_password:
            return  # 未设密码：放行（首次配置用）
        token = request.cookies.get(auth.COOKIE_NAME, "")
        if not auth.verify_token(token, cfg.panel_password, state.secret):
            raise HTTPException(status_code=401, detail="未登录")

    @app.post("/api/login")
    async def login(request: Request, response: Response):
        body = await request.json()
        cfg = state.config()
        password = body.get("password", "")
        if password != cfg.panel_password:
            raise HTTPException(status_code=401, detail="密码错误")
        token = auth.make_token(cfg.panel_password, state.secret)
        response.set_cookie(auth.COOKIE_NAME, token, httponly=True, samesite="lax")
        return {"ok": True}

    @app.get("/api/config")
    def get_config(_=Depends(require_auth)):
        return to_redacted_dict(state.config())

    @app.put("/api/config")
    async def put_config(request: Request, _=Depends(require_auth)):
        body = await request.json()
        cfg = save_config(state.config_path, body)
        return to_redacted_dict(cfg)

    @app.post("/api/runs")
    async def start_run(request: Request, _=Depends(require_auth)):
        body = await request.json() if await request.body() else {}
        try:
            rid = state.runner.start(
                state.config(),
                email=body.get("email"),
                domain=body.get("domain"),
                flow_fn=flow.run,
            )
        except RunnerBusy:
            raise HTTPException(status_code=409, detail="已有任务在运行")
        return {"run_id": rid}

    @app.get("/api/runs")
    def get_runs(limit: int = 50, offset: int = 0, _=Depends(require_auth)):
        return db.list_runs(state.conn, limit, offset)

    @app.get("/api/runs/{run_id}")
    def run_detail(run_id: int, _=Depends(require_auth)):
        row = db.get_run(state.conn, run_id)
        if not row:
            raise HTTPException(status_code=404)
        out = Path(row["output_dir"]) if row["output_dir"] else None
        log_txt = ""
        shots: list[str] = []
        if out and out.exists():
            lp = out / "log.txt"
            if lp.exists():
                log_txt = lp.read_text(encoding="utf-8")
            shots = sorted(p.name for p in out.glob("*.png"))
        return {**row, "log": log_txt, "screenshots": shots}

    @app.get("/api/runs/{run_id}/stream")
    async def stream(run_id: int, request: Request, _=Depends(require_auth)):
        row = db.get_run(state.conn, run_id)
        if not row:
            raise HTTPException(status_code=404)

        async def gen():
            out = Path(row["output_dir"]) if row["output_dir"] else None
            lp = out / "log.txt" if out else None
            if lp and lp.exists():
                for line in lp.read_text(encoding="utf-8").splitlines():
                    yield {"event": "log", "data": line}

            q = state.runner.subscribe(run_id)
            if q is None:
                fresh = db.get_run(state.conn, run_id)
                yield {"event": "done", "data": fresh["status"] if fresh else row["status"]}
                return

            loop = asyncio.get_event_loop()
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await loop.run_in_executor(None, lambda: q.get(timeout=0.5))
                except Exception:
                    continue
                if msg["type"] == "log":
                    yield {"event": "log", "data": msg["line"]}
                elif msg["type"] == "done":
                    yield {"event": "done", "data": msg["status"]}
                    break

        return EventSourceResponse(gen())

    @app.get("/api/accounts")
    def accounts(_=Depends(require_auth)):
        rows = db.list_accounts(state.conn)
        if rows:
            return rows
        seen: dict[str, dict] = {}
        for r in db.list_runs(state.conn, 500, 0):
            e = r["email"]
            if e and e not in seen and r["status"] == "success":
                seen[e] = {
                    "email": e,
                    "domain": r["domain"],
                    "last_run_id": r["id"],
                    "status": r["status"],
                }
        return list(seen.values())

    @app.post("/api/accounts/{email}/rerun")
    def rerun(email: str, _=Depends(require_auth)):
        try:
            rid = state.runner.start(state.config(), email=email, flow_fn=flow.run)
        except RunnerBusy:
            raise HTTPException(status_code=409, detail="已有任务在运行")
        return {"run_id": rid}

    # 截图/日志静态资源
    runs_dir = state.data_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/runs", StaticFiles(directory=runs_dir), name="runs")

    # 前端（dist 存在才挂，测试环境无 dist 不报错）
    if WEB_DIST.exists():
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")

    return app
