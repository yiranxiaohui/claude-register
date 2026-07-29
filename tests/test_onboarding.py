"""建号引导页（onboarding）与登录后收尾。"""

from __future__ import annotations

from pathlib import Path

from claude_register import browser


class _Loc:
    def __init__(self, visible=True, count=1, checked=False, attrs=None):
        self._visible = visible
        self._count = count
        self._checked = checked
        self._attrs = dict(attrs or {})
        self.clicked = 0
        self.checked_calls = 0
        self.filled = None
        self.sequenced = None
        self.click_positions = []
        self.focused = 0
        self.first = self

    def count(self):
        return self._count

    def is_visible(self):
        return self._visible

    def is_checked(self):
        return self._checked

    def check(self, force=False):
        self.checked_calls += 1
        self._checked = True
        self._attrs["aria-checked"] = "true"
        self._attrs["data-state"] = "checked"

    def click(self, force=False, position=None):
        self.clicked += 1
        if position is not None:
            self.click_positions.append(position)
        self._checked = True
        self._attrs["aria-checked"] = "true"
        self._attrs["data-state"] = "checked"

    def focus(self):
        self.focused += 1

    def nth(self, i):
        return self

    def inner_text(self):
        return getattr(self, '_text', 'Continue')

    def get_attribute(self, name):
        return self._attrs.get(name)

    def fill(self, value):
        self.filled = value

    def press_sequentially(self, value, delay=0):
        self.sequenced = value
        self.filled = value
        # 模拟输入后 Continue 可点
        page = getattr(self, "_page", None)
        if page is not None:
            for loc in list(page.role_map.values()):
                if isinstance(loc, _Loc):
                    loc._enabled = True

    def press(self, key):
        self._pressed = getattr(self, "_pressed", [])
        self._pressed.append(key)

    def is_enabled(self):
        return getattr(self, "_enabled", True)

    def filter(self, has_text=None):
        return self

    def locator(self, sel):
        return self


class _Keyboard:
    def __init__(self, page):
        self.page = page
        self.presses = []

    def press(self, key):
        self.presses.append(key)
        # 空格切换当前 checkbox
        for loc in self.page.role_map.values():
            if isinstance(loc, _Loc):
                loc._checked = True
                loc._attrs["aria-checked"] = "true"


class _Page:
    def __init__(self, *, url="https://claude.ai/onboarding", role_map=None, text_map=None):
        self.url = url
        self.role_map = role_map or {}
        self.text_map = text_map or {}
        self.placeholder_map = {}
        self.label_map = {}
        self.test_id_map = {}
        self.locator_map = {}
        self.waited = []
        self.keyboard = _Keyboard(self)
        self.evaluate_calls = []
        self._evaluate_result = True

    def get_by_role(self, role, name=None, **kwargs):
        key = (role, name)
        return self.role_map.get(key, _Loc(visible=False, count=0))

    def get_by_text(self, text, exact=False):
        return self.text_map.get(text, _Loc(visible=False, count=0))

    def get_by_placeholder(self, text):
        loc = self.placeholder_map.get(text, _Loc(visible=False, count=0))
        loc._page = self
        return loc

    def get_by_label(self, text):
        return self.label_map.get(text, _Loc(visible=False, count=0))

    def get_by_test_id(self, test_id):
        return self.test_id_map.get(test_id, _Loc(visible=False, count=0))

    def locator(self, sel):
        if sel in self.locator_map:
            return self.locator_map[sel]
        # label filter 链：默认空
        return _Loc(visible=False, count=0)

    def wait_for_timeout(self, ms):
        self.waited.append(ms)

    def evaluate(self, script):
        self.evaluate_calls.append(script)
        return self._evaluate_result


def test_onboarding_visible_by_heading():
    page = _Page(
        role_map={
            ("heading", "Let's create your account"): _Loc(),
        }
    )
    assert browser.onboarding_visible(page) is True


def test_onboarding_visible_by_create_button():
    page = _Page(
        role_map={
            ("button", "Create account"): _Loc(),
        }
    )
    assert browser.onboarding_visible(page) is True


def test_onboarding_visible_includes_team_join():
    page = _Page(
        role_map={("button", "Continue with personal account"): _Loc()},
    )
    assert browser.onboarding_visible(page) is True


def test_onboarding_not_visible_on_empty_page():
    page = _Page(url="https://claude.ai/magic-link")
    assert browser.onboarding_visible(page) is False


def test_chat_home_visible_by_placeholder():
    page = _Page(url="https://claude.ai/new")
    page.placeholder_map["How can I help you today?"] = _Loc()
    assert browser.chat_home_visible(page) is True


