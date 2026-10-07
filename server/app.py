"""FastAPI：路由 + SSE + 静态托管。薄，活派给 config_store/db/runner/auth。"""
from __future__ import annotations

from dataclasses import replace

import asyncio
import secrets
import httpx
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from claude_register import flow
from claude_register.accounts import AccountRecord
from claude_register.anymail import AnyMailAccessError, AnyMailClient
from claude_register.console import log
from claude_register.oauth import OAuthError
from claude_register.proxy_pool import ProxyPool, XuiNode
from claude_register.session_check import check_session, probe_session
from claude_register.xui import XuiClient
from server import auth, db, export, oauth_acquire, open_api, sk_import
from server.config_store import save_config, to_dict
from server.deps import AppState, default_now
from server.runner import RunnerBusy
from server.takeover import TakeoverBusy, TakeoverError

WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"


def create_app(*, data_dir, config_path, now_fn=None) -> FastAPI:
    state = AppState(Path(data_dir), Path(config_path), now_fn or default_now)
    # 关掉 FastAPI 全局 /docs、/openapi.json：它们列的是需要面板 Cookie 的内部接口，
    # 会误导调用方。开放 API 的文档单独提供在 /api/v1/docs(.md) 与 /llms.txt。
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.cr = state

    def _has_valid_cookie(request: Request, cfg) -> bool:
        token = request.cookies.get(auth.COOKIE_NAME, "")
        return bool(auth.verify_token(token, cfg.panel_password, state.secret))

    def require_auth(request: Request):
        """严格鉴权：数据/动作/工件路由用。

        空密码（未配置）时一律 401——引导态下不暴露任何可触发/泄露的东西，
        直到设好密码；不再 allow-all。
        """
        cfg = state.config()
        if not cfg.panel_password or not _has_valid_cookie(request, cfg):
            raise HTTPException(status_code=401, detail="未登录")

    def require_auth_or_bootstrap(request: Request):
        """宽松鉴权：仅配置读写路由用。

        空密码时放行（好让首次配置能设密码）；一旦设了密码则要求有效 cookie。
        """
        cfg = state.config()
        if not cfg.panel_password:
            return  # 引导态：允许无鉴权读写配置以完成初始设置
        if not _has_valid_cookie(request, cfg):
            raise HTTPException(status_code=401, detail="未登录")

    @app.post("/api/login")
    async def login(request: Request, response: Response):
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 — 缺体/非 JSON 属于客户端错误，不能 500
            body = {}
        if not isinstance(body, dict):
            body = {}
        cfg = state.config()
        # 常量时间比较（兼容非 ASCII 密码与非字符串输入）。未设密码时一律拒绝：
        # 引导态不签发任何会话，避免发出立刻失效的「假登录成功」。
        if not auth.passwords_match(body.get("password"), cfg.panel_password):
            detail = "面板尚未设置密码，请先在设置页设定" if not cfg.panel_password else "密码错误"
            raise HTTPException(status_code=401, detail=detail)
        token = auth.make_token(cfg.panel_password, state.secret)
        response.set_cookie(
            auth.COOKIE_NAME,
            token,
            httponly=True,
            samesite="lax",
            max_age=auth.SESSION_MAX_AGE,
            # 反代终结 HTTPS 时跟着置 Secure，纯 HTTP 部署不置（否则 cookie 存不下）。
            # 多级代理时该头可能是 "https, http"，以最外层（第一个）为准。
            secure=request.headers.get("x-forwarded-proto", request.url.scheme)
            .split(",")[0].strip().lower() == "https",
            path="/",
        )
        return {"ok": True}

    @app.post("/api/logout")
    def logout(response: Response):
        response.delete_cookie(auth.COOKIE_NAME, path="/")
        return {"ok": True}

    @app.get("/api/config")
    def get_config(_=Depends(require_auth_or_bootstrap)):
        return to_dict(state.config())

    @app.put("/api/config")
    async def put_config(request: Request, _=Depends(require_auth_or_bootstrap)):
        body = await request.json()
        try:
            cfg = save_config(state.config_path, body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        return to_dict(cfg)

    @app.post("/api/xui/test")
    async def xui_test(request: Request, _=Depends(require_auth)):
        body = await request.json()
        base_url = str(body.get("base_url", "") or "")
        username = str(body.get("username", "") or "")
        password = str(body.get("password", "") or "")
        if password in ("", "••••"):
            # 脱敏回传：按 base_url 找已存节点取真实密码
            for n in state.config().xui_nodes:
                if n.get("base_url") == base_url:
                    password = n.get("password", "")
                    break
        try:
            count = len(XuiClient(base_url, username, password).list_inbounds())
        except Exception as exc:  # noqa: BLE001 — 面板测试连接需回报任何失败
            raise HTTPException(status_code=400, detail=f"连接失败：{exc}")
        return {"ok": True, "inbound_count": count}

    @app.post("/api/xui/cleanup")
    def xui_cleanup(_=Depends(require_auth)):
        cfg = state.config()
        nodes = [XuiNode(**n) for n in cfg.xui_nodes]
        if not nodes:
            return {"results": {}, "total": 0}
        pool = ProxyPool(
            nodes,
            expiry_days=cfg.xui_expiry_days,
            port_range=(cfg.xui_port_min, cfg.xui_port_max),
        )
        results = pool.cleanup_expired()
        return {"results": results, "total": sum(results.values())}

    @app.post("/api/runs")
    async def start_run(request: Request, _=Depends(require_auth)):
        body = await request.json() if await request.body() else {}
        try:
            rid = start_registration(body)
        except RunnerBusy:
            raise HTTPException(status_code=409, detail="已有任务在运行")
        return {"run_id": rid}

    def resolve_saved_proxy(cfg, proxy_id) -> str:
        """按 id 取代理池里的代理地址；不存在/无效抛 400。"""
        selected = next((p for p in cfg.saved_proxies if p["id"] == proxy_id), None)
        if selected is None:
            raise HTTPException(status_code=400, detail="所选代理不存在，请刷新代理列表")
        from claude_register.browser import validate_proxy
        try:
            validate_proxy(selected["url"])
        except ValueError:
            raise HTTPException(status_code=400, detail="所选代理地址无效，请编辑后重试") from None
        return selected["url"]

    def start_registration(body: dict) -> int:
        """面板与开放 API 共用的注册入口：解析所选代理并启动 Runner。

        代理不存在/无效抛 400；已有任务在跑抛 RunnerBusy，由调用方决定怎么回。
        """
        cfg = state.config()
        proxy_id = body.get("proxy_id")
        if proxy_id is not None:
            url = resolve_saved_proxy(cfg, proxy_id)
            cfg = replace(cfg, register_proxy=url, xui_enabled=False)
        return state.runner.start(
            cfg,
            email=body.get("email"),
            domain=body.get("domain"),
            flow_fn=flow.run,
        )

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
            # 注意：Runner.subscribe 对同一 active run 返回同一个 queue.Queue，
            # 多个并发 SSE 观察者会各自 get() 到不同的行（行被瓜分）。这是单管理员
            # 工具可接受的已知限制——GET /api/runs/{id} 详情始终返回完整 log.txt，
            # 不丢数据，仅重连后的实时 tail 可能不完整。不做 pub/sub 重构。
            q = state.runner.subscribe(run_id)
            if q is None:
                # 已结束的 run：从文件补发完整历史，再发 done（用最新 DB 状态）。
                out = Path(row["output_dir"]) if row["output_dir"] else None
                lp = out / "log.txt" if out else None
                if lp and lp.exists():
                    for line in lp.read_text(encoding="utf-8").splitlines():
                        yield {"event": "log", "data": line}
                fresh = db.get_run(state.conn, run_id)
                yield {"event": "done", "data": fresh["status"] if fresh else row["status"]}
                return

            # 活动 run：队列自 run 启动起累积了全部历史行，直接读队列即可，
            # 不能再补发 log.txt，否则每行重复。
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

    def _account_text(row: dict) -> str:
        """与落盘 account.txt 同源的带标签导出块。"""
        return AccountRecord(
            email=row.get("email") or "",
            sessionKey=row.get("session_key") or "",
            proxy=row.get("proxy") or "",
            mail_key=row.get("mail_key") or "",
            mail_base_url=row.get("mail_base_url") or "",
        ).text_export()

    def _account_rows() -> list[dict]:
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

    @app.get("/api/accounts")
    def accounts(_=Depends(require_auth)):
        return [{**r, "text": _account_text(r)} for r in _account_rows()]

    @app.get("/api/accounts/export")
    def accounts_export(
        fields: str | None = None,
        format: str = "text",
        sep: str | None = None,
        emails: str | None = None,
        status: str | None = None,
        check_status: str | None = None,
        claimed: str | None = None,
        _=Depends(require_auth),
    ):
        # 不带参数时与旧版「导出全部」完全一致：默认五项字段 + text 格式。
        return open_api.export_response(
            _account_rows(), fields=fields, fmt=format, sep=sep, emails=emails,
            status=status, check_status=check_status, download=True, claimed=claimed,
        )

    @app.get("/api/export/fields")
    def export_fields(_=Depends(require_auth)):
        return export.describe()

    @app.post("/api/api-key")
    def rotate_api_key(_=Depends(require_auth)):
        """生成新的开放 API Key 并立即生效（旧 Key 失效）。"""
        key = "cr_" + secrets.token_urlsafe(32)
        save_config(state.config_path, {"api_key": key})
        return {"api_key": key}

    @app.post("/api/accounts/import")
    async def accounts_import(request: Request, _=Depends(require_auth)):
        """批量导入 sessionKey。body: {text, proxy_id?, check?=true, skip_dead?=true}。"""
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            raise HTTPException(status_code=400, detail="请求体必须是 JSON") from None
        if not isinstance(body, dict) or not isinstance(body.get("text"), str):
            raise HTTPException(status_code=400, detail="text 必须是字符串")
        proxy_id = body.get("proxy_id") or None
        if proxy_id is not None and not isinstance(proxy_id, str):
            raise HTTPException(status_code=400, detail="proxy_id 必须是字符串")
        proxy = resolve_saved_proxy(state.config(), proxy_id) if proxy_id else ""
        check = body.get("check", True) is not False
        skip_dead = body.get("skip_dead", True) is not False

        def probe(sk, px, want_email):
            return probe_session(sk, px or None, want_email=want_email)

        try:
            return await asyncio.to_thread(
                sk_import.run_import, state.conn, body["text"], proxy=proxy,
                check=check, skip_dead=skip_dead, now=state.now_fn(), probe=probe,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    async def _json_object(request: Request) -> dict:
        try:
            body = await request.json() if await request.body() else {}
        except Exception:  # noqa: BLE001 — 非 JSON 属于客户端错误
            raise HTTPException(status_code=400, detail="请求体必须是 JSON") from None
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="请求体必须是对象")
        return body

    def _editable_fields(body: dict) -> dict:
        fields = {}
        for k in db.ACCOUNT_EDITABLE_FIELDS:
            if k not in body:
                continue
            v = body[k]
            if v is not None and not isinstance(v, str):
                raise HTTPException(status_code=400, detail=f"{k} 必须是字符串")
            fields[k] = (v or "") if k == "password" else (v or "").strip()
        return fields

    def _valid_email(value) -> str:
        if not isinstance(value, str) or not value.strip():
            raise HTTPException(status_code=400, detail="邮箱不能为空")
        email = value.strip().lower()
        if not sk_import.EMAIL_RE.fullmatch(email):
            raise HTTPException(status_code=400, detail="邮箱格式不正确")
        return email

    @app.post("/api/accounts", status_code=201)
    async def account_create(request: Request, _=Depends(require_auth)):
        """手动新增账号。body: {email, password?, session_key?, proxy?, display_name?,
        mail_key?, mail_base_url?}。邮箱已存在返回 409。"""
        body = await _json_object(request)
        email = _valid_email(body.get("email"))
        fields = _editable_fields(body)
        try:
            row = db.create_account(state.conn, email, fields, created_at=state.now_fn())
        except db.AccountExists:
            raise HTTPException(status_code=409, detail="该邮箱的账号已存在") from None
        log(f"已手动新增账号：{email}")
        return {**row, "text": _account_text(row)}

    @app.post("/api/accounts/batch-delete")
    async def accounts_batch_delete(request: Request, _=Depends(require_auth)):
        """批量删除。body: {emails: [...]}，返回实际删除数。"""
        body = await _json_object(request)
        emails = body.get("emails")
        if not isinstance(emails, list) or not all(isinstance(e, str) for e in emails):
            raise HTTPException(status_code=400, detail="emails 必须是字符串数组")
        if not emails:
            raise HTTPException(status_code=400, detail="未选择任何账号")
        deleted = db.delete_accounts(state.conn, emails)
        return {"ok": True, "deleted": deleted}

    @app.patch("/api/accounts/{email}")
    async def account_update(email: str, request: Request, _=Depends(require_auth)):
        if db.get_account(state.conn, email) is None:
            raise HTTPException(status_code=404, detail="账号不存在")
        body = await _json_object(request)
        fields = _editable_fields(body)
        new_email = _valid_email(body["email"]) if "email" in body else email
        if not fields and new_email == email:
            raise HTTPException(status_code=400, detail="没有可更新的字段")
        if new_email != email:
            if state.takeover.status().get("email") == email:
                raise HTTPException(status_code=409, detail="该账号正在接管，请先结束接管再改邮箱")
            try:
                db.rename_account(state.conn, email, new_email)
            except db.AccountExists:
                raise HTTPException(status_code=409, detail="该邮箱的账号已存在") from None
        if fields:
            db.update_account_fields(state.conn, new_email, fields)
        row = db.get_account(state.conn, new_email)
        return {**row, "text": _account_text(row)}

    @app.put("/api/accounts/{email}/claimed")
    async def account_set_claimed(email: str, request: Request, _=Depends(require_auth)):
        """面板手动标记 / 取消「已获取」。body: {"claimed": true|false}。"""
        body = await request.json() if await request.body() else {}
        claimed = body.get("claimed") if isinstance(body, dict) else None
        if not isinstance(claimed, bool):
            raise HTTPException(status_code=400, detail="claimed 必须是布尔值")
        if not db.set_account_claimed(state.conn, email, state.now_fn() if claimed else ""):
            raise HTTPException(status_code=404, detail="账号不存在")
        row = db.get_account(state.conn, email)
        return {**row, "text": _account_text(row)}

    @app.delete("/api/accounts/{email}")
    def account_delete(email: str, _=Depends(require_auth)):
        if not db.delete_account(state.conn, email):
            raise HTTPException(status_code=404, detail="账号不存在")
        return {"ok": True}

    @app.post("/api/accounts/{email}/rerun")
    def rerun(email: str, _=Depends(require_auth)):
        try:
            rid = state.runner.start(state.config(), email=email, flow_fn=flow.run)
        except RunnerBusy:
            raise HTTPException(status_code=409, detail="已有任务在运行")
        return {"run_id": rid}

    @app.post("/api/accounts/{email}/check")
    async def account_check(email: str, _=Depends(require_auth)):
        row = db.get_account(state.conn, email)
        if row is None:
            raise HTTPException(status_code=404, detail="账号不存在")
        checked_at = state.now_fn()
        status, detail = await asyncio.to_thread(
            check_session, row.get("session_key") or "", row.get("proxy") or ""
        )
        db.update_account_check(state.conn, email, status, checked_at)
        return {"status": status, "detail": detail, "checked_at": checked_at}

    @app.post("/api/accounts/{email}/oauth")
    async def account_oauth(email: str, _=Depends(require_auth)):
        """用账号的 sessionKey 自动完成 Claude OAuth 授权，令牌写库并返回账号行。"""
        row = db.get_account(state.conn, email)
        if row is None:
            raise HTTPException(status_code=404, detail="账号不存在")
        if not row.get("session_key"):
            raise HTTPException(status_code=400, detail="该账号无 sessionKey")
        try:
            row = await asyncio.to_thread(
                oauth_acquire.acquire, state.conn, email, now=state.now_fn(),
            )
        except oauth_acquire.AccountNotFound:
            raise HTTPException(status_code=404, detail="账号不存在") from None
        except OAuthError as exc:
            log(f"OAuth 授权失败：{email}：{exc}")
            raise HTTPException(status_code=422, detail=f"OAuth 授权失败：{exc}") from None
        log(f"已获取 OAuth 令牌：{email}（过期 {row.get('oauth_expires_at') or '未知'}）")
        return {**row, "text": _account_text(row)}

    @app.post("/api/takeover/start")
    async def takeover_start(request: Request, _=Depends(require_auth)):
        cfg = state.config()
        if not cfg.takeover_enabled:
            raise HTTPException(status_code=403, detail="接管功能已禁用")
        body = await request.json() if await request.body() else {}
        email = str(body.get("email", "") or "")
        row = db.get_account(state.conn, email)
        if row is None:
            raise HTTPException(status_code=404, detail="账号不存在")
        if not row.get("session_key"):
            raise HTTPException(status_code=400, detail="该账号无 sessionKey")
        try:
            info = await asyncio.to_thread(
                state.takeover.start,
                email=email,
                session_key=row["session_key"],
                proxy=row.get("proxy") or "",
                idle_timeout_s=cfg.takeover_idle_timeout_min * 60,
            )
        except TakeoverBusy:
            raise HTTPException(status_code=409, detail="已有接管会话，请先结束")
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"启动接管失败：{exc}")
        return info

    @app.post("/api/takeover/manual")
    async def takeover_manual(request: Request, _=Depends(require_auth)):
        """手动登录：开一个未登录的接管浏览器打开登录页。body: {email?, proxy_id?}。"""
        cfg = state.config()
        if not cfg.takeover_enabled:
            raise HTTPException(status_code=403, detail="接管功能已禁用")
        try:
            body = await request.json() if await request.body() else {}
        except Exception:  # noqa: BLE001
            raise HTTPException(status_code=400, detail="请求体必须是 JSON") from None
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="请求体必须是对象")
        email = body.get("email") or ""
        if not isinstance(email, str):
            raise HTTPException(status_code=400, detail="email 必须是字符串")
        email = email.strip().lower()
        if email and not sk_import.EMAIL_RE.fullmatch(email):
            raise HTTPException(status_code=400, detail="邮箱格式不正确")
        proxy_id = body.get("proxy_id") or None
        if proxy_id is not None and not isinstance(proxy_id, str):
            raise HTTPException(status_code=400, detail="proxy_id 必须是字符串")
        proxy = resolve_saved_proxy(cfg, proxy_id) if proxy_id else ""
        try:
            info = await asyncio.to_thread(
                state.takeover.start,
                email="",
                session_key="",
                proxy=proxy,
                idle_timeout_s=cfg.takeover_idle_timeout_min * 60,
                mode="manual",
                login_email=email,
            )
        except TakeoverBusy:
            raise HTTPException(status_code=409, detail="已有接管会话，请先结束")
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"启动手动登录失败：{exc}")
        return {**info, "mode": "manual", "login_email": email}

    capture_lock = asyncio.Lock()

    @app.post("/api/takeover/capture")
    async def takeover_capture(_=Depends(require_auth)):
        """读取接管浏览器当前的 sessionKey；是新值且未失效就新建/更新账号。

        面板在接管期间定时调用；sk 未变化、无 Cookie 或已判失效时只返回状态，
        不重复检测。新值优先用 claude.ai 返回的账号邮箱入库，其次用会话已
        关联的账号 / 手动登录时填写的邮箱，都没有时用占位标识。
        """
        async with capture_lock:
            ctx = state.takeover.capture_context()
            if not ctx["running"]:
                raise HTTPException(status_code=409, detail="当前没有活动的接管会话")
            try:
                session_key = await asyncio.to_thread(state.takeover.read_session_key)
            except TakeoverError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            base = {"found": bool(session_key), "saved": False, "email": ctx["email"]}
            if not session_key:
                return base
            if session_key == ctx["saved_key"]:
                return {**base, "saved": True, "unchanged": True}
            if session_key in ctx["rejected_keys"]:
                return {**base, "check_status": "dead", "repeat": True,
                        "check_detail": "该 sessionKey 已检测为失效"}

            proxy = ctx["proxy"]
            check_status, check_detail, found_email = await asyncio.to_thread(
                probe_session, session_key, proxy or None, want_email=True,
            )
            if check_status == "dead":
                state.takeover.mark_rejected(session_key)
                return {**base, "check_status": check_status, "check_detail": check_detail}

            email = (
                (found_email or "").strip().lower()
                or ctx["email"]
                or ctx["login_email"]
                or sk_import.placeholder_email(session_key)
            )
            now = state.now_fn()
            created = db.get_account(state.conn, email) is None
            if created:
                db.upsert_account(
                    state.conn, email, email.split("@", 1)[1], "", "", None, "success",
                    session_key=session_key, proxy=proxy, created_at=now,
                )
            else:
                fields = {"session_key": session_key}
                if proxy:
                    fields["proxy"] = proxy
                db.update_account_fields(state.conn, email, fields)
            db.update_account_check(state.conn, email, check_status, now)
            state.takeover.mark_saved(email=email, session_key=session_key)
            log(f"已从接管浏览器取得 sessionKey 并{'新建' if created else '更新'}账号：{email}")
            notes = []
            login_email = ctx["login_email"]
            if found_email and login_email and found_email.lower() != login_email:
                notes.append(f"实际登录的是 {found_email}，与填写的 {login_email} 不同")
            if not found_email and not ctx["email"] and not login_email:
                notes.append("未取得邮箱，使用占位标识")
            return {
                "found": True,
                "saved": True,
                "created": created,
                "email": email,
                "check_status": check_status,
                "check_detail": check_detail,
                "note": "；".join(notes),
            }

    @app.post("/api/takeover/stop")
    def takeover_stop(_=Depends(require_auth)):
        state.takeover.stop()
        return {"ok": True}

    @app.post("/api/takeover/relogin")
    async def takeover_relogin(_=Depends(require_auth)):
        info = state.takeover.status()
        if not info.get("running"):
            raise HTTPException(status_code=409, detail="当前没有活动的接管会话")
        if not info.get("email"):
            raise HTTPException(
                status_code=409, detail="手动登录会话尚未取得 sessionKey，无法自动重新登录",
            )

        email = str(info["email"])
        row = db.get_account(state.conn, email)
        if row is None:
            raise HTTPException(status_code=404, detail="账号不存在")

        cfg = state.config()
        using_saved_mail_key = bool(row.get("mail_key") and row.get("mail_base_url"))
        if using_saved_mail_key:
            mail_api_key = str(row["mail_key"])
            mail_base_url = str(row["mail_base_url"])
        else:
            mail_api_key = cfg.anymail_api_key
            mail_base_url = cfg.anymail_base_url
        if not mail_api_key or not mail_base_url:
            raise HTTPException(
                status_code=400,
                detail="该账号没有可用的收件凭据，且未配置 AnyMail 主凭据",
            )

        # 账号记录优先保存一把仅能读取本邮箱的永久子 Key。AnyMail 数据库重置、
        # 父 Key 被删除（会级联删除子 Key）等情况会让这把旧 Key 返回 401；先做
        # 一次只读探测，确认失效后用当前主 Key 重新派生并写回，让旧账号自愈。
        if using_saved_mail_key:
            try:
                saved_client = AnyMailClient(
                    base_url=mail_base_url,
                    api_key=mail_api_key,
                )
                await asyncio.to_thread(saved_client.check_email_access, to=email)
            except httpx.HTTPError as exc:
                # 临时网络/服务端故障交给真正的接码轮询按原逻辑退避，不误判 Key 失效。
                log(f"AnyMail 子 Key 探测暂时失败（{exc}），继续使用原凭据。")
            except (AnyMailAccessError, ValueError) as saved_exc:
                if not cfg.anymail_api_key or not cfg.anymail_base_url:
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            "账号保存的 AnyMail 子 Key 已不可用，"
                            "且设置中没有可用于修复的主凭据"
                        ),
                    ) from saved_exc

                try:
                    main_client = AnyMailClient(
                        base_url=cfg.anymail_base_url,
                        api_key=cfg.anymail_api_key,
                    )
                    await asyncio.to_thread(main_client.check_email_access, to=email)
                except httpx.HTTPError as exc:
                    # 网络瞬断时仍可进入浏览器轮询；create_child_key 也会安全降级。
                    log(f"AnyMail 主 Key 探测暂时失败（{exc}），仍尝试自动修复。")
                except (AnyMailAccessError, RuntimeError, ValueError) as main_exc:
                    raise HTTPException(
                        status_code=422,
                        detail=f"账号子 Key 已不可用，当前 AnyMail 主凭据也无法读取邮箱：{main_exc}",
                    ) from main_exc

                child = await asyncio.to_thread(
                    main_client.create_child_key,
                    email=email,
                    expires_at=None,
                    name_prefix="claude-register-relogin",
                )
                if child is not None:
                    mail_api_key = child.plaintext
                    mail_base_url = main_client.base_url
                    repaired_fields = {
                        "mail_key": child.plaintext,
                        "mail_base_url": main_client.base_url,
                    }
                    log(f"已为 {email} 重新派生永久 AnyMail 子 Key。")
                else:
                    # 主 Key 没有 keys:create 时也能读信；清掉失效子 Key，今后直接
                    # 回退设置里的主凭据，不再让每次重新登录都先撞同一个 401。
                    mail_api_key = cfg.anymail_api_key
                    mail_base_url = cfg.anymail_base_url
                    repaired_fields = {"mail_key": "", "mail_base_url": ""}
                    log(f"未能为 {email} 派生新子 Key，已改用 AnyMail 主 Key。")
                db.update_account_fields(state.conn, email, repaired_fields)

        try:
            session_key = await asyncio.to_thread(
                state.takeover.relogin,
                email=email,
                mail_base_url=mail_base_url,
                mail_api_key=mail_api_key,
                login_timeout=cfg.register_login_timeout,
            )
        except TakeoverError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

        check_status, check_detail = await asyncio.to_thread(
            check_session,
            session_key,
            row.get("proxy") or "",
        )
        checked_at = state.now_fn()
        if check_status == "dead":
            db.update_account_check(state.conn, email, check_status, checked_at)
            raise HTTPException(
                status_code=422,
                detail=f"重新登录取得的 sessionKey 仍不可用：{check_detail}",
            )

        db.update_account_fields(state.conn, email, {"session_key": session_key})
        db.update_account_check(state.conn, email, check_status, checked_at)
        # 让自动取 sk 的轮询认得这把新 Key，不再重复检测/入库。
        state.takeover.mark_saved(email=email, session_key=session_key)
        return {
            "ok": True,
            "email": email,
            "check_status": check_status,
            "check_detail": check_detail,
        }

    @app.get("/api/takeover")
    def takeover_status(_=Depends(require_auth)):
        return state.takeover.status()

    @app.get("/api/vnc-auth", status_code=204)
    def vnc_auth(_=Depends(require_auth)):
        """Nginx auth_request 子请求：复用面板 Cookie 保护 Xpra HTML5。"""
        return Response(status_code=204)

    @app.post("/api/takeover/heartbeat")
    def takeover_heartbeat(_=Depends(require_auth)):
        """接管页存活心跳；只要页面在线就续期回收计时器。"""
        try:
            state.takeover.touch()
        except TakeoverError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True}

    # 截图/日志工件：必须鉴权 + 防路径穿越（不能用裸 StaticFiles，mount 不继承 Depends）
    runs_dir = state.data_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    @app.get("/runs/{run_id}/{filename}")
    def run_artifact(run_id: int, filename: str, _=Depends(require_auth)):
        base = (state.data_dir / "runs" / str(run_id)).resolve()
        target = (base / filename).resolve()
        if base not in target.parents and target != base:
            raise HTTPException(status_code=404)
        if not target.is_file():
            raise HTTPException(status_code=404)
        return FileResponse(target)

    @app.on_event("shutdown")
    def _cleanup_takeover():
        # server 关闭/重启时兜底清理接管会话，避免 Xpra/浏览器变孤儿进程。
        # stop() 幂等，没在跑也安全。
        state.takeover.stop()

    open_api.register_open_api(
        app, state, start_registration=start_registration, account_rows=_account_rows,
    )

    # 前端（dist 存在才挂，测试环境无 dist 不报错）
    if WEB_DIST.exists():
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")

    return app
