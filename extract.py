import json
import sys
import os
import re

BASE_URL = "https://pawchive.st"
WORKING = {"patreon", "pixiv", "fanbox", "subscribestar", "fantia", "boosty"}  # set to None for all

def extract(path, only_working=True):
    if not os.path.exists(path):
        print(f"❌ File not found: {path}")
        return []

    with open(path, encoding="utf-8") as f:
        content = f.read().strip()

    if not content:
        print("❌ File is empty.")
        return []

    urls = []

    # 1) Try proper JSON
    try:
        data = json.loads(content)
        if isinstance(data, dict):
            items = data.get("posts") or data.get("artists") or data.get("results") or []
        elif isinstance(data, list):
            items = data
        else:
            items = []

        for item in items:
            if not isinstance(item, dict):
                continue
            service = (item.get("service") or "").strip().lower()
            if not service:
                continue
            if only_working and WORKING and service not in WORKING:
                continue

            user = item.get("user") or item.get("user_id")
            post = item.get("id") or item.get("post_id")

            if user and post:
                urls.append(f"{BASE_URL}/{service}/user/{user}/post/{post}")
            elif item.get("id") is not None:          # artist-style
                urls.append(f"{BASE_URL}/{service}/user/{item['id']}")

        if urls:
            print(f"✔ Extracted {len(urls)} URLs from JSON structure.")
            return urls
    except json.JSONDecodeError as e:
        print(f"⚠ Not valid JSON ({e}). Trying other methods…")
        # show first 150 characters so you can see what the file actually contains
        preview = content[:150].replace("\n", "\\n")
        print(f"   File starts with: {preview!r}")

    # 2) Regex: any http(s) URL
    found = re.findall(r'https?://[^\s"\'<>]+', content)
    if found:
        # keep only pawchive / kemono style if you want
        urls = [u for u in found if "pawchive.st" in u or "kemono" in u]
        if not urls:
            urls = found
        print(f"✔ Extracted {len(urls)} URLs with regex.")
        return urls

    # 3) Line-by-line
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("http://") or line.startswith("https://"):
            urls.append(line)

    if urls:
        print(f"✔ Extracted {len(urls)} URLs from plain text lines.")
    else:
        print("❌ No URLs found in the file.")
    return urls

if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "json.txt"
    urls = extract(src)

    if not urls:
        sys.exit(1)

    print()
    for u in urls:
        print(u)

    with open("urls.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(urls))
    print(f"\n✔ Saved {len(urls)} URLs → urls.txt")