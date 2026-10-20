"""Real-browser tests: launch the Streamlit app and drive it with Playwright.

Opt-in (slow, needs a browser):
    pip install playwright && python -m playwright install chromium   # or reuse a local Chromium
    export TEST_DATABASE_URL=postgresql://...   # disposable database
    BLUFF_E2E=1 python -m pytest tests/e2e -q

Device presets emulate iPhone / iPad viewport, pixel ratio, touch and user agent in Chromium.
This is NOT the same as testing in Safari/WebKit; see README "Verifying on iPhone and iPad".
"""
from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from ..conftest import TEST_DSN

ROOT = Path(__file__).resolve().parents[2]
SHOTS = os.environ.get("BLUFF_E2E_SHOTS")
pytestmark = pytest.mark.skipif(not (os.environ.get("BLUFF_E2E") and TEST_DSN),
                                reason="set BLUFF_E2E=1 and TEST_DATABASE_URL to run browser tests")

# Target devices: iPhone 13 and newer (13 mini is the narrowest at 375 px), iPad, desktop.
DEVICES = {
    "iphone-13-mini": "iPhone 13 Mini",
    "iphone-13": "iPhone 13",
    "iphone-13-landscape": "iPhone 13 landscape",
    "iphone-17-pro-max": "iPhone 17 Pro Max",
    "iphone-17-pro-max-landscape": "iPhone 17 Pro Max landscape",
    "ipad-portrait": "iPad (gen 7)",
    "ipad-landscape": "iPad (gen 7) landscape",
    "desktop": None,
}


@pytest.fixture(scope="module")
def server(db):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {**os.environ, "DATABASE_URL": TEST_DSN}
    proc = subprocess.Popen([sys.executable, "-m", "streamlit", "run", "app.py", "--server.headless", "true",
                             "--server.port", str(port)], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            urllib.request.urlopen(f"{url}/_stcore/health", timeout=1)
            break
        except OSError:
            time.sleep(0.5)
    yield url
    proc.terminate()
    proc.wait(10)


@pytest.fixture(scope="module")
def browser():
    sync_api = pytest.importorskip("playwright.sync_api")
    pw = sync_api.sync_playwright().start()
    exe = "/opt/pw-browsers/chromium" if os.path.exists("/opt/pw-browsers/chromium") else None
    b = pw.chromium.launch(executable_path=exe)
    yield pw, b
    b.close()
    pw.stop()


def new_page(browser, device: str):
    pw, b = browser
    preset = DEVICES[device]
    opts = dict(pw.devices[preset]) if preset else {"viewport": {"width": 1440, "height": 900}}
    opts.pop("default_browser_type", None)
    ctx = b.new_context(**opts)
    page = ctx.new_page()
    page.set_default_timeout(20000)
    return page


def shot(page, name):
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=f"{SHOTS}/{name}.png")


def main_text(page) -> str:
    return page.locator('[data-testid="stMain"]').inner_text()


def wait_text(page, text, timeout=20000):
    page.get_by_text(text, exact=False).first.wait_for(state="visible", timeout=timeout)


def wait_idle(page):
    """Wait until Streamlit has finished the current rerun."""
    page.wait_for_function("!document.querySelector('[data-testid=\"stStatusWidget\"]')", timeout=20000)


def create_room(page, server, name):
    page.goto(server)
    page.get_by_label("Your name").first.fill(name)
    page.get_by_role("button", name="Create room").click()
    wait_text(page, "ROOM CODE")
    return re.search(r"room=([A-Z0-9]{6})", page.url).group(1)


def join_room(page, server, code, name):
    page.goto(f"{server}/?join={code}")
    page.get_by_role("tab", name="Join room").click()
    page.locator('input[aria-label="Your name"]').last.fill(name)
    page.get_by_role("button", name="Join room").click()
    wait_text(page, "ROOM CODE")


def segmented(page, label):
    """A segmented-control or pills option (both render as a button group)."""
    return page.locator('[data-testid="stButtonGroup"] button', has_text=label).first


