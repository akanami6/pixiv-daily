#!/usr/bin/env python3
"""
Pixiv Daily — GitHub Actions cloud version.
Playwright for JS login, requests for cookie-based image download.
"""
import os
import sys
import re
import json
import random
import zipfile
import logging
import smtplib
import time
import email.policy
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders
from datetime import datetime

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

IMAGE_COUNT = 10
IMAGES_DIR = "/tmp/pixiv_images"
SEARCH_TAGS = os.environ.get("SEARCH_TAGS", "巨乳 美少女")
SEARCH_ORDER = os.environ.get("SEARCH_ORDER", "popular_d")  # popular_d or date_d
SEARCH_MODE = os.environ.get("SEARCH_MODE", "all")  # all, safe, r18


def login_and_get_image_list():
    """Use Playwright to login to Pixiv and search images by tags."""
    from playwright.sync_api import sync_playwright
    from urllib.parse import quote

    username = os.environ["PIXIV_USERNAME"]
    password = os.environ["PIXIV_PASSWORD"]
    tags = SEARCH_TAGS
    encoded = quote(tags)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            locale="zh-CN",
        )
        page = context.new_page()

        # Step 1: Login
        logging.info("Opening Pixiv login page...")
        page.goto("https://accounts.pixiv.net/login?lang=zh&source=pc&view_type=page", wait_until="domcontentloaded")
        page.wait_for_timeout(2000)

        ok_btn = page.query_selector('button:has-text("OK")')
        if ok_btn and ok_btn.is_visible():
            ok_btn.click()
            page.wait_for_timeout(500)

        logging.info("Filling login form...")
        page.fill('input[placeholder*="邮箱"], input[placeholder*="pixiv ID"], input[placeholder*="メールアドレス"]', username)
        page.fill('input[placeholder*="密码"], input[placeholder*="パスワード"]', password)
        page.wait_for_timeout(500)

        login_btn = page.query_selector('button:has-text("登录"), button:has-text("ログイン")')
        if login_btn and login_btn.is_enabled():
            login_btn.click()
        else:
            page.keyboard.press("Enter")
        page.wait_for_timeout(3000)
        logging.info(f"After login URL: {page.url}")

        # Step 2: Fetch multiple pages of search results, then shuffle
        all_illusts = []
        for p in range(1, 4):  # fetch 3 pages × 60 = ~180 candidates
            search_url = (
                f"https://www.pixiv.net/ajax/search/illustrations/{encoded}"
                f"?word={encoded}&order={SEARCH_ORDER}&mode={SEARCH_MODE}"
                f"&p={p}&s_mode=s_tag_full&type=illust&lang=zh"
            )
            logging.info(f"Searching page {p}: {tags}")
            page.goto(search_url, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)

            body = page.evaluate("() => document.body.innerText")
            data = json.loads(body)
            if data.get("error"):
                logging.warning(f"Search page {p} error: {data}")
                break
            page_data = data.get("body", {}).get("illust", {}).get("data", [])
            if not page_data:
                break
            all_illusts.extend(page_data)
            logging.info(f"  Page {p}: {len(page_data)} results (total: {len(all_illusts)})")

        logging.info(f"Total search results: {len(all_illusts)}")
        # Random shuffle for variety
        random.shuffle(all_illusts)

        # Step 3: Build image URL list from shuffled results
        image_urls = []
        seen_ids = set()
        for item in all_illusts:
            if len(image_urls) >= IMAGE_COUNT:
                break
            if item.get("illustType") != 0:  # 0 = illustration, skip manga/ugoira
                continue
            iid = item["id"]
            if iid in seen_ids:
                continue
            seen_ids.add(iid)

            thumb = item["url"]
            # thumbnail: /c/250x250_80_a2/img-master/.../xxx_p0_square1200.jpg
            # full:      /img-master/.../xxx_p0_master1200.jpg
            full = re.sub(r"/c/\d+x\d+.*?/img-master/", "/img-master/", thumb)
            full = re.sub(r"_square1200", "_master1200", full)

            page_count = item.get("pageCount", 1)
            if page_count > 1:
                for p in range(min(page_count, 3)):  # max 3 pages per work
                    if len(image_urls) >= IMAGE_COUNT:
                        break
                    page_url = re.sub(r"_p0_", f"_p{p}_", full)
                    image_urls.append({
                        "url": page_url,
                        "illust_id": iid,
                        "title": item.get("title", ""),
                        "author": item.get("userName", "unknown"),
                    })
            else:
                image_urls.append({
                    "url": full,
                    "illust_id": iid,
                    "title": item.get("title", ""),
                    "author": item.get("userName", "unknown"),
                })

        # Step 4: Export cookies for requests-based download
        cookies = context.cookies()
        browser.close()

        logging.info(f"Collected {len(image_urls)} image URLs and {len(cookies)} cookies")
        return image_urls, cookies, tags


