"""按固定版本安装 Camoufox 浏览器（替代镜像构建里的 `camoufox fetch`）。

`camoufox fetch` 总是装 GitHub 上最新的浏览器版本，与锁定的 Python 包
`camoufox==0.4.11` 不一定兼容：2026-10-03 发布的 156.0.1-beta.34 去掉了
`navigator.appCodeName`，0.4.11 生成的指纹配置里仍有该属性，启动即报
「Unknown property navigator.appCodeName in config」，注册与接管全部失效。

因此构建时固定到已在生产验证过的版本；升级浏览器时请同时确认 Python 包兼容，
再改 CAMOUFOX_VERSION（构建参数 / 环境变量）。其余步骤（GeoIP 库、默认插件）
仍走官方 fetch 命令。
"""
from __future__ import annotations

import os
import sys

from camoufox import __main__ as camoufox_cli
from camoufox.pkgman import installed_verstr

DEFAULT_VERSION = "152.0.4-beta.30"


def pinned_version() -> str:
    return os.environ.get("CAMOUFOX_VERSION", "").strip() or DEFAULT_VERSION


class PinnedCamoufoxUpdate(camoufox_cli.CamoufoxUpdate):
    """只接受与固定版本完全一致的发布资产。"""

    def check_asset(self, asset):
        found = super().check_asset(asset)
        if found and found[0].full_string == pinned_version():
            return found
        return None

    def missing_asset_error(self) -> None:
        raise SystemExit(
            f"GitHub releases 中找不到 Camoufox {pinned_version()} 的安装包"
        )


def main() -> int:
    camoufox_cli.CamoufoxUpdate = PinnedCamoufoxUpdate
    camoufox_cli.fetch.main(args=[], standalone_mode=False)
    installed = installed_verstr()
    if installed != pinned_version():
        print(f"Camoufox 版本不符：期望 {pinned_version()}，实际 {installed}", file=sys.stderr)
        return 1
    print(f"Camoufox {installed} 已安装（固定版本）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