def test_wait_post_auth_returns_onboarding(monkeypatch):
    page = _Page(
        role_map={
            ("heading", "Let's create your account"): _Loc(),
        }
    )
    assert browser.wait_post_auth(page, timeout_ms=1000) == "onboarding"


def test_wait_post_auth_returns_chat():
    page = _Page(url="https://claude.ai/new")
    page.placeholder_map["How can I help you today?"] = _Loc()
    assert browser.wait_post_auth(page, timeout_ms=1000) == "chat"


def test_wait_post_auth_unknown_after_timeout():
    page = _Page(url="https://claude.ai/magic-link")
    # timeout 很小，走一轮就结束
    assert browser.wait_post_auth(page, timeout_ms=1) == "unknown"
    assert page.waited  # 至少等过一次


def test_accept_terms_checks_box_and_clicks_create():
    checkbox = _Loc(checked=False)
    create_btn = _Loc()
    page = _Page(
        role_map={
            ("checkbox", None): checkbox,
            ("button", "Create account"): create_btn,
        }
    )
    assert browser.accept_terms_and_create_account(page) is True
    assert checkbox.checked_calls == 1 or checkbox.clicked >= 1
    assert checkbox.is_checked() is True
    assert create_btn.clicked >= 1


def test_accept_terms_retries_when_agree_error_shown(monkeypatch):
    """没勾上就点 Create 会出现红字；必须重试勾选。"""
    checkbox = _Loc(checked=False)
    create_btn = _Loc()
    page = _Page(
        role_map={
            ("checkbox", None): checkbox,
            ("button", "Create account"): create_btn,
        }
    )
    # 第一次点 Create 后假装红字还在，第二次消失
    calls = {"n": 0}
    real_error = browser._agree_error_visible

    def flaky_error(p):
        calls["n"] += 1
        return calls["n"] < 2

    # 让第一次 check 看起来失败：is_checked 前两次 False
    checks = {"n": 0}
    orig_is_checked = checkbox.is_checked

    def delayed_checked():
        checks["n"] += 1
        # _click 会把 _checked 设 True；这里模拟「点了但 UI 还没更新」一次
        if checks["n"] < 2:
            return False
        return True

    checkbox.is_checked = delayed_checked  # type: ignore
    monkeypatch.setattr(browser, "_agree_error_visible", flaky_error)

    assert browser.accept_terms_and_create_account(page) is True
    assert create_btn.clicked >= 1


def test_accept_terms_falls_back_to_label_left_click():
    create_btn = _Loc()
    label_row = _Loc()
    page = _Page(
        role_map={
            ("button", "Create account"): create_btn,
        },
        text_map={
            "I agree to Anthropic": label_row,
        },
    )
    # label() filter 链也返回同一行
    page.locator_map["label"] = label_row
    assert browser.accept_terms_and_create_account(page) is True
    # check() 或 click() 任一路径把条款勾上即可
    assert label_row.checked_calls >= 1 or label_row.clicked >= 1
    assert create_btn.clicked >= 1


def test_accept_terms_fails_without_controls():
    page = _Page()
    assert browser.accept_terms_and_create_account(page) is False


def test_terms_is_checked_reads_aria():
    box = _Loc(checked=False, attrs={"aria-checked": "true"})
    page = _Page(role_map={("checkbox", None): box})
    assert browser._terms_is_checked(page, box) is True


def test_agree_error_visible():
    page = _Page(text_map={"Agree to the terms to continue": _Loc()})
    assert browser._agree_error_visible(page) is True
    assert browser._agree_error_visible(_Page()) is False


def test_finish_after_auth_onboarding_path(monkeypatch):
    checkbox = _Loc(checked=False)
    create_btn = _Loc()

    page = _Page(
        role_map={
            ("checkbox", None): checkbox,
            ("button", "Create account"): create_btn,
            ("heading", "Let's create your account"): _Loc(),
        }
    )

    def chat_after_click(p):
        return create_btn.clicked >= 1

    def terms_until_click(p):
        return create_btn.clicked < 1

    monkeypatch.setattr(browser, "chat_home_visible", chat_after_click)
    monkeypatch.setattr(browser, "terms_create_visible", terms_until_click)
    monkeypatch.setattr(browser, "team_join_visible", lambda p: False)
    monkeypatch.setattr(browser, "use_case_visible", lambda p: False)
    monkeypatch.setattr(browser, "plan_select_visible", lambda p: False)
    monkeypatch.setattr(browser, "desktop_promo_visible", lambda p: False)
    monkeypatch.setattr(browser, "first_chat_intro_visible", lambda p: False)
    monkeypatch.setattr(browser, "onboarding_visible", terms_until_click)
    monkeypatch.setattr(browser, "screenshot", lambda p, name: Path(name))
    monkeypatch.setattr(browser, "maybe_fill_display_name", lambda p, name="Claude User": False)

    assert browser.finish_after_auth(page, timeout_ms=5_000) is True
    assert create_btn.clicked >= 1


