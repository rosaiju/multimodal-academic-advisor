"""End-to-end browser walkthrough of the advisor UI.

Drives a real Chromium through the whole student journey against a running
backend + Vite dev server, asserting what a demo audience would see and taking a
screenshot at every step:

    register a throwaway account -> upload data/samples/demo_transcript.txt ->
    review (untick / retick a row) -> confirm -> Progress (credits and block
    counts match the engine) -> Ask the advisor -> What-if & unlocks ->
    Semester plan (an out-of-order move is flagged) -> Compare courses ->
    Deadlines (+ .ics download) -> sign out -> 375px mobile screenshots.

It needs Playwright for Python (`pip install playwright`) and a Chromium build.
It does NOT start servers. Example, with a backend on 8004 and Vite on 5174:

    VITE_API_TARGET=http://127.0.0.1:8004 npx vite --port 5174   # in frontend/
    py backend/scripts/browser_walkthrough.py --base-url http://localhost:5174 --out shots/

The account it creates is fictional (walkthrough-<random>@example.test). Point
the backend's USER_DIR / STUDENT_RECORD_DIR at a scratch folder to keep it out
of the real data directories, or delete the two files it prints at the end.

Exit code 0 means every step passed.
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sys
import time
import uuid
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TRANSCRIPT = REPO_ROOT / "data" / "samples" / "demo_transcript.txt"
TIMEOUT_MS = 20_000


def find_chrome() -> str | None:
    """A full Chromium from the Playwright cache, for machines without headless_shell."""
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".cache")) / "ms-playwright"
    hits = sorted(glob.glob(str(base / "chromium-*" / "chrome-win" / "chrome.exe")))
    return hits[0] if hits else None


class Walkthrough:
    def __init__(self, page: Page, base_url: str, out: Path, transcript: Path):
        self.page = page
        self.base = base_url.rstrip("/")
        self.out = out
        self.transcript = transcript
        self.results: list[tuple[str, bool, str]] = []
        self.email = f"walkthrough-{uuid.uuid4().hex[:10]}@example.test"
        self.password = "fictional walkthrough passphrase"
        self.shot_no = 0

    # ---- helpers ----

    def shot(self, name: str, full_page: bool = True) -> str:
        self.shot_no += 1
        path = self.out / f"{self.shot_no:02d}-{name}.png"
        self.page.screenshot(path=str(path), full_page=full_page)
        return str(path)

    def step(self, name, fn):
        started = time.monotonic()
        try:
            fn()
            shot = self.shot(name)
            self.results.append((name, True, shot))
            print(f"PASS  {name}  ({time.monotonic() - started:.1f}s)  {shot}")
        except Exception as exc:  # noqa: BLE001 - report every failure, keep going where possible
            try:
                shot = self.shot(f"{name}-FAILED")
            except Exception:  # noqa: BLE001
                shot = "(no screenshot)"
            self.results.append((name, False, shot))
            print(f"FAIL  {name}: {exc}\n      {shot}")
            raise

    def nav(self, label: str):
        self.page.get_by_role("navigation", name="Main").get_by_role(
            "button", name=label, exact=True
        ).click()

    def api_token(self) -> str:
        r = self.page.request.post(
            f"{self.base}/api/auth/login",
            data={"email": self.email, "password": self.password},
        )
        assert r.ok, f"login via API failed: {r.status}"
        return r.json()["access_token"]

    # ---- steps ----

    def register(self):
        p = self.page
        p.goto(self.base)
        p.get_by_role("button", name="Create an account").click()
        p.get_by_label("Email").fill(self.email)
        p.get_by_label("Name").fill("Walkthrough Student (fictional)")
        p.get_by_label("Password").fill(self.password)
        p.get_by_role("button", name="Create account").click()
        expect(p.get_by_text("Upload your transcript")).to_be_visible(timeout=TIMEOUT_MS)
        expect(p.get_by_role("button", name="Sign out")).to_be_visible()

    def upload(self):
        p = self.page
        p.locator("input[type=file]").set_input_files(str(self.transcript))
        expect(p.get_by_text(self.transcript.name)).to_be_visible()
        p.get_by_role("button", name="Read transcript").click()
        expect(p.get_by_text(re.compile(r"We read \d+ courses?"))).to_be_visible(timeout=TIMEOUT_MS)

    def review(self):
        p = self.page
        boxes = p.locator("tbody input[type=checkbox]")
        n = boxes.count()
        assert n == 7, f"expected 7 rows from the demo transcript, got {n}"
        selected = p.get_by_text(re.compile(r"of 7\s+selected"))
        expect(selected).to_contain_text("7 of 7")
        boxes.nth(0).uncheck()
        expect(selected).to_contain_text("6 of 7")
        expect(p.get_by_role("button", name="Confirm 6 courses")).to_be_visible()
        boxes.nth(0).check()
        expect(selected).to_contain_text("7 of 7")

    def confirm(self):
        p = self.page
        p.get_by_role("button", name="Confirm 7 courses").click()
        expect(p.get_by_text("Credit progress")).to_be_visible(timeout=TIMEOUT_MS)

    def dashboard(self):
        p = self.page
        expect(p.get_by_text("23.00 of 120 required credits earned")).to_be_visible()
        expect(p.get_by_text("Computed by the engine").first).to_be_visible()
        # The block counts on screen must be the engine's, not something the UI derived.
        token = self.api_token()
        me = p.request.get(f"{self.base}/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        sid = me.json()["student_id"]
        summary = p.request.get(
            f"{self.base}/api/students/{sid}/audit/summary",
            headers={"Authorization": f"Bearer {token}"},
        ).json()
        assert summary["credits_earned"] == "23.00", summary["credits_earned"]
        total = (
            summary["satisfied"]
            + summary["in_progress"]
            + summary["unmet"]
            + summary["needs_advisor"]
        )
        expect(p.get_by_text(f"{summary['satisfied']} of {total}", exact=True)).to_be_visible()
        print(f"      blocks: {summary['satisfied']} of {total} encoded requirements satisfied")
        # Nothing on the page may promise a graduation date.
        body = p.locator("main").inner_text().lower()
        for phrase in ("you will graduate", "graduation date:", "expected graduation"):
            assert phrase not in body, f"dashboard implies a graduation date: {phrase!r}"

    def advisor(self):
        p = self.page
        self.nav("Ask the advisor")
        p.locator("#advisor-question").fill("What should I take next semester?")
        p.get_by_role("button", name="Ask", exact=True).click()
        # Who wrote the answer depends on the mode: Gemini (checked by the engine),
        # a fallback, or the engine alone. Any of them is a real answer; which one is
        # printed so a run shows whether the model was actually used.
        label = p.get_by_text(
            re.compile(
                r"^(Written by .+ · checked by the engine|Fallback · .+|Computed by the engine)$"
            )
        ).first
        expect(label).to_be_visible(timeout=120_000)
        print(f"      answer label: {label.inner_text()!r}")
        turn = label.locator("xpath=ancestor::div[contains(@class, 'space-y-2')][1]")
        answer = turn.inner_text().strip()
        assert len(answer) > 40, f"advisor answer looks empty: {answer!r}"

    def explore(self):
        p = self.page
        self.nav("What-if & unlocks")
        p.locator("#whatif-courses").fill("COSC 220")
        p.get_by_role("button", name="Run what-if").click()
        expect(p.get_by_text("This is a simulation")).to_be_visible(timeout=TIMEOUT_MS)
        expect(p.get_by_text("Applied to the simulation")).to_be_visible()
        p.locator("#unlock-course").fill("COSC 220")
        p.get_by_role("button", name="Explore").click()
        expect(p.get_by_text("What it directly unlocks")).to_be_visible(timeout=TIMEOUT_MS)

    def planner(self):
        p = self.page
        self.nav("Semester plan")
        expect(p.get_by_text("Read this before relying on the plan")).to_be_visible(
            timeout=TIMEOUT_MS
        )
        expect(p.get_by_text("not a graduation date").first).to_be_visible()
        terms = p.locator("main section[aria-label]")
        expect(terms.first).to_be_visible()
        names = [terms.nth(i).get_attribute("aria-label") for i in range(terms.count())]
        assert len(names) >= 2, f"need two planned terms to test a move, got {names}"
        # Move a course from a later term into the FIRST. Not every course has a
        # prerequisite, so try later courses (last term first) until the backend
        # validator flags one, re-generating between attempts.
        alert = p.get_by_role("alert").filter(has_text="problem")
        candidates = []
        for i in range(len(names) - 1, 0, -1):
            cards = terms.nth(i).locator(":scope > ul > li")
            for j in range(cards.count()):
                candidates.append(cards.nth(j).locator("span.font-medium").first.inner_text())
        flagged = None
        for code in candidates[:8]:
            card = p.locator("main section[aria-label] li").filter(
                has=p.locator("span.font-medium", has_text=re.compile(rf"^{code}$"))
            )
            card.locator("select").select_option(label=f"Move to {names[0]}")
            try:
                expect(alert).to_be_visible(timeout=4000)
                flagged = code
                break
            except AssertionError:
                p.get_by_role("button", name="Re-generate").click()
                expect(p.get_by_role("button", name="Re-generate")).to_be_enabled()
                p.wait_for_timeout(500)
        assert flagged, f"no out-of-order move was flagged (tried {candidates[:8]})"
        bad = terms.first.locator("li.border-red-300")
        expect(bad.first).to_be_visible()
        print(f"      moved {flagged} into {names[0]}: {bad.first.inner_text()[:160]!r}")
        self.shot("planner-invalid-move")
        # Re-generating replaces the hand edit and clears the error.
        p.get_by_role("button", name="Re-generate").click()
        expect(alert).to_have_count(0, timeout=TIMEOUT_MS)

    def compare(self):
        p = self.page
        self.nav("Compare courses")
        picker = p.get_by_label("Add a course")
        expect(picker).to_be_visible(timeout=TIMEOUT_MS)
        picker.select_option(value="COSC220")
        picker.select_option(value="COSC281")
        expect(p.locator("section[aria-label=COSC220]")).to_be_visible(timeout=TIMEOUT_MS)
        expect(p.locator("section[aria-label=COSC281]")).to_be_visible()
        expect(p.get_by_text("You can take it now").first).to_be_visible()

    def deadlines(self):
        p = self.page
        self.nav("Deadlines")
        expect(p.get_by_role("heading", name="Deadlines")).to_be_visible(timeout=TIMEOUT_MS)
        expect(p.get_by_text("Source on morgan.edu").first).to_be_visible()
        with p.expect_download(timeout=TIMEOUT_MS) as dl:
            p.get_by_role("button", name="Add to my calendar").click()
        download = dl.value
        target = self.out / "deadlines.ics"
        download.save_as(str(target))
        text = target.read_text(encoding="utf-8")
        assert text.startswith("BEGIN:VCALENDAR"), text[:80]
        assert "BEGIN:VEVENT" in text, "calendar has no events"
        print(f"      .ics: {text.count('BEGIN:VEVENT')} events, saved to {target}")

    def mobile(self):
        """Every tab at 375px: nothing may scroll the page sideways."""
        p = self.page
        p.set_viewport_size({"width": 375, "height": 812})
        for label in (
            "Progress",
            "Ask the advisor",
            "Semester plan",
            "What-if & unlocks",
            "Compare courses",
            "Deadlines",
        ):
            self.nav(label)
            p.wait_for_timeout(1200)
            width = p.evaluate("document.documentElement.scrollWidth")
            slug = re.sub(r"[^a-z]+", "-", label.lower()).strip("-")
            self.shot(f"mobile-{slug}")
            assert width <= 375, f"{label}: page is {width}px wide at a 375px viewport"
        p.set_viewport_size({"width": 1280, "height": 900})

    def sign_out(self):
        p = self.page
        p.get_by_role("button", name="Sign out").click()
        expect(p.get_by_role("button", name="Sign in")).to_be_visible()
        expect(p.get_by_role("navigation", name="Main")).to_have_count(0)

    def run(self) -> bool:
        steps = [
            ("register", self.register),
            ("upload", self.upload),
            ("review", self.review),
            ("confirm", self.confirm),
            ("dashboard", self.dashboard),
            ("advisor", self.advisor),
            ("explore", self.explore),
            ("planner", self.planner),
            ("compare", self.compare),
            ("deadlines", self.deadlines),
            ("mobile-375", self.mobile),
            ("sign-out", self.sign_out),
        ]
        # Steps after confirm depend on a record, so a failure there stops the run;
        # the tabs themselves are independent, so one failing tab does not hide the rest.
        independent = {"advisor", "explore", "planner", "compare", "deadlines", "mobile-375"}
        for name, fn in steps:
            try:
                self.step(name, fn)
            except Exception:  # noqa: BLE001
                if name not in independent:
                    break
                self.page.set_viewport_size({"width": 1280, "height": 900})
        print(f"\naccount used: {self.email}")
        return all(ok for _, ok, _ in self.results) and len(self.results) == len(steps)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--base-url", default="http://localhost:5173")
    ap.add_argument("--out", default="walkthrough-shots", help="screenshot folder")
    ap.add_argument("--transcript", default=str(DEFAULT_TRANSCRIPT))
    ap.add_argument("--chrome", default=None, help="Chromium executable (auto-detected)")
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    chrome = args.chrome or find_chrome()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed, executable_path=chrome)
        context = browser.new_context(
            viewport={"width": 1280, "height": 900}, accept_downloads=True
        )
        page = context.new_page()
        page.set_default_timeout(TIMEOUT_MS)
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        ok = Walkthrough(page, args.base_url, out, Path(args.transcript)).run()
        browser.close()

    if errors:
        print("\nuncaught page errors:")
        for e in errors:
            print("  ", e)
        ok = False
    print("\nRESULT:", "ALL STEPS PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