def lock_in(page):
    page.get_by_role("button", name="Lock in move").click()


def no_horizontal_overflow(page) -> bool:
    return page.evaluate("""() => {
        const main = document.querySelector('[data-testid="stMain"]');
        const docOk = document.documentElement.scrollWidth <= window.innerWidth + 1;
        const mainOk = !main || main.scrollWidth <= main.clientWidth + 1;
        return docOk && mainOk;
    }""")


def hidden_scrollers(page) -> list[str]:
    """Elements that scroll sideways and hide content (e.g. a segmented control too wide for the phone)."""
    return page.evaluate("""() => {
        const main = document.querySelector('[data-testid="stMain"]');
        const out = [];
        for (const el of main.querySelectorAll('*')) {
            if (el.closest('[data-testid="stVegaLiteChart"], [data-testid="stTabs"] [role="tablist"]')) continue;
            const ox = getComputedStyle(el).overflowX;
            if ((ox === 'auto' || ox === 'scroll' || ox === 'hidden') && el.scrollWidth > el.clientWidth + 2
                && el.clientWidth > 2 && !el.classList.contains('bb-seat'))
                out.push((el.getAttribute('data-testid') || el.className || el.tagName).toString().slice(0, 40)
                         + ':' + el.scrollWidth + '>' + el.clientWidth);
        }
        return out;
    }""")


def small_touch_targets(page) -> list[str]:
    return page.evaluate("""() => {
        const main = document.querySelector('[data-testid="stMain"]');
        const out = [];
        for (const el of main.querySelectorAll('button, [role="tab"], [data-testid="stRadio"] label')) {
            const r = el.getBoundingClientRect();
            if (r.width === 0 || r.height === 0) continue;   // hidden
            if (r.height < 43.5) out.push((el.innerText || el.getAttribute('aria-label') || el.tagName).trim().slice(0, 30) + ':' + Math.round(r.height));
        }
        return out;
    }""")


# ---------------------------------------------------------------------------

def test_four_browser_sessions_play_a_full_game(server, browser, db):
    a = new_page(browser, "iphone-13")
    b = new_page(browser, "ipad-portrait")
    c = new_page(browser, "desktop")
    d = new_page(browser, "iphone-17-pro-max")
    code = create_room(a, server, "Ammu")
    for page, name in ((b, "Biju"), (c, "Chinnu"), (d, "Dev")):
        join_room(page, server, code, name)
    wait_text(a, "Traders 4/4")
    for page in (a, b, c, d):
        wait_idle(page)
        page.get_by_role("button", name="I'm ready").first.click()
    for page in (a, b, c, d):
        wait_text(page, "Your secret move")
    shot(a, "game-iphone-13")
    shot(b, "game-ipad-portrait")
    shot(c, "game-desktop")
    shot(d, "game-iphone-17-pro-max")

    # Round 1: Ammu buys 2 Thenga. Her pending move must stay invisible to everyone else.
    segmented(a, "Buy").click()
    wait_text(a, "Quantity")
    segmented(a, "2").click()
    wait_text(a, "coins after trade")
    lock_in(a)
    wait_text(a, "Move accepted")
    for page in (b, c, d):
        # The lock strip updates in place via polling (no full-page reload needed).
        page.locator(".bb-seat.done", has_text="Ammu").wait_for(timeout=10000)
        assert "Buy 2" not in main_text(page) and "bought 2" not in main_text(page)

    # Selections survive polling while a player is still choosing (Ammu's lock-in arrives meanwhile).
    segmented(b, "Buy").click()
    wait_text(b, "Quantity")
    segmented(b, "Kurumulaku").click()
    wait_idle(b)
    segmented(b, "3").click()
    wait_idle(b)
    time.sleep(4.5)  # at least two poll cycles
    for label in ("Buy", "Kurumulaku", "3"):
        assert segmented(b, label).get_attribute("aria-checked") == "true", label
    assert "coins after trade" in main_text(b)
    segmented(b, "Hold").click()
    wait_idle(b)

    # Near-simultaneous lock-ins, including a double-tap on Dev's button.
    lock_in(b)
    lock_in(c)
    d.get_by_role("button", name="Lock in move").dblclick()
    for page in (a, b, c, d):
        wait_text(page, "Round 2/8")
        wait_text(page, "Round 1 settled")
    with db.tx() as conn:
        n = conn.execute("select count(*) n from bluff.submissions s join bluff.rooms r on r.id = s.room_id "
                         "where r.code = %s and s.round_no = 1", (code,)).fetchone()["n"]
    assert n == 4
    for page in (a, b, c, d):
        page.get_by_role("tab", name="Last round").click()
        wait_text(page, "bought 2 Thenga @ 10")

    # Reconnect: Chinnu closes the tab and reopens their URL in a brand-new browser context.
    url_c = c.url
    c.context.close()
    c = new_page(browser, "desktop")
    c.goto(url_c)
    wait_text(c, "Round 2/8")
    wait_text(c, "Your secret move")

    # Rounds 2..8: everybody holds.
    for rnd in range(2, 9):
        for page in (a, b, c, d):
            wait_text(page, f"Round {rnd}/8")
            lock_in(page)
        if rnd < 8:
            for page in (a, b, c, d):
                wait_text(page, f"Round {rnd + 1}/8")
    winners = set()
    for page in (a, b, c, d):
        wait_text(page, "the bazaar!", timeout=30000)
        winners.add(page.locator(".bb-winner").inner_text())
        assert no_horizontal_overflow(page)
    assert len(winners) == 1  # all four devices agree on the winner (who wins depends on the random events)
    shot(a, "results-iphone-13")
    shot(c, "results-desktop")