def test_finish_after_auth_skips_team_join(monkeypatch):
    """域名已有 Team 时先点 Continue with personal account，再走条款。"""
    personal_btn = _Loc()
    checkbox = _Loc(checked=False)
    create_btn = _Loc()
    phase = {"n": 0}  # 0=team, 1=terms, 2=chat

    page = _Page(
        role_map={
            ("button", "Continue with personal account"): personal_btn,
            ("heading", "Join your team"): _Loc(),
            ("checkbox", None): checkbox,
            ("button", "Create account"): create_btn,
            ("heading", "Let's create your account"): _Loc(),
        }
    )

    def team_vis(p):
        return phase["n"] == 0

    def terms_vis(p):
        return phase["n"] == 1

    def chat_vis(p):
        return phase["n"] >= 2

    def click_personal(p):
        personal_btn.click()
        phase["n"] = 1
        return True

    def accept_terms(p):
        checkbox.check()
        create_btn.click()
        phase["n"] = 2
        return True

    monkeypatch.setattr(browser, "team_join_visible", team_vis)
    monkeypatch.setattr(browser, "use_case_visible", lambda p: False)
    monkeypatch.setattr(browser, "plan_select_visible", lambda p: False)
    monkeypatch.setattr(browser, "desktop_promo_visible", lambda p: False)
    monkeypatch.setattr(browser, "first_chat_intro_visible", lambda p: False)
    monkeypatch.setattr(browser, "terms_create_visible", terms_vis)
    monkeypatch.setattr(browser, "chat_home_visible", chat_vis)
    monkeypatch.setattr(browser, "onboarding_visible", lambda p: phase["n"] < 2)
    monkeypatch.setattr(browser, "continue_with_personal_account", click_personal)
    monkeypatch.setattr(browser, "accept_terms_and_create_account", accept_terms)
    monkeypatch.setattr(browser, "maybe_fill_display_name", lambda p, name="Claude User": False)
    monkeypatch.setattr(browser, "screenshot", lambda p, name: Path(name))
    # wait_post_auth 直接返回 onboarding
    monkeypatch.setattr(browser, "wait_post_auth", lambda p, timeout_ms=90_000: "onboarding")

    assert browser.finish_after_auth(page, timeout_ms=5_000) is True
    assert personal_btn.clicked == 1
    assert create_btn.clicked == 1


def test_team_join_visible():
    page = _Page(
        role_map={("heading", "Join your team"): _Loc()},
    )
    assert browser.team_join_visible(page) is True
    assert browser.team_join_visible(_Page()) is False


def test_continue_with_personal_account():
    btn = _Loc()
    page = _Page(
        role_map={("button", "Continue with personal account"): btn},
    )
    assert browser.continue_with_personal_account(page) is True
    assert btn.clicked == 1


def test_finish_after_auth_chat_path(monkeypatch):
    page = _Page(url="https://claude.ai/new")
    page.placeholder_map["How can I help you today?"] = _Loc()
    shots = []
    monkeypatch.setattr(browser, "screenshot", lambda p, name: shots.append(name) or Path(name))
    assert browser.finish_after_auth(page, timeout_ms=1_000) is True
    assert "logged_in.png" in shots

def test_use_case_visible():
    page = _Page(
        role_map={("heading", "How are you planning to use Claude?"): _Loc()},
    )
    assert browser.use_case_visible(page) is True
    assert browser.use_case_visible(_Page()) is False


def test_select_personal_use():
    btn = _Loc()
    page = _Page(
        role_map={("button", "For personal use"): btn},
    )
    assert browser.select_personal_use(page) is True
    assert btn.clicked == 1


def test_onboarding_visible_includes_use_case():
    page = _Page(text_map={"For personal use": _Loc()})
    assert browser.onboarding_visible(page) is True


