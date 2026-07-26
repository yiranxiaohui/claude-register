"""一次性探针：走到验证码界面，dump DOM 供分析。

用法：uv run scripts/probe_code_screen.py
跑完看 output/code_screen.html 和 output/code_screen.png。
"""

from __future__ import annotations

from playwright.sync_api import sync_playwright

from claude_register.anymail import AnyMailClient, load_dotenv
from claude_register.browser import (
    OUTPUT_DIR,
    fill_email,
    launch_browser,
    new_page,
    open_login,
    screenshot,
    wait_login_form,
)
from claude_register.console import log, prompt
from claude_register.mailbox import prepare_mailbox


def main() -> None:
    load_dotenv()
    client = AnyMailClient()
    mailbox, since = prepare_mailbox(client, domain="ckvlhj.xyz")
    log(f"邮箱：{mailbox.email}  since={since}")

    with sync_playwright() as p:
        browser = launch_browser(p)
        context, page = new_page(browser)
        open_login(page)
        wait_login_form(page)
        fill_email(page, mailbox.email)

        page.wait_for_timeout(5_000)
        screenshot(page, "code_screen.png")

        OUTPUT_DIR.mkdir(exist_ok=True)
        html_path = OUTPUT_DIR / "code_screen.html"
        html_path.write_text(page.content(), encoding="utf-8")
        log(f"DOM 已保存：{html_path}")

        # 把所有 input 的关键属性打出来
        inputs = page.locator("input").all()
        log(f"\n页面上有 {len(inputs)} 个 input：")
        for i, box in enumerate(inputs):
            attrs = box.evaluate(
                "el => ({type: el.type, name: el.name, id: el.id, "
                "placeholder: el.placeholder, autocomplete: el.autocomplete, "
                "maxLength: el.maxLength, inputMode: el.inputMode, "
                "ariaLabel: el.getAttribute('aria-label'), "
                "dataTestId: el.getAttribute('data-testid')})"
            )
            log(f"  [{i}] {attrs}")

        prompt("\n看完后按回车关闭…")
        context.close()
        browser.close()


if __name__ == "__main__":
    main()
