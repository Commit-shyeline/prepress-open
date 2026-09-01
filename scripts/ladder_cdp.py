# -*- coding: utf-8 -*-
"""Drive the hero's quality ladder through load and release, over CDP, and print what it did.

The only way to actually test the ladder in model3d.html: it steps on measured frame pace, so no
unit test can exercise it, and it needs a devicePixelRatio ABOVE 1 — the rungs are
[min(dpr,2), min(dpr,1.5), 1, 0.75, 0.55], so at dpr 1 the top three collapse to 1.0 and the first
two steps are invisible. CDP's Emulation.setDeviceMetricsOverride sets dpr 2.5, where every rung is
distinct; a busy-wait inside the page's own rAF then makes frames genuinely slow, and the release
phase watches the climb back. A healthy run ends steppedDown / steppedBackUp / recoveredFully all
true, with no rung changes after recovery — oscillation is the failure the up/down gap prevents.

Needs: the app running on :5099, a Chromium binary (CHROME below points at Playwright's cache;
adjust the build number if yours differs), and the `websockets` package.

Run: python scripts/ladder_cdp.py
"""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

import websockets

CHROME = os.path.join(os.environ["LOCALAPPDATA"], "ms-playwright", "chromium-1223",
                      "chrome-win64", "chrome.exe")
PORT = 9333
APP = "http://127.0.0.1:5099"
DEVICE_SCALE = 2.5              # every rung distinct: 2, 1.5, 1, 0.75, 0.55

# The phases, in seconds. The step UP is the slow one by design — four consecutive roomy blocks of
# 45 frames per rung — so the release phase gets the most time.
SETTLE_S, LOAD_S, FREE_S = 6, 26, 40
BURN_MS = 40                    # held per frame, to make frames genuinely slow


def launch():
    profile = tempfile.mkdtemp(prefix="ladder-chrome-")
    args = [CHROME,
            f"--remote-debugging-port={PORT}",
            f"--user-data-dir={profile}",
            "--no-first-run", "--no-default-browser-check",
            # The whole measurement depends on the page NOT being throttled. These three switch off
            # every reason Chrome has to slow a page it thinks nobody is looking at.
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--window-size=1280,820",
            "about:blank"]
    process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=1) as r:
                json.load(r)
                return process, profile
        except Exception:                       # noqa: BLE001 — still starting
            time.sleep(0.25)
    process.kill()
    raise SystemExit("chromium did not expose a devtools port")


def first_template():
    with urllib.request.urlopen(f"{APP}/api/templates", timeout=10) as response:
        templates = json.load(response).get("templates") or []
    if not templates:
        raise SystemExit("no templates in the store — nothing to render")
    play = [t for t in templates if "Play A" in (t.get("name") or "")]
    return (play or templates)[0]


# The experiment, run inside the page. Returns the whole timeline so the verdict is auditable rather
# than asserted: every sample, its phase, and the frame pace that says whether the run was valid.
PROBE = r"""
(async () => {
  const SETTLE = %(settle)d * 1000, LOAD = %(load)d * 1000, FREE = %(free)d * 1000;
  const BURN = %(burn)d;
  const canvas = document.querySelector('#stage canvas');
  if (!canvas) return { error: 'no canvas' };
  const rung = () => {
    const css = parseFloat(getComputedStyle(canvas).width);
    return css > 0 ? +(canvas.width / css).toFixed(3) : null;
  };
  const t0 = performance.now();
  const at = () => +((performance.now() - t0) / 1000).toFixed(2);
  const timeline = [], pace = [];
  let phase = 'settle', burning = false;

  // Frame pace, so a throttled run is distinguishable from a fast one.
  let frames = 0, since = performance.now();
  const watch = () => {
    frames += 1;
    const now = performance.now();
    if (now - since >= 1500) {
      pace.push({ t: at(), phase, msPerFrame: +((now - since) / frames).toFixed(1),
                  visible: document.visibilityState });
      frames = 0; since = now;
    }
    requestAnimationFrame(watch);
  };
  requestAnimationFrame(watch);

  const burn = () => {
    if (!burning) return;
    const until = performance.now() + BURN;
    while (performance.now() < until) { /* hold the thread, as a weak GPU does */ }
    requestAnimationFrame(burn);
  };

  const sampler = setInterval(() => {
    const value = rung();
    if (value !== null) timeline.push({ t: at(), phase, rung: value });
  }, 500);

  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  await wait(SETTLE);
  phase = 'load'; burning = true; requestAnimationFrame(burn);
  await wait(LOAD);
  phase = 'free'; burning = false;
  await wait(FREE);
  clearInterval(sampler);

  const of = (name) => timeline.filter((e) => e.phase === name).map((e) => e.rung);
  const settle = of('settle'), load = of('load'), free = of('free');
  const baseline = settle.length ? settle[settle.length - 1] : null;
  const lowest = load.length ? Math.min(...load) : null;
  const ended = free.length ? free[free.length - 1] : null;
  return {
    dpr: window.devicePixelRatio,
    verdict: { baseline, lowestUnderLoad: lowest, afterRelease: ended,
               steppedDown: lowest !== null && baseline !== null && lowest < baseline,
               steppedBackUp: ended !== null && lowest !== null && ended > lowest,
               recoveredFully: ended !== null && ended === baseline },
    pace, timeline,
  };
})()
""" % {"settle": SETTLE_S, "load": LOAD_S, "free": FREE_S, "burn": BURN_MS}


