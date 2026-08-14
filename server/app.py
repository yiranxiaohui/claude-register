"""FastAPI：路由 + SSE + 静态托管。薄，活派给 config_store/db/runner/auth。"""
from __future__ import annotations

import asyncio
import hmac
import httpx
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response, WebSocket
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from claude_register import flow
from claude_register.accounts import AccountRecord
from claude_register.anymail import AnyMailAccessError, AnyMailClient
from claude_register.console import log
from claude_register.proxy_pool import ProxyPool, XuiNode
from claude_register.session_check import check_session
from claude_register.xui import XuiClient
from server import auth, db
from server.config_store import save_config, to_dict
from server.deps import AppState, default_now
from server.runner import RunnerBusy
from server.takeover import TakeoverBusy, TakeoverError

WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"


def create_app(*, data_dir, config_path, now_fn=None) -> FastAPI:
    state = AppState(Path(data_dir), Path(config_path), now_fn or default_now)
    app = FastAPI()
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
        body = await request.json()
        cfg = state.config()
        password = body.get("password", "")
        # 常量时间比较，避免密码校验的时序侧信道。空密码（首次配置）保持旧行为：
        # compare_digest("", "") 为真即放行。
        if not hmac.compare_digest(password, cfg.panel_password):
            raise HTTPException(status_code=401, detail="密码错误")
        token = auth.make_token(cfg.panel_password, state.secret)
        response.set_cookie(auth.COOKIE_NAME, token, httponly=True, samesite="lax")
        return {"ok": True}

    @app.get("/api/config")
    def get_config(_=Depends(require_auth_or_bootstrap)):
        return to_dict(state.config())

    @app.put("/api/config")
    async def put_config(request: Request, _=Depends(require_auth_or_bootstrap)):
        body = await request.json()
        cfg = save_config(state.config_path, body)
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
    def accounts_export(_=Depends(require_auth)):
        text = "\n\n".join(_account_text(r) for r in _account_rows())
        if text:
            text += "\n"
        return Response(
            text,
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="accounts.txt"'},
        )

    @app.patch("/api/accounts/{email}")
    async def account_update(email: str, request: Request, _=Depends(require_auth)):
        if db.get_account(state.conn, email) is None:
            raise HTTPException(status_code=404, detail="账号不存在")
        body = await request.json() if await request.body() else {}
        fields = {k: body[k] for k in db.ACCOUNT_EDITABLE_FIELDS if k in body}
        if not fields:
            raise HTTPException(status_code=400, detail="没有可更新的字段")
        db.update_account_fields(state.conn, email, fields)
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

    @app.post("/api/takeover/stop")
    def takeover_stop(_=Depends(require_auth)):
        state.takeover.stop()
        return {"ok": True}

    @app.post("/api/takeover/relogin")
    async def takeover_relogin(_=Depends(require_auth)):
        info = state.takeover.status()
        if not info.get("running") or not info.get("email"):
            raise HTTPException(status_code=409, detail="当前没有活动的接管会话")

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
        return {
            "ok": True,
            "email": email,
            "check_status": check_status,
            "check_detail": check_detail,
        }

    @app.get("/api/takeover")
    def takeover_status(_=Depends(require_auth)):
        return state.takeover.status()

    # ---- KasmVNC 反代（HTTP 资源 + websocket 推流都过面板，复用密码鉴权）----
    # Xvnc 只绑 127.0.0.1:<web_port>，外界唯一入口是这里。端口每次请求时从
    # manager 读：测试里可以把它指到假上游。
    def _kasm_base() -> str:
        return f"http://127.0.0.1:{state.takeover.web_port}"

    @app.get("/vnc/{path:path}")
    async def vnc_http(path: str, _=Depends(require_auth)):
        if ".." in path:
            raise HTTPException(status_code=404)
        base = _kasm_base()
        url = f"{base}/{path}" if path else f"{base}/"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(url)
        except Exception:
            raise HTTPException(
                status_code=502, detail="KasmVNC 未运行，请先启动接管会话"
            )
        return Response(
            r.content,
            status_code=r.status_code,
            media_type=r.headers.get("content-type"),
        )

    # 旧版 noVNC 客户端会按页面路径解析出 /vnc/websockify，直连 /websockify 的
    # 老书签也兼容；两个路径都桥到 KasmVNC 的 websocket。
    @app.websocket("/websockify")
    @app.websocket("/vnc/websockify")
    async def vnc_ws(ws: WebSocket):
        cfg = state.config()
        token = ws.cookies.get(auth.COOKIE_NAME, "")
        if not cfg.panel_password or not auth.verify_token(token, cfg.panel_password, state.secret):
            await ws.close(code=1008)
            return
        # 子协议：把客户端请求的原样递给上游，再把上游选中的回给客户端
        # （KasmVNC 自家客户端请求 binary；选了客户端没请求的子协议会被浏览器断开）。
        offered = [
            p.strip()
            for p in ws.headers.get("sec-websocket-protocol", "").split(",")
            if p.strip()
        ]
        try:
            import websockets

            upstream = await websockets.connect(
                f"ws://127.0.0.1:{state.takeover.web_port}/websockify",
                subprotocols=offered or None,
                # KasmVNC 校验 Origin 头，缺了直接 404
                additional_headers={"Origin": _kasm_base()},
                open_timeout=8,
                max_size=None,
            )
        except Exception:
            await ws.close(code=1011)
            return
        await ws.accept(subprotocol=upstream.protocol.subprotocol)

        async def client_to_kasm():
            try:
                while True:
                    msg = await ws.receive()
                    if msg.get("bytes") is not None:
                        await upstream.send(msg["bytes"])
                    elif msg.get("text") is not None:
                        await upstream.send(msg["text"])
                    else:  # disconnect
                        break
            except Exception:
                pass

        async def kasm_to_client():
            try:
                async for data in upstream:
                    if isinstance(data, bytes):
                        await ws.send_bytes(data)
                    else:
                        await ws.send_text(data)
            except Exception:
                pass

        tasks = [
            asyncio.create_task(client_to_kasm()),
            asyncio.create_task(kasm_to_client()),
        ]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            try:
                await upstream.close()
            except Exception:
                pass
            try:
                await ws.close()
            except Exception:
                pass

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
        # server 关闭/重启时兜底清理接管会话，避免 Xvfb/x11vnc/浏览器变孤儿进程。
        # stop() 幂等，没在跑也安全。
        state.takeover.stop()

    # 前端（dist 存在才挂，测试环境无 dist 不报错）
    if WEB_DIST.exists():
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")

    return app
