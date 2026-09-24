"""开放 API 文档：免鉴权可读、Base URL 推导、与实际路由/字段保持同步。"""
from __future__ import annotations

import re

from fastapi.testclient import TestClient

from server import export
from server.app import create_app
from server.config_store import save_config

KEY = "cr_doc_key_0123456789"


def _client(tmp_path, **cfg):
    save_config(tmp_path / "config.yaml", {"panel_password": "pw", **cfg})
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "config.yaml")
    return app, TestClient(app)


def _v1_routes(app):
    return sorted(
        (m, r.path) for r in app.routes
        if getattr(r, "path", "").startswith("/api/v1/") and getattr(r, "include_in_schema", False)
        for m in r.methods if m not in ("HEAD", "OPTIONS")
    )


def test_markdown_docs_public_and_complete(tmp_path):
    app, c = _client(tmp_path)  # 开放 API 未启用也能看文档
    r = c.get("/api/v1/docs.md", headers={"host": "10.1.42.1:8790"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    md = r.text
    assert "http://10.1.42.1:8790/api/v1" in md
    assert "{{" not in md and "}}" not in md
    for method, path in _v1_routes(app):
        assert f"`{method}` | `{path}`" in md, (method, path)
    for f in export.FIELDS:
        assert f"| `{f.key}` |" in md
    assert "Authorization: Bearer" in md and "X-API-Key" in md


def test_base_url_honours_forwarded_headers(tmp_path):
    _, c = _client(tmp_path)
    md = c.get("/api/v1/docs.md", headers={
        "host": "10.1.42.1:8790",
        "x-forwarded-proto": "https, http",
        "x-forwarded-host": "reg.example.com",
    }).text
    assert "https://reg.example.com/api/v1" in md
    assert "10.1.42.1" not in md


def test_base_url_rejects_injected_host(tmp_path):
    _, c = _client(tmp_path)
    md = c.get("/api/v1/docs.md", headers={
        "x-forwarded-host": "evil.com/<script>", "x-forwarded-proto": "javascript",
    }).text
    assert "<script>" not in md and "javascript://" not in md
    assert "http://testserver/api/v1" in md


def test_openapi_only_covers_open_api(tmp_path):
    app, c = _client(tmp_path)
    spec = c.get("/api/v1/openapi.json", headers={"host": "h:1"}).json()
    assert spec["openapi"].startswith("3.")
    assert spec["servers"] == [{"url": "http://h:1"}]
    assert all(p.startswith("/api/v1/") for p in spec["paths"])
    ops = sorted((m.upper(), p) for p, item in spec["paths"].items() for m in item)
    assert ops == _v1_routes(app)
    assert set(spec["components"]["securitySchemes"]) == {"BearerAuth", "ApiKeyHeader"}
    for item in spec["paths"].values():
        for op in item.values():
            assert op.get("summary"), op
    body = spec["paths"]["/api/v1/register"]["post"]["requestBody"]
    ref = body["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[1]
    assert ref in spec["components"]["schemas"]
    # 所有 $ref 都能解析
    for name in re.findall(r'"#/components/schemas/([^"]+)"', c.get("/api/v1/openapi.json").text):
        assert name in spec["components"]["schemas"], name


def test_swagger_and_llms_txt(tmp_path):
    _, c = _client(tmp_path)
    html = c.get("/api/v1/docs")
    assert html.status_code == 200 and "/api/v1/openapi.json" in html.text
    llms = c.get("/llms.txt", headers={"host": "10.1.42.1:8790"}).text
    assert llms.startswith("# claude-register")
    assert "http://10.1.42.1:8790/api/v1/docs.md" in llms
    assert "http://10.1.42.1:8790/api/v1/openapi.json" in llms


def test_global_fastapi_docs_disabled(tmp_path):
    _, c = _client(tmp_path)
    assert c.get("/docs").status_code == 404
    assert c.get("/openapi.json").status_code == 404
    assert c.get("/redoc").status_code == 404


def test_auth_errors_point_to_docs(tmp_path):
    _, c = _client(tmp_path, api_enabled=True, api_key=KEY)
    r = c.get("/api/v1/fields", headers={"host": "10.1.42.1:8790"})
    assert r.status_code == 401
    assert "http://10.1.42.1:8790/api/v1/docs.md" in r.json()["detail"]
    assert r.headers["link"] == '<http://10.1.42.1:8790/api/v1/docs.md>; rel="describedby"'
    _, c2 = _client(tmp_path / "off")
    assert "/api/v1/docs.md" in c2.get("/api/v1/fields").json()["detail"]
