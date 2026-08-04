import json
import os
import re
import sys
import time
import random
import threading
from getpass import getpass
from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

# ── Configuration ─────────────────────────────────────────────────────────────
TARGET_TXT_PATH = (
    r"your ready urls from export"
)
PROGRESS_FILE = "processed_urls.txt"
LOGIN_URL = "https://pawchive.st/account/login"
BASE_URL = "https://pawchive.st"

# Enable this if you explicitly want to re-check the first N items in urls.txt
RECHECK_FIRST_80 = False

WORKING_SERVICES = {"patreon", "pixiv", "fanbox", "subscribestar", "fantia"}

HARDCODED_USERNAME = "yours"
HARDCODED_PASSWORD = "yours"


def load_urls_from_file(filepath):
    if not os.path.exists(filepath):
        print(f"❌ Target file not found at: {filepath}")
        return []

    print(f"📂 Reading input file: {filepath}")
    urls = []

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read().strip()

        try:
            data = json.loads(content)
            if isinstance(data, dict):
                items = data.get("posts") or data.get("artists") or []
            elif isinstance(data, list):
                items = data
            else:
                items = []

            for item in items:
                if isinstance(item, dict):
                    service = (item.get("service") or "").strip().lower()
                    user_id = item.get("user")
                    post_id = item.get("id")

                    if service in WORKING_SERVICES:
                        if user_id and post_id:
                            urls.append(
                                f"{BASE_URL}/{service}/user/{user_id}/post/{post_id}"
                            )
                        elif post_id:
                            urls.append(f"{BASE_URL}/{service}/user/{post_id}")

            if urls:
                print(f"✔ Extracted {len(urls)} URLs from JSON structure.")
                return urls
        except json.JSONDecodeError:
            pass

        for line in content.splitlines():
            line = line.strip()
            if line.startswith("http://") or line.startswith("https://"):
                urls.append(line)

        print(f"✔ Extracted {len(urls)} URLs from text file.")
        return urls

    except Exception as e:
        print(f"❌ Error reading target file: {e}")
        return []


def load_processed_urls():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            return set(line.strip() for line in f if line.strip())
    return set()


def append_processed_url(url):
    with open(PROGRESS_FILE, "a", encoding="utf-8") as f:
        f.write(f"{url}\n")


