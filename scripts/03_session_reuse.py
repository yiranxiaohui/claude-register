"""脚本 03：登录会话复用 + 等待机制。

第一次运行会真的登录一次，把 cookie 和 localStorage 存进 .auth/state.json；
之后再运行就直接带着这份状态开浏览器，跳过登录页。

运行：uv run scripts/03_session_reuse.py
（想重新走一遍登录流程，把 .auth/state.json 删掉再跑）
"""

from pathlib import Path

from playwright.sync_api import Browser, Page, expect, sync_playwright

URL = "https://www.saucedemo.com/"
INVENTORY_URL = "https://www.saucedemo.com/inventory.html"
USERNAME = "standard_user"
PASSWORD = "secret_sauce"  # saucedemo 首页公开的演示密码，非真实凭据

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"
AUTH_FILE = ROOT / ".auth" / "state.json"


def save_login_state(browser: Browser) -> None:
    """登录一次，把浏览器状态存成文件。

    context 是浏览器里一个隔离的"身份空间"，各自有独立的 cookie 和
    localStorage。storage_state() 就是把这个空间的内容导出来。
    """
    print("没有找到已保存的登录状态，先登录一次……")

    context = browser.new_context()
    context.set_default_timeout(15_000)
    page = context.new_page()

    page.goto(URL)
    page.get_by_placeholder("Username").fill(USERNAME)
    page.get_by_placeholder("Password").fill(PASSWORD)
    page.get_by_role("button", name="Login").click()
    expect(page).to_have_url(INVENTORY_URL)

    AUTH_FILE.parent.mkdir(exist_ok=True)
    context.storage_state(path=AUTH_FILE)
    print(f"登录状态已保存：{AUTH_FILE}")

    context.close()


def use_saved_state(browser: Browser) -> Page:
    """带着已保存的状态开一个新 context，直接进入已登录页面。"""
    print("正在用已保存的状态打开浏览器……")

    # storage_state 参数就是复用的关键：新 context 一出生就带着那些 cookie。
    context = browser.new_context(storage_state=AUTH_FILE)
    context.set_default_timeout(15_000)
    page = context.new_page()

    # 直接访问需要登录才能看的页面。如果状态没生效，saucedemo 会把我们
    # 踢回登录页 —— 下面这条断言就是在验证"复用真的成功了"。
    page.goto(INVENTORY_URL)
    expect(page).to_have_url(INVENTORY_URL)
    expect(page.get_by_text("Sauce Labs Backpack")).to_be_visible()

    print("跳过登录页，直接进入了商品列表")
    page.screenshot(path=OUTPUT_DIR / "03_logged_in.png")

    return page


def demo_waiting(page: Page) -> None:
    """演示自动等待：两个会改变页面的操作，都不用 sleep。"""
    print("\n=== 等待机制演示 ===")

    # 操作一：切换排序。选完之后整个商品列表会重新渲染。
    page.get_by_role("combobox").select_option("lohi")  # 价格从低到高
    # 排序生效后第一个商品会变成最便宜的那个。
    # expect 会自动重试到列表刷新完，不需要我们猜"要等几秒"。
    first_item = page.locator("[data-test='inventory-item-name']").first
    expect(first_item).to_have_text("Sauce Labs Onesie")
    print(f"已按价格升序排列，第一个商品：{first_item.inner_text()}")

    # 操作二：加购物车。角标数字是异步更新的。
    page.get_by_role("button", name="Add to cart").first.click()
    badge = page.locator(".shopping_cart_badge")
    expect(badge).to_have_text("1")
    print(f"已加入购物车，角标显示：{badge.inner_text()}")

    # 对比一下：如果这里写 time.sleep(2)，页面快的时候白等 2 秒，
    # 网络慢的时候又不够用，脚本会时灵时不灵。expect 两个问题都没有。

    page.screenshot(path=OUTPUT_DIR / "03_cart.png")
    print(f"截图已保存：{OUTPUT_DIR / '03_cart.png'}")


def main() -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)

        if not AUTH_FILE.exists():
            save_login_state(browser)
        else:
            print(f"找到已保存的登录状态：{AUTH_FILE}")

        page = use_saved_state(browser)
        demo_waiting(page)

        browser.close()

    print("\n完成。再跑一次会直接复用状态，跳过登录。")


if __name__ == "__main__":
    main()