def test_finish_after_auth_selects_personal_use(monkeypatch):
    use_btn = _Loc()
    checkbox = _Loc(checked=False)
    create_btn = _Loc()
    phase = {"n": 0}  # 0=use case, 1=terms, 2=chat

    def use_vis(p):
        return phase["n"] == 0

    def terms_vis(p):
        return phase["n"] == 1

    def chat_vis(p):
        return phase["n"] >= 2

    def select_use(p):
        use_btn.click()
        phase["n"] = 1
        return True

    def accept_terms(p):
        checkbox.check()
        create_btn.click()
        phase["n"] = 2
        return True

    monkeypatch.setattr(browser, "team_join_visible", lambda p: False)
    monkeypatch.setattr(browser, "use_case_visible", use_vis)
    monkeypatch.setattr(browser, "plan_select_visible", lambda p: False)
    monkeypatch.setattr(browser, "desktop_promo_visible", lambda p: False)
    monkeypatch.setattr(browser, "first_chat_intro_visible", lambda p: False)
    monkeypatch.setattr(browser, "terms_create_visible", terms_vis)
    monkeypatch.setattr(browser, "chat_home_visible", chat_vis)
    monkeypatch.setattr(browser, "onboarding_visible", lambda p: phase["n"] < 2)
    monkeypatch.setattr(browser, "select_personal_use", select_use)
    monkeypatch.setattr(browser, "accept_terms_and_create_account", accept_terms)
    monkeypatch.setattr(browser, "maybe_fill_display_name", lambda p, name="Claude User": False)
    monkeypatch.setattr(browser, "screenshot", lambda p, name: Path(name))
    monkeypatch.setattr(browser, "wait_post_auth", lambda p, timeout_ms=90_000: "onboarding")

    assert browser.finish_after_auth(_Page(), timeout_ms=5_000) is True
    assert use_btn.clicked == 1
    assert create_btn.clicked == 1

def test_plan_select_visible():
    page = _Page(
        role_map={("heading", "Plans that grow with you"): _Loc()},
    )
    assert browser.plan_select_visible(page) is True
    assert browser.plan_select_visible(_Page()) is False


def test_select_free_plan():
    btn = _Loc()
    page = _Page(
        role_map={("button", "Use Claude for free"): btn},
    )
    assert browser.select_free_plan(page) is True
    assert btn.clicked == 1


def test_onboarding_visible_includes_plan_select():
    page = _Page(
        role_map={("button", "Use Claude for free"): _Loc()},
    )
    assert browser.onboarding_visible(page) is True


def test_finish_after_auth_selects_free_plan(monkeypatch):
    free_btn = _Loc()
    phase = {"n": 0}  # 0=plan, 1=chat

    def plan_vis(p):
        return phase["n"] == 0

    def chat_vis(p):
        return phase["n"] >= 1

    def pick_free(p):
        free_btn.click()
        phase["n"] = 1
        return True

    monkeypatch.setattr(browser, "team_join_visible", lambda p: False)
    monkeypatch.setattr(browser, "use_case_visible", lambda p: False)
    monkeypatch.setattr(browser, "plan_select_visible", plan_vis)
    monkeypatch.setattr(browser, "desktop_promo_visible", lambda p: False)
    monkeypatch.setattr(browser, "first_chat_intro_visible", lambda p: False)
    monkeypatch.setattr(browser, "terms_create_visible", lambda p: False)
    monkeypatch.setattr(browser, "chat_home_visible", chat_vis)
    monkeypatch.setattr(browser, "onboarding_visible", lambda p: phase["n"] < 1)
    monkeypatch.setattr(browser, "select_free_plan", pick_free)
    monkeypatch.setattr(browser, "maybe_fill_display_name", lambda p, name="Claude User": False)
    monkeypatch.setattr(browser, "screenshot", lambda p, name: Path(name))
    monkeypatch.setattr(browser, "wait_post_auth", lambda p, timeout_ms=90_000: "onboarding")

    assert browser.finish_after_auth(_Page(), timeout_ms=5_000) is True
    assert free_btn.clicked == 1

def test_desktop_promo_visible():
    page = _Page(
        role_map={
            ("heading", "Get the most out of Claude on your desktop"): _Loc(),
        },
    )
    assert browser.desktop_promo_visible(page) is True
    assert browser.desktop_promo_visible(_Page()) is False


def test_skip_desktop_promo():
    btn = _Loc()
    page = _Page(role_map={("button", "Skip"): btn})
    assert browser.skip_desktop_promo(page) is True
    assert btn.clicked == 1


def test_onboarding_visible_includes_desktop_promo():
    page = _Page(
        role_map={("button", "Download for Windows"): _Loc()},
    )
    assert browser.onboarding_visible(page) is True


