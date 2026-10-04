"""构建期固定 Camoufox 浏览器版本的脚本。"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "fetch_camoufox", ROOT / "deploy" / "fetch_camoufox.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _asset(full):
    return {
        "name": f"camoufox-{full}-lin.x86_64.zip",
        "browser_download_url": f"https://example.invalid/{full}.zip",
    }


def test_only_pinned_version_is_accepted(monkeypatch):
    mod = _load()
    monkeypatch.delenv("CAMOUFOX_VERSION", raising=False)
    # 跳过构造函数里的联网 fetch_latest，只测筛选逻辑
    fetcher = object.__new__(mod.PinnedCamoufoxUpdate)
    fetcher.arch = "x86_64"
    import re
    fetcher.pattern = re.compile(
        r"camoufox-(?P<version>.+)-(?P<release>.+)-lin\.x86_64\.zip"
    )
    assert fetcher.check_asset(_asset("156.0.1-beta.34")) is None
    assert fetcher.check_asset(_asset("152.0.4-beta.31")) is None
    version, url = fetcher.check_asset(_asset("152.0.4-beta.30"))
    assert version.full_string == "152.0.4-beta.30"
    assert url.endswith("152.0.4-beta.30.zip")

    monkeypatch.setenv("CAMOUFOX_VERSION", "152.0.4-beta.29")
    assert fetcher.check_asset(_asset("152.0.4-beta.30")) is None
    assert fetcher.check_asset(_asset("152.0.4-beta.29")) is not None


def test_dockerfile_uses_pinned_fetch():
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "camoufox fetch" not in dockerfile.replace("`camoufox fetch`", "")
    assert "deploy/fetch_camoufox.py" in dockerfile
    assert f"CAMOUFOX_VERSION={_load().DEFAULT_VERSION}" in dockerfile
