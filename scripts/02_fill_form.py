"""脚本 02：自动填写表单、提交、验证结果。

成功和失败两条路径都要跑 —— 只验证顺利路径的自动化脚本，
一旦线上出问题会"静默假装成功"，这是最危险的情况。

运行：uv run scripts/02_fill_form.py
"""

from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

URL = "https://www.saucedemo.com/"
USERNAME = "standard_user"
PASSWORD = "secret_sauce"  # saucedemo 首页公开的演示密码，非真实凭据
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"


def login(page: Page, username: str, password: str) -> None:
    """填写登录表单并提交。

    定位器优先用用户看得见的文字（占位符、按钮文字），而不是 CSS class。
    saucedemo 是 React 渲染的，class 名可能随构建变化，但界面上的
    "Username"、"Login" 这些字是稳定的。
    """
    page.goto(URL)
    page.get_by_placeholder("Username").fill(username)
    page.get_by_placeholder("Password").fill(password)
    page.get_by_role("button", name="Login").click()


def check_success(page: Page) -> None:
    """成功路径：正确凭据应当跳转到商品列表页。"""
    print("\n=== 成功路径 ===")
    login(page, USERNAME, PASSWORD)

    # expect() 会自动重试，直到条件成立或超时。
    # 这就是为什么不需要写 time.sleep(2) —— 页面快就立刻过，慢就多等一会。
    expect(page).to_have_url("https://www.saucedemo.com/inventory.html")

    # 光看 URL 不够：URL 对了但页面报错的情况是存在的。
    # 再确认一个真实商品渲染出来了，才算真的登录成功。
    expect(page.get_by_text("Sauce Labs Backpack")).to_be_visible()

    print(f"登录成功，已跳转到：{page.url}")

    shot = OUTPUT_DIR / "02_login_success.png"
    page.screenshot(path=shot)
    print(f"截图已保存：{shot}")


def check_failure(page: Page) -> None:
    """失败路径：错误密码应当留在登录页，并显示报错。

    注意这里的"错误"是我们主动预期的，所以要捕获并检查它。
    脚本其他地方的异常一律不包 try/except —— Playwright 的报错信息
    本身就写得很清楚，包起来反而把有用信息吃掉了。
    """
    print("\n=== 失败路径 ===")
    login(page, USERNAME, "wrong_password")

    # 错误提示以 "Epic sadface:" 开头，这是 saucedemo 的固定文案。
    error = page.locator("[data-test='error']")
    expect(error).to_be_visible()
    print(f"页面报错文案：{error.inner_text()}")

    # 没跳转，仍停在登录页 —— 这一条同样要验证。
    expect(page).to_have_url(URL)
    print(f"符合预期，仍停留在：{page.url}")

    shot = OUTPUT_DIR / "02_login_failed.png"
    page.screenshot(path=shot)
    print(f"截图已保存：{shot}")


def main() -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        page.set_default_timeout(15_000)

        check_success(page)
        check_failure(page)

        browser.close()

    print("\n两条路径都通过。")


if __name__ == "__main__":
    main()