def test_finish_after_auth_skips_desktop_promo(monkeypatch):
    skip_btn = _Loc()
    phase = {"n": 0}

    def desk_vis(p):
        return phase["n"] == 0

    def chat_vis(p):
        return phase["n"] >= 1

    def do_skip(p):
        skip_btn.click()
        phase["n"] = 1
        return True

    monkeypatch.setattr(browser, "team_join_visible", lambda p: False)
    monkeypatch.setattr(browser, "use_case_visible", lambda p: False)
    monkeypatch.setattr(browser, "plan_select_visible", lambda p: False)
    monkeypatch.setattr(browser, "desktop_promo_visible", desk_vis)
    monkeypatch.setattr(browser, "first_chat_intro_visible", lambda p: False)
    monkeypatch.setattr(browser, "terms_create_visible", lambda p: False)
    monkeypatch.setattr(browser, "chat_home_visible", chat_vis)
    monkeypatch.setattr(browser, "onboarding_visible", lambda p: phase["n"] < 1)
    monkeypatch.setattr(browser, "skip_desktop_promo", do_skip)
    monkeypatch.setattr(browser, "maybe_fill_display_name", lambda p, name="Claude User": False)
    monkeypatch.setattr(browser, "screenshot", lambda p, name: Path(name))
    monkeypatch.setattr(browser, "wait_post_auth", lambda p, timeout_ms=90_000: "onboarding")

    assert browser.finish_after_auth(_Page(), timeout_ms=5_000) is True
    assert skip_btn.clicked == 1

def test_first_chat_intro_visible():
    page = _Page(
        role_map={("heading", "Before your first chat"): _Loc()},
    )
    assert browser.first_chat_intro_visible(page) is True
    assert browser.first_chat_intro_visible(_Page()) is False


def test_continue_first_chat_intro():
    btn = _Loc()
    btn._text = "Continue"
    page = _Page(role_map={("button", "Continue"): btn})
    # exact=True 调用 get_by_role("button", name="Continue") — our fake only has name kw
    # Playwright exact is separate; our get_by_role ignores exact. OK.
    assert browser.continue_first_chat_intro(page) is True
    assert btn.clicked == 1


def test_onboarding_visible_includes_first_chat_intro():
    page = _Page(text_map={"Before your first chat": _Loc()})
    assert browser.onboarding_visible(page) is True


def test_finish_after_auth_continues_first_chat_intro(monkeypatch):
    cont = _Loc()
    phase = {"n": 0}

    def intro_vis(p):
        return phase["n"] == 0

    def chat_vis(p):
        return phase["n"] >= 1

    def do_cont(p):
        cont.click()
        phase["n"] = 1
        return True

    monkeypatch.setattr(browser, "team_join_visible", lambda p: False)
    monkeypatch.setattr(browser, "use_case_visible", lambda p: False)
    monkeypatch.setattr(browser, "plan_select_visible", lambda p: False)
    monkeypatch.setattr(browser, "desktop_promo_visible", lambda p: False)
    monkeypatch.setattr(browser, "first_chat_intro_visible", intro_vis)
    monkeypatch.setattr(browser, "terms_create_visible", lambda p: False)
    monkeypatch.setattr(browser, "chat_home_visible", chat_vis)
    monkeypatch.setattr(browser, "onboarding_visible", lambda p: phase["n"] < 1)
    monkeypatch.setattr(browser, "continue_first_chat_intro", do_cont)
    monkeypatch.setattr(browser, "maybe_fill_display_name", lambda p, name="Claude User": False)
    monkeypatch.setattr(browser, "screenshot", lambda p, name: Path(name))
    monkeypatch.setattr(browser, "wait_post_auth", lambda p, timeout_ms=90_000: "onboarding")

    assert browser.finish_after_auth(_Page(), timeout_ms=5_000) is True
    assert cont.clicked == 1


def test_name_step_visible():
    page = _Page(
        role_map={("heading", "What's your name?"): _Loc()},
        text_map={"What's your name?": _Loc()},
    )
    page.placeholder_map["Enter your name"] = _Loc()
    assert browser.name_step_visible(page) is True
    assert browser.name_step_visible(_Page()) is False


def test_fill_display_name_and_continue():
    box = _Loc()
    btn = _Loc()
    btn._text = "Continue"
    btn._enabled = False
    page = _Page(
        role_map={
            ("heading", "What's your name?"): _Loc(),
            ("button", "Continue"): btn,
        },
        text_map={
            "What's your name?": _Loc(),
            "So Claude knows what to call you": _Loc(),
        },
    )
    page.placeholder_map["Enter your name"] = box
    box._page = page
    # 输入后 press_sequentially 会把 btn enable
    assert browser.fill_display_name_and_continue(page, name="Alex") is True
    assert box.sequenced == "Alex"
    assert btn.clicked >= 1


def test_onboarding_visible_includes_name_step():
    page = _Page(text_map={"What's your name?": _Loc()})
    page.placeholder_map["Enter your name"] = _Loc()
    assert browser.onboarding_visible(page) is True