async def drive(url):
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list", timeout=5) as response:
        targets = [t for t in json.load(response) if t.get("type") == "page"]
    if not targets:
        raise SystemExit("no page target")
    counter = 0

    async with websockets.connect(targets[0]["webSocketDebuggerUrl"],
                                  max_size=32 * 1024 * 1024) as socket:
        async def call(method, params=None, timeout=180):
            nonlocal counter
            counter += 1
            wanted = counter
            await socket.send(json.dumps({"id": wanted, "method": method,
                                          "params": params or {}}))
            deadline = time.time() + timeout
            while time.time() < deadline:
                raw = await asyncio.wait_for(socket.recv(), timeout=deadline - time.time())
                message = json.loads(raw)
                if message.get("id") == wanted:
                    if "error" in message:
                        raise SystemExit(f"{method} failed: {message['error']}")
                    return message.get("result", {})
            raise SystemExit(f"{method} timed out")

        await call("Page.enable")
        await call("Runtime.enable")
        # BEFORE navigating: QUALITY is computed from devicePixelRatio when the module initialises,
        # so an override applied afterwards would not be in the ladder the page built.
        await call("Emulation.setDeviceMetricsOverride",
                   {"width": 1280, "height": 800, "deviceScaleFactor": DEVICE_SCALE,
                    "mobile": False})
        await call("Page.navigate", {"url": url})

        # Wait for the scene: the canvas appears only after the template fetch and the build.
        for _ in range(120):
            result = await call("Runtime.evaluate",
                                {"expression": "!!document.querySelector('#stage canvas')",
                                 "returnByValue": True})
            if result.get("result", {}).get("value"):
                break
            await asyncio.sleep(0.5)
        else:
            raise SystemExit("the scene never produced a canvas")

        total = SETTLE_S + LOAD_S + FREE_S
        print(f"running {total}s at deviceScaleFactor {DEVICE_SCALE} …", flush=True)
        result = await call("Runtime.evaluate",
                            {"expression": PROBE, "awaitPromise": True,
                             "returnByValue": True, "timeout": (total + 60) * 1000},
                            timeout=total + 90)
        return result["result"]["value"]


def main():
    if not os.path.exists(CHROME):
        raise SystemExit(f"no chromium at {CHROME}")
    template = first_template()
    url = f"{APP}/model/{template['token']}?bare=1"
    print(f"template: {template.get('name')}\nurl: {url}", flush=True)
    process, profile = launch()
    try:
        report = asyncio.run(drive(url))
    finally:
        process.kill()
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ladder_cdp_result.json")
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)

    if report.get("error"):
        raise SystemExit(report["error"])
    print("\ndpr in page:", report["dpr"])
    print("verdict:", json.dumps(report["verdict"], indent=2))
    print("\nframe pace (ms/frame) — a valid run is tens, a throttled one hundreds:")
    for entry in report["pace"]:
        print(f"  {entry['t']:7.2f}s {entry['phase']:7s} {entry['msPerFrame']:7.1f} "
              f"{entry['visible']}")
    print("\nrung changes:")
    previous = None
    for entry in report["timeline"]:
        if entry["rung"] != previous:
            print(f"  {entry['t']:7.2f}s {entry['phase']:7s} -> {entry['rung']}")
            previous = entry["rung"]
    print("\nfull timeline written to", out)


if __name__ == "__main__":
    sys.exit(main())
