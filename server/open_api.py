"""开放 API（/api/v1/*）：供外部脚本触发自动注册、查询结果、按需导出账号信息。

与面板接口的区别：
- 鉴权用 API Key（Authorization: Bearer <key> 或 X-API-Key），不用面板 Cookie；
  不接受 query 参数传 key，避免落进访问日志。
- 默认关闭，需在面板「设置 → 开放 API」启用并生成 Key。
- 注册仍是单任务：已有任务在跑时返回 409 并带上正在运行的 run_id。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from server import auth, db, export
from server.runner import RunnerBusy

# 长轮询上限：容器内 Nginx 对 / 的默认读超时是 60s，留足余量。
MAX_WAIT_S = 50
LOG_TAIL_LINES = 20


def run_result(state, run_id: int, fields, fmt: str, sep: str) -> dict | None:
    """把 runs 行 + 账号行整理成对外的结果；run 不存在返回 None。

    status 取值：running / success / needs_manual / failed。
    needs_manual 表示流程跑完但没拿到 sessionKey（账号已入库，需在面板接管处理）。
    """
    row = db.get_run(state.conn, run_id)
    if row is None:
        return None
    status = row["status"] or "failed"
    acct = None
    if status == "success":
        acct = db.get_account(state.conn, row["email"]) if row.get("email") else None
        status = "success" if acct and acct.get("session_key") else "needs_manual"
    result = {
        "run_id": row["id"],
        "status": status,
        "email": row.get("email") or None,
        "started_at": row.get("started_at") or None,
        "finished_at": row.get("finished_at") or None,
        "account": export.pick(acct, fields) if acct else None,
    }
    if acct and fmt != "json":
        result["export"] = export.render([acct], fields, fmt, sep=sep)[0]
    if status in ("failed", "needs_manual"):
        result["log_tail"] = _log_tail(row.get("output_dir"))
    return result


def _log_tail(output_dir) -> list[str]:
    if not output_dir:
        return []
    path = Path(output_dir) / "log.txt"
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return lines[-LOG_TAIL_LINES:]


def _split(raw: str | None) -> list[str]:
    return [x.strip() for x in (raw or "").split(",") if x.strip()]


def export_response(rows, *, fields, fmt, sep, emails, status, check_status,
                    download: bool) -> Response:
    try:
        keys = export.parse_fields(fields)
        fmt = export.parse_format(fmt)
        sep = export.parse_sep(sep)
    except export.ExportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    picked = export.filter_rows(
        rows, emails=_split(emails), status=status or None,
        check_status=check_status or None,
    )
    body, media = export.render(picked, keys, fmt, sep=sep)
    headers = {"X-Total-Count": str(len(picked))}
    if download:
        headers["Content-Disposition"] = (
            f'attachment; filename="accounts.{export.FILE_EXT[fmt]}"'
        )
    return Response(body, media_type=media, headers=headers)


def register_open_api(app: FastAPI, state, *, start_registration, account_rows) -> None:
    def require_api_key(request: Request):
        cfg = state.config()
        if not cfg.api_enabled or not cfg.api_key:
            raise HTTPException(status_code=403, detail="开放 API 未启用")
        token = ""
        authz = request.headers.get("authorization", "")
        if authz[:7].lower() == "bearer ":
            token = authz[7:].strip()
        token = token or request.headers.get("x-api-key", "").strip()
        if not auth.passwords_match(token, cfg.api_key):
            raise HTTPException(
                status_code=401, detail="API Key 无效",
                headers={"WWW-Authenticate": "Bearer"},
            )

    @app.get("/api/v1/fields", tags=["open-api"])
    def v1_fields(_=Depends(require_api_key)):
        """可导出的字段与格式。"""
        return export.describe()

    @app.get("/api/v1/proxies", tags=["open-api"])
    def v1_proxies(_=Depends(require_api_key)):
        """面板代理池里可选的代理（只给 id 和名称，不暴露地址与凭据）。"""
        return [{"id": p["id"], "name": p["name"]} for p in state.config().saved_proxies]

    @app.post("/api/v1/register", status_code=202, tags=["open-api"])
    async def v1_register(request: Request, _=Depends(require_api_key)):
        """触发一次自动注册。body 可选：email / domain / proxy_id。"""
        try:
            body = await request.json() if await request.body() else {}
        except Exception:  # noqa: BLE001
            raise HTTPException(status_code=400, detail="请求体必须是 JSON") from None
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")
        for key in ("email", "domain", "proxy_id"):
            if body.get(key) is not None and not isinstance(body[key], str):
                raise HTTPException(status_code=400, detail=f"{key} 必须是字符串")
        try:
            rid = start_registration(body)
        except RunnerBusy:
            active = db.active_run(state.conn)
            return JSONResponse(
                status_code=409,
                content={
                    "detail": "已有任务在运行",
                    "active_run_id": active["id"] if active else None,
                },
            )
        return {"run_id": rid, "status": "running"}

    @app.get("/api/v1/register/{run_id}", tags=["open-api"])
    async def v1_register_status(
        run_id: int,
        wait: int = Query(0, ge=0, le=MAX_WAIT_S, description="最多等待秒数（长轮询）"),
        fields: str | None = Query(None, description="逗号分隔的字段，默认五项"),
        format: str = Query("json", description="额外生成 export 文本：text/csv/line"),
        sep: str | None = Query(None, description="line 格式的分隔符，默认 ----"),
        _=Depends(require_api_key),
    ):
        """查询注册结果；成功时 account 只含所选字段。"""
        try:
            keys = export.parse_fields(fields)
            fmt = export.parse_format(format)
            line_sep = export.parse_sep(sep)
        except export.ExportError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait
        while True:
            result = run_result(state, run_id, keys, fmt, line_sep)
            if result is None:
                raise HTTPException(status_code=404, detail="任务不存在")
            if result["status"] != "running" or loop.time() >= deadline:
                return result
            await asyncio.sleep(min(1.0, max(0.0, deadline - loop.time())))

    @app.get("/api/v1/accounts/export", tags=["open-api"])
    def v1_accounts_export(
        fields: str | None = None,
        format: str = "json",
        sep: str | None = None,
        emails: str | None = None,
        status: str | None = None,
        check_status: str | None = None,
        _=Depends(require_api_key),
    ):
        """批量导出账号；可按 emails / status / check_status 过滤。"""
        return export_response(
            account_rows(), fields=fields, fmt=format, sep=sep, emails=emails,
            status=status, check_status=check_status, download=False,
        )