def test_default_display_name_from_email_and_random():
    assert browser.default_display_name("jordan.lee@example.com") == "Jordan"
    # claude_xxx 邮箱走随机池
    n = browser.default_display_name("claude_385228bc@xyprohani.xyz")
    assert n in browser._DISPLAY_FIRST_NAMES


def test_finish_after_auth_fills_name(monkeypatch):
    phase = {"n": 0}
    clicks = {"name": 0}

    def name_vis(p):
        return phase["n"] < 1

    def do_name(p, name=None):
        clicks["name"] += 1
        phase["n"] += 1
        return True

    monkeypatch.setattr(browser, "wait_post_auth", lambda p, timeout_ms=90_000: "onboarding")
    monkeypatch.setattr(browser, "chat_home_visible", lambda p: phase["n"] >= 1)
    monkeypatch.setattr(browser, "team_join_visible", lambda p: False)
    monkeypatch.setattr(browser, "use_case_visible", lambda p: False)
    monkeypatch.setattr(browser, "plan_select_visible", lambda p: False)
    monkeypatch.setattr(browser, "desktop_promo_visible", lambda p: False)
    monkeypatch.setattr(browser, "first_chat_intro_visible", lambda p: False)
    monkeypatch.setattr(browser, "terms_create_visible", lambda p: False)
    monkeypatch.setattr(browser, "name_step_visible", name_vis)
    monkeypatch.setattr(browser, "onboarding_visible", lambda p: phase["n"] < 1)
    monkeypatch.setattr(browser, "fill_display_name_and_continue", do_name)
    monkeypatch.setattr(browser, "maybe_fill_display_name", lambda p, name=None: False)
    monkeypatch.setattr(browser, "screenshot", lambda p, name: Path(name))
    monkeypatch.setattr(
        browser,
        "accept_terms_and_create_account",
        lambda p: (_ for _ in ()).throw(AssertionError("should not terms")),
    )

    assert browser.finish_after_auth(_Page(), timeout_ms=5_000, display_name="Sam") is True
    assert clicks["name"] == 1


_STEP_PROBES = (
    "team_join_visible",
    "use_case_visible",
    "plan_select_visible",
    "desktop_promo_visible",
    "first_chat_intro_visible",
    "name_step_visible",
    "work_role_visible",
    "terms_create_visible",
)


def _quiet_other_steps(monkeypatch, keep=()):
    """把 keep 之外的 onboarding 步骤判定全部关掉。"""
    for probe in _STEP_PROBES:
        if probe not in keep:
            monkeypatch.setattr(browser, probe, lambda p: False)


def _counting_page(polls):
    """wait_for_timeout 会记账的假页面，用来模拟「轮询若干轮后请求才回来」。"""
    page = _Page()
    inner = page.wait_for_timeout

    def counting_wait(ms):
        polls["n"] += 1
        inner(ms)

    page.wait_for_timeout = counting_wait
    return page


def test_finish_after_auth_waits_out_personal_account_spinner(monkeypatch):
    """点完 Continue with personal account 后按钮变 spinner，不能当成失败。

    复现 run 6：点击成功 → 请求飞行中 → 按钮文字被 spinner 顶掉导致定位不到，
    而 heading「Join your team」还在 → 旧代码 1.5s 后直接 return False。
    """
    clicks = {"n": 0}
    polls = {"n": 0}
    page = _counting_page(polls)

    def team_vis(p):
        if clicks["n"] == 0:
            return True
        return polls["n"] < 3  # 请求飞行期间标题仍在

    def click_personal(p):
        if clicks["n"] == 0:
            clicks["n"] += 1
            return True
        return False  # 按钮只剩 spinner，三条定位策略全落空

    def chat_vis(p):
        return clicks["n"] >= 1 and polls["n"] >= 3

    _quiet_other_steps(monkeypatch, keep=("team_join_visible",))
    monkeypatch.setattr(browser, "team_join_visible", team_vis)
    monkeypatch.setattr(browser, "continue_with_personal_account", click_personal)
    monkeypatch.setattr(browser, "chat_home_visible", chat_vis)
    monkeypatch.setattr(browser, "onboarding_visible", lambda p: not chat_vis(p))
    monkeypatch.setattr(browser, "maybe_fill_display_name", lambda p, name=None: False)
    monkeypatch.setattr(browser, "screenshot", lambda p, name: Path(name))
    monkeypatch.setattr(browser, "wait_post_auth", lambda p, timeout_ms=90_000: "onboarding")

    assert browser.finish_after_auth(page, timeout_ms=5_000) is True
    assert clicks["n"] == 1


