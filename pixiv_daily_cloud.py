#!/usr/bin/env python3
"""
Pixiv Daily — GitHub Actions cloud version.
Playwright for JS login, requests for cookie-based image download.
"""
import os
import sys
import re
import json
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


def login_and_get_image_list():
    """Use Playwright to login to Pixiv and fetch ranking image URLs + cookies."""
    from playwright.sync_api import sync_playwright

    username = os.environ["PIXIV_USERNAME"]
    password = os.environ["PIXIV_PASSWORD"]

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

        # Step 2: Get ranking data
        logging.info("Fetching ranking...")
        page.goto("https://www.pixiv.net/ranking.php?mode=daily&format=json", wait_until="domcontentloaded")
        page.wait_for_timeout(2000)

        body = page.evaluate("() => document.body.innerText")
        data = json.loads(body)
        contents = data.get("contents", [])
        logging.info(f"Got {len(contents)} ranking entries")

        # Step 3: Build image list
        image_urls = []
        for item in contents:
            if len(image_urls) >= IMAGE_COUNT:
                break
            if item.get("illust_type") != "0":
                continue
            thumb = item["url"]
            full = re.sub(r"/c/\d+x\d+/", "/", thumb)
            image_urls.append({
                "url": full,
                "illust_id": item["illust_id"],
                "title": item.get("title", ""),
                "author": item.get("user_name", "unknown"),
            })

        # Step 4: Export cookies for requests-based download
        cookies = context.cookies()
        browser.close()

        logging.info(f"Collected {len(image_urls)} image URLs and {len(cookies)} cookies")
        return image_urls, cookies


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


def send_email(zip_path):
    sender = os.environ["GMAIL_SENDER"].encode("ascii", errors="ignore").decode()
    pwd = os.environ["GMAIL_APP_PASSWORD"].encode("ascii", errors="ignore").decode()
    receiver = os.environ.get("GMAIL_RECEIVER", sender)
    today = datetime.now().strftime("%Y年%m月%d日")

    msg = MIMEMultipart(policy=email.policy.SMTP)
    msg["From"] = sender
    msg["To"] = receiver
    msg["Subject"] = f"[Pixiv Daily] {today} 二次元美少女图包"

    body = f"今日({today}) Pixiv 日榜热门插画已打包，请查收附件。\n\n此邮件由 GitHub Actions 自动发送。"
    msg.attach(MIMEText(body, "plain", "utf-8"))

    with open(zip_path, "rb") as f:
        part = MIMEBase("application", "zip")
        part.set_payload(f.read())
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", "attachment", filename=os.path.basename(zip_path))
    msg.attach(part)

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as srv:
        srv.set_debuglevel(0)
        r1 = srv.ehlo()
        logging.info(f"EHLO: {r1}")
        r2 = srv.starttls()
        logging.info(f"STARTTLS: {r2}")
        r3 = srv.ehlo()
        logging.info(f"EHLO2: {r3}")
        r4 = srv.login(sender, pwd)
        logging.info(f"LOGIN: {r4}")
        result = srv.send_message(msg)
        logging.info(f"SEND result: {result}")
        srv.quit()
    logging.info(f"Email sent to {receiver}")


def main():
    logging.info("=" * 50)
    logging.info("Pixiv Daily (cloud v4 - Playwright + requests) starting")

    try:
        image_urls, cookies = login_and_get_image_list()
        if not image_urls:
            logging.error("No image URLs collected")
            sys.exit(1)

        files = download_images(image_urls, cookies)
        if not files:
            logging.error("No images downloaded")
            sys.exit(1)

        zip_path = create_zip(files)
        send_email(zip_path)
        logging.info("Pixiv Daily finished successfully")
    except Exception as e:
        logging.error(f"Fatal error: {e}")
        raise


if __name__ == "__main__":
    main()
