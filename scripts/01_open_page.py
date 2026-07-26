"""脚本 01：打开网页并截图 —— 浏览器自动化的最小可运行例子。

运行：uv run scripts/01_open_page.py
"""

from pathlib import Path

from playwright.sync_api import sync_playwright

URL = "https://www.saucedemo.com/"
# 用脚本文件的位置推算项目根目录，这样在任何工作目录下运行结果都一样
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"


def main() -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)

    # sync_playwright() 管理的是 Playwright 的驱动进程。
    # 必须用 with：退出时它会关掉驱动，否则后台会残留 node 进程。
    with sync_playwright() as p:
        # headless=False 会弹出真实浏览器窗口，练手时能亲眼看到脚本在做什么。
        # 改成 True 就是无头模式，跑得更快，适合以后放进 CI。
        browser = p.chromium.launch(headless=False)

        # 一个 browser 下可以开多个 page。这里只需要一个。
        page = browser.new_page()

        # 默认超时是 30 秒，练手时等太久。15 秒足够 saucedemo 响应，
        # 又能让写错的定位器更快暴露出来。
        page.set_default_timeout(15_000)

        print(f"正在打开：{URL}")
        page.goto(URL)

        # goto 返回时页面已经加载完毕，不需要再 sleep。
        print(f"页面标题：{page.title()}")
        print(f"当前地址：{page.url}")

        shot = OUTPUT_DIR / "01_homepage.png"
        page.screenshot(path=shot)
        print(f"截图已保存：{shot}")

        browser.close()

    print("完成。")


if __name__ == "__main__":
    main()