@pytest.mark.parametrize("device", list(DEVICES))
def test_layout_fits_and_touch_targets(server, browser, db, device):
    from bluff import service
    page = new_page(browser, device)
    page.goto(server)
    wait_text(page, "The Malabar Trade Game")
    page.locator("input").first.wait_for()  # Streamlit renders the form after the header
    assert no_horizontal_overflow(page), "home page scrolls horizontally"
    font = page.evaluate("getComputedStyle(document.querySelector('input')).fontSize")
    assert float(font.rstrip("px")) >= 16, "inputs under 16px make iOS Safari zoom"
    # Put this browser in a running game.
    code = create_room(page, server, "Ammu")
    tokens = [service.join_room(db, code, n) for n in ("Biju", "Chinnu", "Dev")]
    for t in tokens:
        service.set_ready(db, code, t, True)
    wait_idle(page)
    page.get_by_role("button", name="I'm ready").first.click()
    wait_text(page, "Your secret move")
    segmented(page, "Buy").click()
    wait_text(page, "Quantity")
    wait_idle(page)
    assert no_horizontal_overflow(page), f"game page scrolls horizontally on {device}"
    assert not hidden_scrollers(page), f"content hidden in a sideways scroller on {device}: {hidden_scrollers(page)}"
    # Commodity names must never break mid-word (each name renders on one line).
    broken = page.evaluate("""() => [...document.querySelectorAll('.bb-tile .nm')]
        .filter(e => e.getBoundingClientRect().height > parseFloat(getComputedStyle(e).lineHeight) * 1.5)
        .map(e => e.innerText)""")
    assert not broken, f"commodity names wrap on {device}: {broken}"
    small = small_touch_targets(page)
    assert not small, f"touch targets under 44px on {device}: {small}"
    shot(page, f"layout-{device}")


