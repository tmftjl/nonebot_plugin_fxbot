"""使用构建产物和假 API 验证桌面、移动端聊天界面，不连接真实 Bot 或模型。"""

from __future__ import annotations

import json
import asyncio
import argparse
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "console" / "web" / "dist"
OUTPUT = ROOT / "temp"
CONVERSATION = "11111111-1111-4111-8111-111111111111"


async def main(channel: str | None = None) -> None:
    """加载真实页面、发送文字、恢复历史，并检查移动端没有横向溢出。"""
    items = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, channel=channel)
        page = await browser.new_page(viewport={"width": 1440, "height": 1050})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        async def route_request(route):
            """只在测试浏览器中提供本地资源及可控接口响应。"""
            request = route.request
            path = urlparse(request.url).path
            if path == "/fxbot/auth/me":
                await route.fulfill(json={"username": "root", "must_change_password": False})
            elif path == "/fxbot/config/tabs":
                await route.fulfill(json=[])
            elif path == "/fxbot/local-chat/options":
                await route.fulfill(json={"personas": ["默认人格"], "providers": ["测试模型"]})
            elif path.startswith("/fxbot/local-chat/"):
                assert request.headers.get("authorization") == "Bearer ui-test-only"
                if path.endswith("/messages"):
                    payload = request.post_data_json
                    items.extend(
                        [
                            {"id": payload["message_id"], "role": "user", "content": payload["text"]},
                            {
                                "id": "reply",
                                "role": "assistant",
                                "content": "你好！这是独立网页会话的回复，不需要 QQ Bot 在线。",
                            },
                        ]
                    )
                    events = [
                        {"type": "accepted", "id": items[-2]["id"], "content": items[-2]["content"]},
                        {"type": "message", "id": "reply", "content": items[-1]["content"]},
                        {"type": "done"},
                    ]
                    await route.fulfill(
                        content_type="application/x-ndjson",
                        body="\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n",
                    )
                elif request.method == "DELETE":
                    items.clear()
                    await route.fulfill(json={"success": True, "history_cleared": True})
                elif path.endswith("/stop"):
                    await route.fulfill(json={"stopped": True})
                else:
                    await route.fulfill(
                        json={
                            "conversation": CONVERSATION,
                            "tenant": "webui:console:private:test",
                            "items": items,
                            "running": False,
                            "message_id": None,
                            "persona": "",
                            "provider": "",
                            "effective_persona": "FxBot",
                            "can_chat": True,
                        }
                    )
            elif path.startswith("/fxbot/assets/"):
                asset = DIST / "assets" / Path(path).name
                await route.fulfill(
                    path=asset, content_type="text/css" if asset.suffix == ".css" else "application/javascript"
                )
            elif path.rstrip("/") == "/fxbot":
                await route.fulfill(path=DIST / "index.html", content_type="text/html")
            else:
                await route.fulfill(status=404, body="")

        await page.route("**/*", route_request)
        await page.add_init_script("""
            localStorage.setItem('fxbot_console_token', 'ui-test-only');
            localStorage.setItem('fxbot-console-active', 'local-chat');
            localStorage.setItem('fxbot-console-local-conversations', JSON.stringify([
                { id: '11111111-1111-4111-8111-111111111111', title: '独立会话测试' }
            ]));
            localStorage.setItem('fxbot-console-local-active', '11111111-1111-4111-8111-111111111111');
        """)
        await page.goto("http://console.test/fxbot/")
        await page.get_by_role("heading", name="直接对话", exact=True).wait_for()
        await page.get_by_role("textbox", name="消息内容").fill("你好，我们可以直接在控制台聊天吗？")
        await page.get_by_role("button", name="发送", exact=True).click()
        await page.get_by_text("你好！这是独立网页会话的回复，不需要 QQ Bot 在线。", exact=True).wait_for()
        OUTPUT.mkdir(exist_ok=True)
        await page.screenshot(path=OUTPUT / "local-chat-desktop.png", full_page=True)
        await page.reload()
        await page.get_by_text("你好！这是独立网页会话的回复，不需要 QQ Bot 在线。", exact=True).wait_for()
        await page.set_viewport_size({"width": 390, "height": 844})
        await page.wait_for_function("document.querySelector('.app-sidebar').getBoundingClientRect().right <= 1")
        await page.evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
        await page.screenshot(path=OUTPUT / "local-chat-mobile.png", full_page=True)
        assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "移动端横向溢出"
        assert not errors, errors
        await browser.close()
    print("UI passed: send, history restoration, desktop, mobile; screenshots in temp/local-chat-*.png")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", choices=("chrome", "msedge"), help="使用已安装的浏览器替代 Playwright Chromium")
    asyncio.run(main(parser.parse_args().channel))
