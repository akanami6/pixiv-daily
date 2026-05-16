#!/usr/bin/env python3
"""
Pixiv Daily — GitHub Actions cloud version.
Uses Playwright headless browser to handle Pixiv's JavaScript login.
"""
import os
import sys
import re
import zipfile
import logging
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

IMAGE_COUNT = 10
IMAGES_DIR = "/tmp/pixiv_images"


def login_and_get_images():
    """Use Playwright to login and fetch ranking image URLs."""
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

        # Step 1: Go to login page
        logging.info("Opening Pixiv login page...")
        page.goto("https://accounts.pixiv.net/login?lang=zh&source=pc&view_type=page", wait_until="domcontentloaded")
        page.wait_for_timeout(2000)

        # Accept privacy notice if present
        ok_btn = page.query_selector('button:has-text("OK")')
        if ok_btn and ok_btn.is_visible():
            ok_btn.click()
            page.wait_for_timeout(500)

        # Fill login form
        logging.info("Filling login form...")
        page.fill('input[placeholder*="邮箱"], input[placeholder*="pixiv ID"], input[placeholder*="メールアドレス"]', username)
        page.fill('input[placeholder*="密码"], input[placeholder*="パスワード"]', password)
        page.wait_for_timeout(500)

        # Click login button
        login_btn = page.query_selector('button:has-text("登录"), button:has-text("ログイン")')
        if login_btn and login_btn.is_enabled():
            login_btn.click()
        else:
            # Press Enter in the password field
            page.keyboard.press("Enter")

        page.wait_for_timeout(3000)
        logging.info(f"After login URL: {page.url}")

        # Step 2: Get ranking JSON
        logging.info("Fetching ranking data...")
        page.goto("https://www.pixiv.net/ranking.php?mode=daily&format=json", wait_until="domcontentloaded")
        page.wait_for_timeout(2000)

        body_text = page.evaluate("() => document.body.innerText")
        import json
        try:
            data = json.loads(body_text)
        except json.JSONDecodeError:
            logging.error(f"Failed to parse ranking JSON: {body_text[:500]}")
            browser.close()
            return []

        contents = data.get("contents", [])
        logging.info(f"Got {len(contents)} ranking entries")

        # Step 3: Build image URL list
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

        # Step 4: Download images via page (to reuse auth cookies)
        logging.info(f"Downloading {len(image_urls)} images...")
        os.makedirs(IMAGES_DIR, exist_ok=True)
        downloaded = []

        for img in image_urls:
            fname = f"{img['illust_id']}.{img['url'].split('.')[-1].split('?')[0]}"
            fpath = os.path.join(IMAGES_DIR, fname)
            try:
                response = page.evaluate("""
                    async ([url, fname]) => {
                        const resp = await fetch(url, {
                            headers: { 'Referer': 'https://www.pixiv.net/' }
                        });
                        if (!resp.ok) throw new Error('HTTP ' + resp.status);
                        const blob = await resp.blob();
                        const reader = new FileReader();
                        return new Promise((resolve, reject) => {
                            reader.onload = () => resolve(reader.result);
                            reader.onerror = reject;
                            reader.readAsDataURL(blob);
                        });
                    }
                """, [img["url"], fname])
                # Decode base64 data URL
                import base64
                b64_data = response.split(",", 1)[1]
                with open(fpath, "wb") as f:
                    f.write(base64.b64decode(b64_data))
                downloaded.append(fpath)
                logging.info(f"Downloaded: {fname} (by {img['author']})")
                time.sleep(0.3)
            except Exception as e:
                logging.warning(f"Download {img['illust_id']} failed: {e}")

        browser.close()
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
    sender = os.environ["GMAIL_SENDER"]
    pwd = os.environ["GMAIL_APP_PASSWORD"]
    receiver = os.environ.get("GMAIL_RECEIVER", sender)
    today = datetime.now().strftime("%Y年%m月%d日")

    msg = MIMEMultipart()
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
        srv.ehlo()
        srv.starttls()
        srv.ehlo()
        srv.login(sender, pwd)
        srv.send_message(msg)
    logging.info(f"Email sent to {receiver}")


def main():
    logging.info("=" * 50)
    logging.info("Pixiv Daily (cloud v3 - Playwright) starting")

    try:
        files = login_and_get_images()
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