def test_finish_after_auth_waits_out_use_case_spinner(monkeypatch):
    """同样的 loading 中间态在用途选择页也不能当成失败。"""
    clicks = {"n": 0}
    polls = {"n": 0}
    page = _counting_page(polls)

    def use_vis(p):
        if clicks["n"] == 0:
            return True
        return polls["n"] < 3

    def click_use(p):
        if clicks["n"] == 0:
            clicks["n"] += 1
            return True
        return False

    def chat_vis(p):
        return clicks["n"] >= 1 and polls["n"] >= 3

    _quiet_other_steps(monkeypatch, keep=("use_case_visible",))
    monkeypatch.setattr(browser, "use_case_visible", use_vis)
    monkeypatch.setattr(browser, "select_personal_use", click_use)
    monkeypatch.setattr(browser, "chat_home_visible", chat_vis)
    monkeypatch.setattr(browser, "onboarding_visible", lambda p: not chat_vis(p))
    monkeypatch.setattr(browser, "maybe_fill_display_name", lambda p, name=None: False)
    monkeypatch.setattr(browser, "screenshot", lambda p, name: Path(name))
    monkeypatch.setattr(browser, "wait_post_auth", lambda p, timeout_ms=90_000: "onboarding")

    assert browser.finish_after_auth(page, timeout_ms=5_000) is True
    assert clicks["n"] == 1


def test_finish_after_auth_gives_up_when_team_join_never_clears(monkeypatch):
    """请求真挂死时，要在多试几次之后才放弃，且不能无限轮询。"""
    shots = []
    calls = {"n": 0}

    def click_personal(p):
        calls["n"] += 1
        return calls["n"] == 1  # 只有第一次点得到，之后永远是 spinner

    _quiet_other_steps(monkeypatch, keep=("team_join_visible",))
    monkeypatch.setattr(browser, "team_join_visible", lambda p: True)
    monkeypatch.setattr(browser, "continue_with_personal_account", click_personal)
    monkeypatch.setattr(browser, "chat_home_visible", lambda p: False)
    monkeypatch.setattr(browser, "onboarding_visible", lambda p: True)
    monkeypatch.setattr(browser, "maybe_fill_display_name", lambda p, name=None: False)
    monkeypatch.setattr(browser, "screenshot", lambda p, name: shots.append(name) or Path(name))
    monkeypatch.setattr(browser, "wait_post_auth", lambda p, timeout_ms=90_000: "onboarding")

    assert browser.finish_after_auth(_Page(), timeout_ms=5_000) is False
    assert "team_join_failed.png" in shots
    assert calls["n"] >= 4  # 放弃前确实反复重试过，而不是 1.5s 就判死


def test_continue_with_personal_account_finds_button_in_loading_state():
    """按钮进 loading 态后文字被 spinner 顶掉，仍要能定位到。

    run 6 截图实测：点击后按钮内文字消失，只剩转圈，
    get_by_role(name="Continue with personal account") 三条策略全部落空。
    """
    btn = _Loc()
    btn._text = ""  # spinner 顶掉了文字
    page = _Page(role_map={("heading", "Join your team"): _Loc()})
    page.test_id_map["continue-with-personal-account"] = btn

    assert browser.continue_with_personal_account(page) is True
    assert btn.clicked == 1


def test_finish_after_auth_does_not_mistake_blank_transition_for_completion(monkeypatch):
    """客户端路由切换时 DOM 短暂空白，不能当成「已离开 onboarding」。

    复现 run 7：点完 Continue with personal account 后页面正在切路由，
    所有探针落空且 onboarding_visible 为 False，但 URL 仍是 /onboarding
    —— 旧代码据此 return True，紧接着用途选择页才渲染出来。
    """
    polls = {"n": 0}
    clicks = {"team": 0, "use": 0}
    page = _counting_page(polls)
    page.url = "https://claude.ai/onboarding"

    def team_vis(p):
        return clicks["team"] == 0

    def click_team(p):
        clicks["team"] += 1
        return True

    # 点完后有 2 轮空白期，之后用途选择页才渲染
    def use_vis(p):
        return clicks["team"] >= 1 and polls["n"] >= 3 and clicks["use"] == 0

    def click_use(p):
        clicks["use"] += 1
        return True

    def chat_vis(p):
        return clicks["use"] >= 1

    def onboarding_vis(p):
        # 空白期里连 onboarding 都认不出来
        return team_vis(p) or use_vis(p)

    _quiet_other_steps(monkeypatch, keep=("team_join_visible", "use_case_visible"))
    monkeypatch.setattr(browser, "team_join_visible", team_vis)
    monkeypatch.setattr(browser, "continue_with_personal_account", click_team)
    monkeypatch.setattr(browser, "use_case_visible", use_vis)
    monkeypatch.setattr(browser, "select_personal_use", click_use)
    monkeypatch.setattr(browser, "chat_home_visible", chat_vis)
    monkeypatch.setattr(browser, "onboarding_visible", onboarding_vis)
    monkeypatch.setattr(browser, "maybe_fill_display_name", lambda p, name=None: False)
    monkeypatch.setattr(browser, "screenshot", lambda p, name: Path(name))
    monkeypatch.setattr(browser, "wait_post_auth", lambda p, timeout_ms=90_000: "onboarding")

    assert browser.finish_after_auth(page, timeout_ms=5_000) is True
    assert clicks["use"] == 1, "空白期被误判成完成，用途选择页没走到"