def download_images(image_urls, cookies):
    """Download images using requests with Playwright cookies."""
    os.makedirs(IMAGES_DIR, exist_ok=True)
    for f in os.listdir(IMAGES_DIR):
        os.remove(os.path.join(IMAGES_DIR, f))

    session = requests.Session()
    # Add cookies to session
    for c in cookies:
        session.cookies.set(c["name"], c["value"], domain=c.get("domain", ""))

    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Referer": "https://www.pixiv.net/",
    })

    downloaded = []
    for img in image_urls:
        fname = f"{img['illust_id']}.{img['url'].split('.')[-1].split('?')[0]}"
        fpath = os.path.join(IMAGES_DIR, fname)
        try:
            r = session.get(img["url"], timeout=30, stream=True)
            r.raise_for_status()
            with open(fpath, "wb") as f:
                for chunk in r.iter_content(8192):
                    f.write(chunk)
            downloaded.append(fpath)
            logging.info(f"Downloaded: {fname} (by {img['author']})")
            time.sleep(0.3)
        except Exception as e:
            logging.warning(f"Download {img['illust_id']} failed: {e}")

    logging.info(f"Downloaded {len(downloaded)} images total")
    return downloaded


def create_zip(files):
    today = datetime.now().strftime("%Y-%m-%d")
    zip_name = f"pixiv_daily_{today}.zip"
    zip_path = f"/tmp/{zip_name}"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(f, os.path.basename(f))
    zip_size = os.path.getsize(zip_path) / 1024 / 1024
    logging.info(f"Created zip: {zip_name} ({zip_size:.1f} MB)")
    return zip_path


def send_email(zip_path, tags):
    sender = os.environ["GMAIL_SENDER"].encode("ascii", errors="ignore").decode()
    pwd = os.environ["GMAIL_APP_PASSWORD"].encode("ascii", errors="ignore").decode()
    receiver = os.environ.get("GMAIL_RECEIVER", sender).encode("ascii", errors="ignore").decode()
    today = datetime.now().strftime("%Y年%m月%d日")

    msg = MIMEMultipart(policy=email.policy.SMTP)
    msg["From"] = sender
    msg["To"] = receiver
    msg["Subject"] = f"[Pixiv Daily] {today} {tags} 图包"

    body = f"今日({today}) Pixiv 标签搜索「{tags}」热门插画已打包，请查收附件。\n\n此邮件由 GitHub Actions 自动发送。"
    msg.attach(MIMEText(body, "plain", "utf-8"))

    with open(zip_path, "rb") as f:
        part = MIMEBase("application", "zip")
        part.set_payload(f.read())
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", "attachment", filename=os.path.basename(zip_path))
    msg.attach(part)

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as srv:
        srv.ehlo()
        srv.starttls()
        srv.ehlo()
        srv.login(sender, pwd)
        srv.send_message(msg)
    logging.info(f"Email sent to {receiver}")


def main():
    logging.info("=" * 50)
    logging.info("Pixiv Daily (cloud v4 - Playwright + requests) starting")

    try:
        image_urls, cookies, tags = login_and_get_image_list()
        if not image_urls:
            logging.error("No image URLs collected")
            sys.exit(1)

        files = download_images(image_urls, cookies)
        if not files:
            logging.error("No images downloaded")
            sys.exit(1)

        zip_path = create_zip(files)
        send_email(zip_path, tags)
        logging.info("Pixiv Daily finished successfully")
    except Exception as e:
        logging.error(f"Fatal error: {e}")
        raise


if __name__ == "__main__":
    main()