def test_keyboard_focus_is_visible(server, browser):
    page = new_page(browser, "desktop")
    page.goto(server)
    wait_text(page, "The Malabar Trade Game")
    seen = []
    for _ in range(12):
        page.keyboard.press("Tab")
        info = page.evaluate("""() => {
            const el = document.activeElement;
            const cs = getComputedStyle(el);
            return {tag: el.tagName, text: (el.innerText || el.getAttribute('aria-label') || '').slice(0, 20),
                    outline: cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) >= 2,
                    shadow: cs.boxShadow !== 'none'};
        }""")
        seen.append(info)
        if info["tag"] == "BUTTON" and "Create room" in info["text"]:
            assert info["outline"] or info["shadow"], info
            return
    pytest.fail(f"never reached the Create room button by keyboard: {seen}")


def pick_mode(page, label):
    page.locator('[data-testid="stButtonGroup"] button', has_text=label).first.click()
    wait_idle(page)


def test_two_player_game_on_phone_and_tablet(server, browser):
    a = new_page(browser, "iphone-13")
    b = new_page(browser, "ipad-portrait")
    a.goto(server)
    wait_text(a, "How do you want to play?")
    pick_mode(a, "2 Players")
    wait_text(a, "Head-to-Head")
    a.get_by_label("Your name").first.fill("Ammu")
    a.get_by_role("button", name="Create room").click()
    wait_text(a, "ROOM CODE")
    code = re.search(r"room=([A-Z0-9]{6})", a.url).group(1)
    join_room(b, server, code, "Biju")
    wait_text(a, "Traders 2/2")
    for page in (a, b):
        wait_idle(page)
        page.get_by_role("button", name="I'm ready").first.click()
    for page in (a, b):
        wait_text(page, "2 Players · Full Bazaar")
        assert page.locator(".bb-strip .bb-seat").count() == 2
    for rnd in range(1, 9):
        for page in (a, b):
            wait_text(page, f"Round {rnd}/8")
            lock_in(page)
        if rnd < 8:
            for page in (a, b):
                wait_text(page, f"Round {rnd + 1}/8")
    winners = set()
    for page in (a, b):
        wait_text(page, "the bazaar!", timeout=30000)
        winners.add(page.locator(".bb-winner").inner_text())
        assert no_horizontal_overflow(page)
    assert len(winners) == 1


@pytest.mark.parametrize("device", ["iphone-13-mini", "iphone-13", "ipad-portrait", "desktop"])
def test_tutorial_in_browser(server, browser, device):
    page = new_page(browser, device)
    page.goto(server)
    wait_text(page, "How do you want to play?")
    pick_mode(page, "Tutorial")
    page.get_by_role("button", name="Start the tutorial").click()
    wait_text(page, "Lesson 1/")
    for k in (2, 3):
        page.get_by_role("button", name="Next lesson").click()
        wait_text(page, f"Lesson {k}/")  # wait for the re-render before the next click
    wait_text(page, "Your task:")
    # Play the buy lesson for real: Buy -> Thenga (default) -> 2.
    segmented(page, "Buy").click()
    wait_text(page, "Quantity")
    segmented(page, "2").click()
    wait_idle(page)
    assert no_horizontal_overflow(page) and not hidden_scrollers(page)
    assert not small_touch_targets(page), small_touch_targets(page)
    page.get_by_role("button", name="Lock in move").click()
    wait_text(page, "What happened")
    shot(page, f"tutorial-{device}")
    # Skip the rest and finish.
    for _ in range(12):
        if page.get_by_text("Tutorial complete").count():
            break
        before = page.locator(".bb-chip", has_text="Lesson").inner_text()
        has_result = page.get_by_text("What happened").count() > 0
        btn = page.get_by_role("button", name="Next lesson")
        (btn if btn.count() else page.get_by_role("button", name="Skip lesson")).first.click()
        page.wait_for_function(
            "([b, r]) => { const c = [...document.querySelectorAll('.bb-chip')].find(e => e.innerText.startsWith('Lesson'));"
            " return !c || c.innerText !== b || (r && !document.body.innerText.includes('What happened')); }",
            arg=[before, has_result], timeout=20000)
    wait_text(page, "Tutorial complete")
    page.get_by_role("button", name="Finish and go to the bazaar").click()
    wait_text(page, "How do you want to play?")