def test_finish_after_auth_leaves_when_url_is_no_longer_onboarding(monkeypatch):
    """真的跳出 onboarding（URL 已变）时仍要正常收尾，不能死等。"""
    clicks = {"n": 0}
    page = _Page(url="https://claude.ai/onboarding")

    def team_vis(p):
        return clicks["n"] == 0

    def click_team(p):
        clicks["n"] += 1
        page.url = "https://claude.ai/new"  # 路由已切走
        return True

    _quiet_other_steps(monkeypatch, keep=("team_join_visible",))
    monkeypatch.setattr(browser, "team_join_visible", team_vis)
    monkeypatch.setattr(browser, "continue_with_personal_account", click_team)
    monkeypatch.setattr(browser, "chat_home_visible", lambda p: False)
    monkeypatch.setattr(browser, "onboarding_visible", lambda p: False)
    monkeypatch.setattr(browser, "maybe_fill_display_name", lambda p, name=None: False)
    monkeypatch.setattr(browser, "screenshot", lambda p, name: Path(name))
    monkeypatch.setattr(browser, "wait_post_auth", lambda p, timeout_ms=90_000: "onboarding")

    assert browser.finish_after_auth(page, timeout_ms=5_000) is True


def test_work_role_visible():
    page = _Page(
        role_map={("heading", "What kind of work do you do?"): _Loc()},
        text_map={
            "What kind of work do you do?": _Loc(),
            "Set up later": _Loc(),
        },
    )
    assert browser.work_role_visible(page) is True
    assert browser.work_role_visible(_Page()) is False


def test_skip_work_role():
    btn = _Loc()
    page = _Page(
        role_map={("button", "Set up later"): btn},
        text_map={"Set up later": btn},
    )
    assert browser.skip_work_role(page) is True
    assert btn.clicked >= 1


def test_onboarding_visible_includes_work_role():
    page = _Page(text_map={"What kind of work do you do?": _Loc()})
    assert browser.onboarding_visible(page) is True


def test_finish_after_auth_skips_work_role(monkeypatch):
    phase = {"n": 0}
    clicks = {"skip": 0}

    def role_vis(p):
        return phase["n"] < 1

    def do_skip(p):
        clicks["skip"] += 1
        phase["n"] += 1
        return True

    monkeypatch.setattr(browser, "wait_post_auth", lambda p, timeout_ms=90_000: "onboarding")
    monkeypatch.setattr(browser, "chat_home_visible", lambda p: phase["n"] >= 1)
    monkeypatch.setattr(browser, "team_join_visible", lambda p: False)
    monkeypatch.setattr(browser, "use_case_visible", lambda p: False)
    monkeypatch.setattr(browser, "plan_select_visible", lambda p: False)
    monkeypatch.setattr(browser, "desktop_promo_visible", lambda p: False)
    monkeypatch.setattr(browser, "first_chat_intro_visible", lambda p: False)
    monkeypatch.setattr(browser, "name_step_visible", lambda p: False)
    monkeypatch.setattr(browser, "terms_create_visible", lambda p: False)
    monkeypatch.setattr(browser, "work_role_visible", role_vis)
    monkeypatch.setattr(browser, "onboarding_visible", lambda p: phase["n"] < 1)
    monkeypatch.setattr(browser, "skip_work_role", do_skip)
    monkeypatch.setattr(browser, "maybe_fill_display_name", lambda p, name=None: False)
    monkeypatch.setattr(browser, "screenshot", lambda p, name: Path(name))
    monkeypatch.setattr(
        browser,
        "accept_terms_and_create_account",
        lambda p: (_ for _ in ()).throw(AssertionError("should not terms")),
    )

    assert browser.finish_after_auth(_Page(), timeout_ms=5_000) is True
    assert clicks["skip"] == 1
