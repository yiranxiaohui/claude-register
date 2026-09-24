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
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from server import auth, db, export, open_api_docs
from server.runner import RunnerBusy

# 长轮询上限：容器内 Nginx 对 / 的默认读超时是 60s，留足余量。
MAX_WAIT_S = 50
LOG_TAIL_LINES = 20


# ---- 仅用于生成 OpenAPI 文档的模型（运行时校验仍走下面的手写逻辑，保持 400 语义）----


class RegisterRequest(BaseModel):
    """触发注册的请求体；整个 body 与每个字段都可省略。"""

    email: str | None = Field(None, description="指定要注册的邮箱；不填自动创建临时邮箱",
                              examples=["alice@example.com"])
    domain: str | None = Field(None, description="自动创建邮箱时使用的域名；不填用默认域名")
    proxy_id: str | None = Field(None, description="代理池中的代理 id（见 GET /api/v1/proxies）")


class RegisterAccepted(BaseModel):
    run_id: int = Field(description="注册任务 id，用于查询结果")
    status: Literal["running"]


class RegisterBusy(BaseModel):
    detail: str = Field(examples=["已有任务在运行"])
    active_run_id: int | None = Field(description="正在运行的任务 id")


class RegisterResult(BaseModel):
    run_id: int
    status: Literal["running", "success", "needs_manual", "failed"] = Field(
        description="running 进行中；success 成功并拿到 sessionKey；"
                    "needs_manual 跑完但没拿到 sessionKey（账号已入库）；failed 失败")
    email: str | None = Field(description="注册的邮箱（开始后才确定）")
    started_at: str | None
    finished_at: str | None
    account: dict[str, Any] | None = Field(
        description="成功或 needs_manual 时的账号信息，只含 fields 选择的字段",
        examples=[{"email": "alice@example.com", "session_key": "sk-ant-sid01-..."}])
    export: str | None = Field(None, description="format 为 text/csv/line 时才返回的导出文本")
    log_tail: list[str] | None = Field(None, description="failed / needs_manual 时最近 20 行日志")


class ErrorResponse(BaseModel):
    detail: str


class FieldInfo(BaseModel):
    key: str = Field(description="fields 参数里使用的字段名")
    title: str = Field(description="中文名称")
    label: str = Field(description="text 格式里的行标签")
    default: bool = Field(description="是否属于默认字段")
    secret: bool = Field(description="是否为敏感凭据")


class FieldsInfo(BaseModel):
    fields: list[FieldInfo]
    formats: list[str]
    default_fields: list[str]
    default_line_sep: str


class ProxyItem(BaseModel):
    id: str
    name: str


DOC_MODELS = (RegisterRequest, RegisterAccepted, RegisterBusy, RegisterResult,
              ErrorResponse, FieldInfo, FieldsInfo, ProxyItem)
