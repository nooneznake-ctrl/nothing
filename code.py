import os
import json
import re
import time
from getpass import getpass
import requests
from playwright.sync_api import sync_playwright

# ── Configuration ─────────────────────────────────────────────────────────────
TARGET_TXT_PATH = r"yours path file for json export"
PROGRESS_FILE = "processed_urls.txt"
DEBUG_PORT = 9222
LOGIN_URL = "https://pawchive.st/account/login"
BASE_URL = "https://pawchive.st"

# Set to False so it skips already-processed items instead of re-toggling them -- doesnt matter for you
RECHECK_FIRST_80 = False

# Set to True if you want to inspect the raw HTML of the first post in console
DEBUG_HTML_OUTPUT = False

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


class HybridPawchiveAutomationApp:
    def __init__(self):
        self.fav_count = 0
        self.already_fav_count = 0
        self.skip_count = 0
        self.error_count = 0
        self.done = 0
        self.http_session = requests.Session()

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

    def _extract_session_data(self, page, context):
        """
        Steals cookies, User-Agent, and CSRF token from Playwright 
        and configures the requests.Session.
        """
        self.log("🔑 Extracting browser session & cookies into HTTP engine...")

        # 1. Grab cookies
        cookies = context.cookies()
        for cookie in cookies:
            self.http_session.cookies.set(cookie['name'], cookie['value'], domain=cookie.get('domain', 'pawchive.st'))

        # 2. Grab User-Agent
        user_agent = page.evaluate("navigator.userAgent")

        # 3. Headers setup
        self.http_session.headers.update({
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Origin": BASE_URL,
            "Referer": f"{BASE_URL}/",
        })

        # 4. Check for CSRF token if meta tag exists
        try:
            csrf_token = page.locator("meta[name='csrf-token']").get_attribute("content", timeout=2000)
            if csrf_token:
                self.http_session.headers["X-CSRF-Token"] = csrf_token
                self.log("✔ Extracted CSRF token.")
        except Exception:
            pass

        self.log("⚡ Session hijacked successfully!")

    def process_favorite_hybrid(self, url, post_id, service, user_id):
        """
        Uses raw HTTP requests, detects favorited status, extracts the exact 
        favorite endpoint from HTML, and triggers favoriting safely.
        """
        try:
            # 1. Fetch raw HTML of the post page
            resp = self.http_session.get(url, timeout=8)
            if resp.status_code != 200:
                return "error", f"HTTP {resp.status_code}"

            html_text = resp.text

            # Optional Debug Printer
            if DEBUG_HTML_OUTPUT and self.done == 0:
                print("\n================ DEBUG HTML OUTPUT ================")
                print(html_text[:3000])
                print("===================================================\n")

            # 2. State Detection: Comprehensive raw HTML check for active favorite indicators
            html_lower = html_text.lower()
            
            is_already_fav = (
                # CSS class indicators
                'class="post__favorite-button active"' in html_lower or
                'class="post__favorite-button favorited"' in html_lower or
                'favorite-button active' in html_lower or
                'favorite-button favorited' in html_lower or
                'is-favorite' in html_lower or
                
                # Attribute indicators
                'aria-pressed="true"' in html_lower or
                'title="unfavorite' in html_lower or
                'title="remove' in html_lower or
                'data-favorited="true"' in html_lower or
                
                # Button label/icon indicators
                'unfavorite' in html_lower or
                '★' in html_text or  # Case sensitive check for star symbol
                '<svg class="fa-star"' in html_lower or
                'fa-star-solid' in html_lower
            )

            if is_already_fav:
                return "already_favorited", None

            # 3. Dynamic Endpoint Extraction: Find actual favorite endpoint in HTML
            fav_endpoint_match = re.search(
                r'action=["\']([^"\']*(?:favorite|favourite|fav)[^"\']*)["\']', 
                html_text, 
                re.IGNORECASE
            ) or re.search(
                r'href=["\']([^"\']*(?:favorite|favourite|fav)[^"\']*)["\']', 
                html_text, 
                re.IGNORECASE
            )

            if fav_endpoint_match:
                target_path = fav_endpoint_match.group(1)
                if not target_path.startswith("http"):
                    api_fav_url = f"{BASE_URL}{target_path}" if target_path.startswith("/") else f"{BASE_URL}/{target_path}"
                else:
                    api_fav_url = target_path
            else:
                # Fallback URL patterns if regex doesn't match
                api_fav_url = f"{BASE_URL}/api/v1/favorites/post/{service}/{user_id}/{post_id}"

            # 4. Fire the Favorite POST Request
            headers = {
                "X-Requested-With": "XMLHttpRequest",
                "Referer": url,
                "Accept": "application/json, text/plain, */*"
            }
            
            post_resp = self.http_session.post(api_fav_url, headers=headers, timeout=8)

            # Some endpoints use GET for toggling via links
            if post_resp.status_code in (404, 405):
                post_resp = self.http_session.get(api_fav_url, headers=headers, timeout=8)

            if post_resp.status_code in (200, 201, 204):
                return "favorited", None
            elif post_resp.status_code == 400 or "already" in post_resp.text.lower():
                return "already_favorited", None
            else:
                return "error", f"Fav Request Status {post_resp.status_code} ({api_fav_url})"

        except Exception as e:
            return "error", str(e)

    def run_automation(self, urls, username, password):
        processed_set = load_processed_urls()
        self.log(f"📋 Loaded {len(processed_set)} previously saved entries from {PROGRESS_FILE}.")

        errors = []
        total_urls = len(urls)

        try:
            with sync_playwright() as p:
                self.log("🌐 Connecting to Chrome session for authentication...")
                browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{DEBUG_PORT}")
                context = browser.contexts[0] if browser.contexts else browser.new_context()
                page = context.pages[0] if context.pages else context.new_page()

                if not self._ensure_login(page, username, password):
                    self.log("❌ Authentication failed. Stopping execution.")
                    return

                # Transfer session to HTTP client
                self._extract_session_data(page, context)

                self.log("\n🚀 Hybrid Ultra-Fast Engine Started (Direct HTTP Mode)...\n")

                pattern = re.compile(r"/(?P<service>[^/]+)/user/(?P<user>[^/]+)/post/(?P<post>[^/]+)")

                start_time = time.time()

                for idx, url in enumerate(urls, start=1):
                    should_force_check = RECHECK_FIRST_80 and (idx <= 80)

                    if url in processed_set and not should_force_check:
                        self.skip_count += 1
                        self.done += 1
                        continue

                    match = pattern.search(url)
                    if match:
                        service = match.group("service")
                        user_id = match.group("user")
                        post_id = match.group("post")
                    else:
                        parts = url.strip("/").split("/")
                        post_id = parts[-1]
                        user_id = parts[-3] if len(parts) >= 3 else "unknown"
                        service = parts[-5] if len(parts) >= 5 else "patreon"

                    status, err_detail = self.process_favorite_hybrid(url, post_id, service, user_id)

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
                        errors.append(f"{url} -> {err_detail}")
                        self.error_count += 1
                        self.log(f"[{idx}/{total_urls}] ❌ Error ({post_id}): {err_detail}")

                    self.done += 1

                elapsed = time.time() - start_time
                self.log(f"\n⚡ Processed {self.done} URLs in {elapsed:.2f} seconds!")

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
        print(f"🚀 Starting Hybrid automation with {len(urls_to_process)} target links...")
        app = HybridPawchiveAutomationApp()
        app.run_automation(urls_to_process, username, password)

        print(
            f"\nFinished! Favorited: {app.fav_count}, Already Favorited: {app.already_fav_count}, Skipped: {app.skip_count}, Errors: {app.error_count}"
        )