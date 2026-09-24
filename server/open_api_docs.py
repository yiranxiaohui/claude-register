"""开放 API 文档：Markdown（给 AI/人读）、OpenAPI（给工具）、Swagger 页、llms.txt。

全部免鉴权：文档不含任何密钥或账号数据。Base URL 按请求头推导（兼容外层反代的
X-Forwarded-Proto / X-Forwarded-Host），文档里的示例因此可以直接复制使用。
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from server import export

DOC_PATH = Path(__file__).with_name("open_api.md")
DOCS_MD = "/api/v1/docs.md"
OPENAPI_JSON = "/api/v1/openapi.json"
SWAGGER = "/api/v1/docs"
API_TITLE = "claude-register 开放 API"
API_VERSION = "1.0.0"

# Host 只允许主机名/IP/端口字符，防止伪造 Host 头往文档里注入内容。
_HOST_RE = re.compile(r"^[A-Za-z0-9.\-]+(:\d{1,5})?$|^\[[0-9A-Fa-f:.]+\](:\d{1,5})?$")


def _first(value: str | None) -> str:
    return (value or "").split(",")[0].strip()


def base_url(request: Request) -> str:
    proto = _first(request.headers.get("x-forwarded-proto")).lower() or request.url.scheme
    if proto not in ("http", "https"):
        proto = request.url.scheme
    host = _first(request.headers.get("x-forwarded-host")) or request.headers.get("host", "")
    if not _HOST_RE.match(host or ""):
        host = request.url.netloc
    return f"{proto}://{host}"


def fields_table() -> str:
    rows = ["| 字段 | 名称 | text 标签 | 默认 | 敏感 |", "|---|---|---|---|---|"]
    for f in export.FIELDS:
        rows.append(
            f"| `{f.key}` | {f.title} | `{f.label}` | "
            f"{'✓' if f.key in export.DEFAULT_FIELDS else ''} | {'敏感' if f.secret else ''} |"
        )
    return "\n".join(rows)


@lru_cache(maxsize=1)
def _template() -> str:
    return DOC_PATH.read_text(encoding="utf-8")


def render_markdown(base: str, max_wait: int) -> str:
    return (
        _template()
        .replace("{{BASE_URL}}", base)
        .replace("{{MAX_WAIT}}", str(max_wait))
        .replace("{{FIELDS_TABLE}}", fields_table())
    )


def render_llms_txt(base: str) -> str:
    return (
        "# claude-register\n\n"
        "> 自动注册 claude.ai 账号的服务。开放 API 可触发自动注册、查询结果，"
        "并按需选择字段导出账号信息（邮箱、sessionKey、代理、密码等）。"
        "调用需要 API Key（Authorization: Bearer <key>）。\n\n"
        "## API 文档\n\n"
        f"- [开放 API 文档（Markdown）]({base}{DOCS_MD}): 鉴权、全部接口、参数、响应、字段与示例\n"
        f"- [OpenAPI 3 规范（JSON）]({base}{OPENAPI_JSON}): 机器可读的接口定义\n"
        f"- [在线调试（Swagger UI）]({base}{SWAGGER}): 浏览器里直接试调用\n"
    )


def build_openapi(app: FastAPI, base: str) -> dict:
    from server.open_api import DOC_MODELS  # 避免循环导入

    routes = [
        r for r in app.routes
        if getattr(r, "path", "").startswith("/api/v1/") and getattr(r, "include_in_schema", False)
    ]
    schema = get_openapi(
        title=API_TITLE,
        version=API_VERSION,
        description=(
            "触发自动注册、查询结果、按需导出账号信息。完整说明（含示例、字段表）见 "
            f"[{DOCS_MD}]({base}{DOCS_MD})。"
        ),
        routes=routes,
        servers=[{"url": base}],
    )
    components = schema.setdefault("components", {})
    schemas = components.setdefault("schemas", {})
    for model in DOC_MODELS:
        defs = model.model_json_schema(ref_template="#/components/schemas/{model}")
        for name, sub in defs.pop("$defs", {}).items():
            schemas.setdefault(name, sub)
        schemas.setdefault(model.__name__, defs)
    components["securitySchemes"] = {
        "BearerAuth": {"type": "http", "scheme": "bearer",
                       "description": "Authorization: Bearer <API Key>"},
        "ApiKeyHeader": {"type": "apiKey", "in": "header", "name": "X-API-Key"},
    }
    schema["security"] = [{"BearerAuth": []}, {"ApiKeyHeader": []}]
    return schema


def register_docs(app: FastAPI, *, max_wait: int) -> None:
    @app.get(DOCS_MD, include_in_schema=False)
    def docs_markdown(request: Request):
        return PlainTextResponse(
            render_markdown(base_url(request), max_wait),
            media_type="text/markdown; charset=utf-8",
        )

    @app.get(OPENAPI_JSON, include_in_schema=False)
    def docs_openapi(request: Request):
        return JSONResponse(build_openapi(app, base_url(request)))

    @app.get(SWAGGER, include_in_schema=False)
    def docs_swagger() -> HTMLResponse:
        # 用相对地址，页面里不回显任何请求头内容
        return get_swagger_ui_html(openapi_url=OPENAPI_JSON, title=API_TITLE)

    @app.get("/llms.txt", include_in_schema=False)
    def llms_txt(request: Request):
        return PlainTextResponse(render_llms_txt(base_url(request)),
                                 media_type="text/plain; charset=utf-8")