_AUTH_ERRORS = {
    401: {"model": ErrorResponse, "description": "缺少或错误的 API Key"},
    403: {"model": ErrorResponse, "description": "服务未启用开放 API"},
}
_FIELDS_DESC = ("逗号分隔的字段名，默认 email,session_key,proxy,mail_base_url,mail_key；"
                "可选值见 GET /api/v1/fields")


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
    open_api_docs.register_docs(app, max_wait=MAX_WAIT_S)

    def require_api_key(request: Request):
        cfg = state.config()
        docs = open_api_docs.base_url(request) + open_api_docs.DOCS_MD
        if not cfg.api_enabled or not cfg.api_key:
            raise HTTPException(status_code=403, detail=f"开放 API 未启用（接口文档：{docs}）")
        token = ""
        authz = request.headers.get("authorization", "")
        if authz[:7].lower() == "bearer ":
            token = authz[7:].strip()
        token = token or request.headers.get("x-api-key", "").strip()
        if not auth.passwords_match(token, cfg.api_key):
            raise HTTPException(
                status_code=401, detail=f"API Key 无效（接口文档：{docs}）",
                headers={"WWW-Authenticate": "Bearer",
                         "Link": f'<{docs}>; rel="describedby"'},
            )

    @app.get("/api/v1/fields", tags=["元数据"], summary="可导出的字段与格式",
             response_model=FieldsInfo, responses=_AUTH_ERRORS)
    def v1_fields(_=Depends(require_api_key)):
        """返回 fields 参数可用的字段、默认字段和支持的导出格式。"""
        return export.describe()

    @app.get("/api/v1/proxies", tags=["元数据"], summary="可选的注册代理",
             response_model=list[ProxyItem], responses=_AUTH_ERRORS)
    def v1_proxies(_=Depends(require_api_key)):
        """面板代理池里可选的代理（只给 id 和名称，不暴露地址与凭据）。"""
        return [{"id": p["id"], "name": p["name"]} for p in state.config().saved_proxies]

    @app.post(
        "/api/v1/register", status_code=202, tags=["注册"], summary="触发一次自动注册",
        responses={
            202: {"model": RegisterAccepted, "description": "已开始，用 run_id 查询结果"},
            400: {"model": ErrorResponse, "description": "请求体不合法或代理不存在"},
            409: {"model": RegisterBusy, "description": "已有注册任务在运行"},
            **_AUTH_ERRORS,
        },
        openapi_extra={"requestBody": {"required": False, "content": {"application/json": {
            "schema": {"$ref": "#/components/schemas/RegisterRequest"}}}}},
    )
    async def v1_register(request: Request, _=Depends(require_api_key)):
        """后台开始一次注册（通常 1–5 分钟），立即返回 run_id。同一时刻只运行一个任务。"""
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

    @app.get(
        "/api/v1/register/{run_id}", tags=["注册"], summary="查询注册结果",
        responses={
            200: {"model": RegisterResult, "description": "任务状态与结果"},
            400: {"model": ErrorResponse, "description": "fields / format / sep 不合法"},
            404: {"model": ErrorResponse, "description": "任务不存在"},
            **_AUTH_ERRORS,
        },
    )
    async def v1_register_status(
        run_id: int,
        wait: int = Query(0, ge=0, le=MAX_WAIT_S,
                          description=f"长轮询：任务仍在运行时最多等待的秒数（0–{MAX_WAIT_S}）"),
        fields: str | None = Query(None, description=_FIELDS_DESC),
        format: str = Query("json", description="设为 text/csv/line 时额外返回 export 文本",
                            json_schema_extra={"enum": list(export.FORMATS)}),
        sep: str | None = Query(None, description="format=line 的分隔符，默认 ----"),
        _=Depends(require_api_key),
    ):
        """查询注册任务；成功时 account 只含 fields 选择的字段。任务未结束且 wait>0 时会等待。"""
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

    @app.get(
        "/api/v1/accounts/export", tags=["导出"], summary="批量导出账号",
        responses={
            200: {
                "description": "对应格式的导出内容；响应头 X-Total-Count 为条数",
                "content": {
                    "application/json": {"schema": {"type": "array", "items": {"type": "object"}}},
                    "text/plain": {"schema": {"type": "string"}},
                    "text/csv": {"schema": {"type": "string"}},
                },
            },
            400: {"model": ErrorResponse, "description": "参数不合法"},
            **_AUTH_ERRORS,
        },
    )
    def v1_accounts_export(
        fields: str | None = Query(None, description=_FIELDS_DESC),
        format: str = Query("json", description="json / text / csv / line",
                            json_schema_extra={"enum": list(export.FORMATS)}),
        sep: str | None = Query(None, description="format=line 的分隔符，默认 ----"),
        emails: str | None = Query(None, description="只导出这些邮箱（逗号分隔，不区分大小写）"),
        status: str | None = Query(None, description="注册状态：success / needs_manual"),
        check_status: str | None = Query(
            None, description="最近检测结果：alive / dead / blocked / error"),
        _=Depends(require_api_key),
    ):
        """按创建时间倒序导出已入库账号，可选字段、格式与筛选条件。"""
        return export_response(
            account_rows(), fields=fields, fmt=format, sep=sep, emails=emails,
            status=status, check_status=check_status, download=False,
        )
