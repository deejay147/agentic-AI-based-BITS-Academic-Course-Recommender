"""Takes the README screenshots from a running dashboard (streamlit run app/app.py --server.port 8599)."""
import asyncio
import sys

from playwright.async_api import async_playwright

URL = "http://localhost:8599"
OUT = sys.argv[1] if len(sys.argv) > 1 else "docs/img/"


async def ask(pg, q, wait=6000):
    box = pg.get_by_placeholder("Ask about courses for this semester")
    await box.fill(q)
    await box.press("Enter")
    await pg.wait_for_timeout(wait)


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1440, "height": 1250})
        await pg.goto(URL, wait_until="networkidle")
        await pg.wait_for_timeout(7000)
        await pg.screenshot(path=OUT + "1_requirements.png")

        await pg.get_by_role("tab", name="Ask").click()
        await pg.wait_for_timeout(800)
        await ask(pg, "Suggest DELs related to AI.")
        await pg.screenshot(path=OUT + "2_ask_ai_dels.png")
        await ask(pg, "I like finance and economics, any OPEL?", 7000)   # newest answer shows first
        await pg.screenshot(path=OUT + "3_ask_blocked_topic.png")

        await pg.get_by_role("tab", name="Plan semester").click()
        await pg.wait_for_timeout(1000)
        ms = pg.locator("div[data-testid='stMultiSelect']").filter(has_text="Add any course").locator("input").first
        for code in ["CS F317"]:
            await ms.click()
            await ms.type(code)
            await pg.wait_for_timeout(500)
            await pg.keyboard.press("Enter")
            await pg.wait_for_timeout(1500)
        await pg.keyboard.press("Escape")
        await pg.wait_for_timeout(4000)
        await pg.mouse.wheel(0, 700)
        await pg.wait_for_timeout(1500)
        await pg.screenshot(path=OUT + "4_plan_semester.png")

        await pg.get_by_role("tab", name="Eligible courses").click()
        await pg.wait_for_timeout(2000)
        await pg.screenshot(path=OUT + "5_eligible.png")
        await b.close()

asyncio.run(main())
