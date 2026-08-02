import os
import json
import re
from getpass import getpass
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ── Configuration ─────────────────────────────────────────────────────────────
TARGET_TXT_PATH = r"yours path file for json in txt"
PROGRESS_FILE = "processed_urls.txt"
DEBUG_PORT = 9222
LOGIN_URL = "https://pawchive.st/account/login"
BASE_URL = "https://pawchive.st"

# Enable this to force items 1..80 to be re-checked / re-favorited if needed
RECHECK_FIRST_80 = True

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
                            urls.append(f"{BASE_URL}/{service}/user/{user_id}/post/{post_id}")
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

    def log(self, message):
        print(message)

    def _ensure_login(self, page, username, password):
        try:
            page.goto(f"{BASE_URL}/artists", wait_until="domcontentloaded", timeout=15000)
            if page.locator("#login_form").count() == 0:
                self.log("✔ Already logged into Pawchive.")
                return True

            self.log("🔑 Not logged in. Attempting login...")
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=15000)
            page.wait_for_selector("#login_form", timeout=8000)
            page.fill("#old-username", username)
            page.fill("#old-password", password)
            page.click("#login_form button[type='submit']")
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
            "a[class*='favourite']"
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
            "active" in class_attr or
            "favorited" in class_attr or
            "is-favorite" in class_attr or
            "unfavorite" in title_attr or
            "remove" in title_attr or
            "unfavorite" in text_content or
            "★" in text_content
        )

        if is_already_fav:
            return "already_favorited"

        btn.click()
        return "favorited"

    def run_automation(self, urls, username, password):
        processed_set = load_processed_urls()
        self.log(f"📋 Loaded {len(processed_set)} previously saved entries from {PROGRESS_FILE}.")

        errors = []
        total_urls = len(urls)

        try:
            with sync_playwright() as p:
                self.log("🌐 Connecting to Chrome session...")
                browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{DEBUG_PORT}")
                context = browser.contexts[0] if browser.contexts else browser.new_context()
                page = context.pages[0] if context.pages else context.new_page()

                # 🚀 HIGH-SPEED OPTIMIZATION: Block heavy assets (images, fonts, stylesheets)
                def block_heavy_resources(route):
                    if route.request.resource_type in ["image", "stylesheet", "font", "media"]:
                        route.abort()
                    else:
                        route.continue_()

                context.route("**/*", block_heavy_resources)

                if not self._ensure_login(page, username, password):
                    self.log("❌ Authentication failed. Stopping execution.")
                    return

                self.log("⚡ Turbo Engine Started (Resource Blocking Enabled)...\n")

                pattern = re.compile(r"/(?P<service>[^/]+)/user/(?P<user>[^/]+)/post/(?P<post>[^/]+)")

                for idx, url in enumerate(urls, start=1):
                    should_force_check = RECHECK_FIRST_80 and (idx <= 80)

                    if url in processed_set and not should_force_check:
                        self.skip_count += 1
                        self.done += 1
                        continue

                    match = pattern.search(url)
                    post_id = match.group("post") if match else url.split("/")[-1]

                    try:
                        # Fast domcontentloaded wait with strict 10s timeout
                        page.goto(url, wait_until="domcontentloaded", timeout=10000)

                        status = self.process_favorite(page)

                        if status == "favorited":
                            self.fav_count += 1
                            tag = "★ Re-Favorited" if should_force_check else "★ Favorited"
                            self.log(f"[{idx}/{total_urls}] {tag}: {post_id}")
                            append_processed_url(url)
                        elif status == "already_favorited":
                            self.already_fav_count += 1
                            self.log(f"[{idx}/{total_urls}] ⏩ Already favorited: {post_id}")
                            append_processed_url(url)
                        else:
                            errors.append(f"{url} -> Favorite button not found")
                            self.error_count += 1
                            self.log(f"[{idx}/{total_urls}] ❌ Button not found: {post_id}")

                    except PWTimeout:
                        errors.append(f"{url} -> Page load timeout")
                        self.error_count += 1
                        self.log(f"[{idx}/{total_urls}] ❌ Timeout loading page: {post_id}")
                    except Exception as e:
                        err_msg = str(e).splitlines()[0] if str(e) else "Unknown error"
                        errors.append(f"{url} -> {err_msg}")
                        self.error_count += 1
                        self.log(f"[{idx}/{total_urls}] ❌ Error: {post_id}")

                    self.done += 1

        except Exception as e:
            self.log(f"❌ Connection/Browser Error: {e}")
        finally:
            if errors:
                self.log("\n──── ERRORS ────")
                for err in errors[:20]:
                    self.log(f"❌ {err}")


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
        print(f"🚀 Starting fast automation with {len(urls_to_process)} target links...")
        app = FastPawchiveAutomationApp()
        app.run_automation(urls_to_process, username, password)

        print(
            f"\nFinished! Favorited: {app.fav_count}, Already Favorited: {app.already_fav_count}, Skipped: {app.skip_count}, Errors: {app.error_count}"
        )