class FastPawchiveAutomationApp:

    def __init__(self):
        self.fav_count = 0
        self.already_fav_count = 0
        self.skip_count = 0
        self.error_count = 0
        self.done = 0
        self.is_paused = False
        self.is_running = True

    def log(self, message):
        print(message)

    def listen_for_pause(self):
        """Thread listener: Press 'p' + Enter to toggle pause/resume."""
        while self.is_running:
            try:
                user_input = input().strip().lower()
                if user_input == "p":
                    self.is_paused = not self.is_paused
                    if self.is_paused:
                        self.log("\n⏸  [PAUSED] Press 'p' and Enter to resume execution...")
                    else:
                        self.log("▶  [RESUMED] Continuing automation...\n")
            except (EOFError, KeyboardInterrupt):
                break

    def _ensure_login(self, page, username, password):
        try:
            page.goto(
                f"{BASE_URL}/artists",
                wait_until="domcontentloaded",
                timeout=15000,
            )
            if page.locator("#login_form").count() == 0:
                self.log("✔ Already logged into Pawchive.")
                return True

            self.log("🔑 Not logged in. Attempting login...")
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=15000)
            page.wait_for_selector("#login_form", timeout=8000)
            page.fill("#old-username", username)
            page.fill("#old-password", password)

            # Direct JS dispatch to bypass ad overlays
            page.locator("#login_form button[type='submit']").dispatch_event(
                "click"
            )
            page.wait_for_load_state("domcontentloaded", timeout=15000)

            if page.locator("#login_form").count() == 0:
                self.log("✔ Login successful!")
                return True
            else:
                self.log("❌ Login submission failed.")
                return False

        except Exception as e:
            self.log(f"❌ Login error: {e}")
            return False

    def process_favorite(self, page):
        selectors = [
            "button.post__favorite-button",
            "button[title*='Favorite']",
            "button[title*='favorite']",
            "a[title*='Favorite']",
            "button:has(svg.fa-star)",
            "button:has-text('Favorite')",
            "button:has-text('Favourite')",
            ".post__actions button",
            "button[class*='favorite']",
            "button[class*='favourite']",
            "a[class*='favorite']",
            "a[class*='favourite']",
        ]

        btn = None
        for sel in selectors:
            try:
                candidate = page.query_selector(sel)
                if candidate and candidate.is_visible():
                    btn = candidate
                    break
            except Exception:
                continue

        if not btn:
            return "not_found"

        class_attr = (btn.get_attribute("class") or "").lower()
        title_attr = (btn.get_attribute("title") or "").lower()
        text_content = (btn.inner_text() or "").strip().lower()

        is_already_fav = (
            "active" in class_attr
            or "favorited" in class_attr
            or "is-favorite" in class_attr
            or "unfavorite" in title_attr
            or "remove" in title_attr
            or "unfavorite" in text_content
            or "★" in text_content
        )

        if is_already_fav:
            return "already_favorited"

        # Direct event dispatch bypasses ad overlays completely
        btn.dispatch_event("click")
        
        # Wait briefly for Pawchive API to register the request
        try:
            page.wait_for_load_state("networkidle", timeout=3000)
        except Exception:
            pass

        return "favorited"

    def run_automation(self, urls, username, password):
        processed_set = load_processed_urls()
        total_urls = len(urls)

        # Calculate exact start position based on processed URLs
        already_done_count = sum(1 for u in urls if u in processed_set)
        self.skip_count = already_done_count
        
        self.log(f"📋 Total input URLs: {total_urls}")
        self.log(f"✅ Previously completed: {already_done_count}")
        self.log(f"🚀 Starting automation from position #{already_done_count + 1}\n")
        self.log("💡 Tip: Type 'p' and press Enter at any time to pause/resume.\n")

        # Start keyboard listener thread for pause feature
        pause_thread = threading.Thread(target=self.listen_for_pause, daemon=True)
        pause_thread.start()

        profile_dir = os.path.expandvars(
            r"%USERPROFILE%\FirefoxPawchiveProfile"
        )

        try:
            with sync_playwright() as p:
                self.log("🦊 Launching Firefox with uBlock Origin...")

                context = p.firefox.launch_persistent_context(
                    user_data_dir=profile_dir,
                    headless=False,
                    viewport={"width": 1280, "height": 720},
                )

                context.on("page", lambda popup: popup.close())

                page = (
                    context.pages[0] if context.pages else context.new_page()
                )

                if not self._ensure_login(page, username, password):
                    self.log("❌ Authentication failed. Stopping execution.")
                    context.close()
                    return

                self.log("⚡ Automation Engine Active...\n")

                pattern = re.compile(
                    r"/(?P<service>[^/]+)/user/(?P<user>[^/]+)/post/(?P<post>[^/]+)"
                )

                for idx, url in enumerate(urls, start=1):
                    # Handle manual pause state
                    while self.is_paused:
                        time.sleep(0.5)

                    should_force_check = RECHECK_FIRST_80 and (idx <= 80)

                    if url in processed_set and not should_force_check:
                        self.done += 1
                        continue

                    match = pattern.search(url)
                    post_id = (
                        match.group("post") if match else url.split("/")[-1]
                    )

                    try:
                        page.goto(
                            url, wait_until="domcontentloaded", timeout=12000
                        )

                        if page.locator("#login_form").count() > 0:
                            self.log("⚠ Session expired. Re-authenticating...")
                            self._ensure_login(page, username, password)
                            page.goto(
                                url,
                                wait_until="domcontentloaded",
                                timeout=12000,
                            )

                        status = self.process_favorite(page)
                        
                        if status == "favorited":
                            self.fav_count += 1
                            tag = (
                                "★ Re-Favorited"
                                if should_force_check
                                else "★ Favorited"
                            )
                            self.log(f"[{idx}/{total_urls}] {tag}: {post_id}")
                            append_processed_url(url)
                            processed_set.add(url)
                            
                            # Random pause to avoid rate limiting
                            time.sleep(random.uniform(1.5, 3.0))

                        elif status == "already_favorited":
                            self.already_fav_count += 1
                            self.log(
                                f"[{idx}/{total_urls}] ⏩ Already favorited: {post_id}"
                            )
                            append_processed_url(url)
                            processed_set.add(url)
                        else:
                            self.error_count += 1
                            self.log(
                                f"[{idx}/{total_urls}] ❌ Button not found: {post_id}"
                            )

                    except Exception as e:
                        err_msg = (
                            str(e).splitlines()[0] if str(e) else "Unknown error"
                        )
                        self.error_count += 1
                        self.log(f"[{idx}/{total_urls}] ❌ Error ({post_id}): {err_msg}")

                    self.done += 1

                context.close()

        except KeyboardInterrupt:
            self.log("\n🛑 Execution stopped by user (Ctrl+C). Saving progress...")
        except Exception as e:
            self.log(f"❌ Firefox Browser Error: {e}")
        finally:
            self.is_running = False


if __name__ == "__main__":
    username = HARDCODED_USERNAME
    password = HARDCODED_PASSWORD

    if not username:
        username = input("Enter Pawchive Username: ").strip()
    if not password:
        password = getpass("Enter Pawchive Password: ")

    urls_to_process = load_urls_from_file(TARGET_TXT_PATH)

    if not urls_to_process:
        print("❌ No valid URLs found to process.")
    else:
        app = FastPawchiveAutomationApp()
        app.run_automation(urls_to_process, username, password)

        print(
            f"\nFinished! Favorited: {app.fav_count}, Already Favorited:"
            f" {app.already_fav_count}, Skipped/Processed Previously: {app.skip_count}, Errors:"
            f" {app.error_count}"
        )