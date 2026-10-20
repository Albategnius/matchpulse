"""Record the demo video with Playwright (captions on screen, no audio).

Usage:  uvicorn matchpulse.api:app --port 8000 &   then   python scripts/record_demo.py [base_url]
Writes  video/matchpulse_demo.webm  (convert with ffmpeg, see README in video/).
"""

import asyncio
import shutil
import sys
from pathlib import Path

from playwright.async_api import async_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
LIVE = "https://matchpulse.politefield-ec1b9bf6.southeastasia.azurecontainerapps.io"
OUT = Path(__file__).resolve().parents[1] / "video"

CAPTION_JS = """
(text) => {
  let el = document.getElementById('__cap');
  if (!el) {
    el = document.createElement('div');
    el.id = '__cap';
    el.style.cssText = 'position:fixed;left:50%;bottom:28px;transform:translateX(-50%);max-width:1100px;' +
      'background:rgba(16,28,22,.92);color:#fff;font:600 22px/1.35 system-ui,sans-serif;padding:14px 22px;' +
      'border-radius:10px;z-index:9999;text-align:center;box-shadow:0 6px 24px rgba(0,0,0,.25)';
    document.body.appendChild(el);
  }
  el.style.display = text ? 'block' : 'none';
  el.textContent = text;
}
"""

CARD_JS = """
([title, sub]) => {
  let el = document.getElementById('__card');
  if (!el) {
    el = document.createElement('div');
    el.id = '__card';
    el.style.cssText = 'position:fixed;inset:0;background:#16241D;color:#EEF1EC;z-index:10000;display:flex;' +
      'flex-direction:column;align-items:center;justify-content:center;gap:18px;text-align:center;padding:40px;' +
      'font-family:system-ui,sans-serif';
    document.body.appendChild(el);
  }
  if (!title) { el.remove(); return; }
  el.innerHTML = '<div style="font:800 64px/1 system-ui;letter-spacing:.04em">' + title + '</div>' +
                 '<div style="font:400 24px/1.5 system-ui;max-width:1000px;opacity:.9">' + sub + '</div>';
}
"""


async def main() -> None:
    OUT.mkdir(exist_ok=True)
    tmp = OUT / "_raw"
    shutil.rmtree(tmp, ignore_errors=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900},
                                        record_video_dir=str(tmp), record_video_size={"width": 1440, "height": 900})
        page = await ctx.new_page()

        async def cap(text, secs=0.0):
            await page.evaluate(CAPTION_JS, text)
            if secs:
                await page.wait_for_timeout(int(secs * 1000))

        async def card(title, sub, secs):
            await page.evaluate(CARD_JS, [title, sub])
            await page.wait_for_timeout(int(secs * 1000))
            await page.evaluate(CARD_JS, ["", ""])

        async def scroll_to(selector, block="start"):
            await page.evaluate("([s,b]) => document.querySelector(s).scrollIntoView({behavior:'smooth', block:b})",
                                [selector, block])
            await page.wait_for_timeout(900)

        async def analysed():
            await page.wait_for_function("document.getElementById('run').textContent.trim() === 'Analyse match'")
            await page.wait_for_selector(".moment")
            await page.wait_for_timeout(500)

        await page.goto(BASE)
        await analysed()

        # 1. Title (0:00-0:07)
        await card("MATCHPULSE", "Beyond the scoreline — explain why the game changed.<br>"
                   "Evidence-first match intelligence on synthetic football events.<br><br>"
                   "<span style='font-size:18px;opacity:.75'>Microsoft Agent Framework · Microsoft Foundry-ready · Azure Container Apps<br>"
                   "All teams, players and events are synthetic.</span>", 7)

        # 2. Scenario + pulse (0:07-0:24)
        await cap("A reproducible synthetic match: 'Momentum swing', seed 7. The patterns were injected on purpose, so we know the ground truth.", 6)
        await scroll_to(".pulse")
        await cap("The match pulse: which team generated more attacking threat in each 5-minute window. Shaded bands are detected shifts.", 7)

        # 3. Evidence drill-down (0:24-0:44)
        await page.click(".moment >> nth=1")
        await page.wait_for_timeout(600)
        await scroll_to("#evidence", "start")
        await cap("Shift M2: control moves to Harbour City. Momentum index goes from -0.8 to +0.8 against the baseline window.", 6)
        await page.evaluate("window.scrollBy({top: 360, behavior: 'smooth'})")
        await cap("Every claim is backed by the supporting events: minute, team, synthetic player ID, event type, outcome and location.", 6)

        # 4. Audience modes (0:44-1:08)
        await scroll_to("#story")
        await cap("Same verified insight, three audiences. Fan mode: one key moment, why it matters, a short recap.", 7)
        await page.click("[data-mode=analyst]")
        await page.wait_for_timeout(400)
        await cap("Analyst mode: before/after metrics, the hypothesis labelled as such, confidence, limitations and the next question.", 7)
        await page.evaluate("window.scrollBy({top: 420, behavior: 'smooth'})")
        await page.wait_for_timeout(2500)
        await scroll_to("#story")
        await page.click("[data-mode=broadcast]")
        await page.wait_for_timeout(400)
        await cap("Broadcast mode: time-stamped narration, lower-thirds and a JSON overlay payload for the graphics pipeline.", 7)

        # 5. Bahasa Indonesia (1:08-1:15)
        await page.click("[data-lang=id]")
        await analysed()
        await scroll_to("#story")
        await cap("The same analysis in Bahasa Indonesia — content and format change, not just the heading.", 6)

        # 6. Reviewer catches a faulty model (1:15-1:38)
        await page.click("[data-lang=en]")
        await analysed()
        await page.select_option("#writer", "fault_injection")
        await page.click("#run")
        await page.wait_for_function("document.querySelector('#trace').children.length >= 6")
        await page.wait_for_timeout(500)
        await scroll_to(".grid.lower")
        await cap("Stress test: a SIMULATED faulty model invents numbers and a causal claim. Four agents run on Microsoft Agent Framework…", 6)
        await page.evaluate("window.scrollBy({top: 260, behavior: 'smooth'})")
        await cap("…the Evidence & Quality Reviewer rejects every unsupported claim, sends feedback, and only the corrected draft is approved.", 7)
        await page.evaluate("window.scrollBy({top: 300, behavior: 'smooth'})")
        await page.wait_for_timeout(2500)

        # 7. Ground truth + evaluation (1:38-1:46)
        await scroll_to(".grid.lower")
        await cap("Checked against ground truth: F1 0.84 on 180 held-out synthetic matches; 100% of template claims verified.", 7)

        # 8. End card (1:46-1:54)
        await cap("")
        await card("MATCHPULSE", "Evidence first. Then the story.<br><br>"
                   f"<span style='font-size:20px'>Live demo: {LIVE.replace('https://', '')}<br>"
                   "Code: github.com/Albategnius/matchpulse</span><br><br>"
                   "<span style='font-size:16px;opacity:.7'>Recorded from the same build that is deployed on Azure Container Apps. "
                   "Synthetic data only — not official Premier League data.</span>", 7)

        await ctx.close()
        await browser.close()
    raw = next(tmp.glob("*.webm"))
    shutil.move(str(raw), OUT / "matchpulse_demo.webm")
    shutil.rmtree(tmp, ignore_errors=True)
    print("wrote", OUT / "matchpulse_demo.webm")


if __name__ == "__main__":
    asyncio.run(main())
