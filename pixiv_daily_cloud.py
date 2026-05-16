#!/usr/bin/env python3
"""
Pixiv Daily — cloud version (GitHub Actions).
Reads credentials from environment variables instead of config file.
"""
import os
import sys
import zipfile
import logging
import smtplib
import time
import io
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders
from datetime import datetime

from pixivpy3 import AppPixivAPI

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

SETTINGS = {
    "image_count": 10,
    "quality": "large",
    "ranking_mode": "day",
    "min_bookmarks": 100,
    "search_tags": ["女の子", "オリジナル"],
    "use_ranking": True,
}

IMAGES_DIR = "/tmp/pixiv_images"


def pixiv_login(api):
    """Login — try refresh token first, then password."""
    token = os.environ.get("PIXIV_REFRESH_TOKEN", "")
    if token:
        try:
            api.auth(refresh_token=token)
            logging.info("Logged in via refresh token")
            return
        except Exception as e:
            logging.warning(f"Refresh token failed: {e}")

    username = os.environ["PIXIV_USERNAME"]
    password = os.environ["PIXIV_PASSWORD"]
    resp = api.login(username, password)
    new_token = resp.get("refresh_token", "")
    if new_token and new_token != token:
        logging.info(f"New refresh token obtained: {new_token[:8]}...{new_token[-8:]}")
        logging.info("Update PIXIV_REFRESH_TOKEN secret with this value for faster login")


def fetch_illustrations(api):
    ids = []
    resp = api.illust_ranking(mode=SETTINGS["ranking_mode"])
    for item in resp.get("illusts", []):
        if len(ids) >= SETTINGS["image_count"] * 3:
            break
        if item.get("total_bookmarks", 0) >= SETTINGS["min_bookmarks"]:
            ids.append(item["id"])
    logging.info(f"Got {len(ids)} illust IDs from ranking")
    return ids


def download_images(api, illust_ids):
    os.makedirs(IMAGES_DIR, exist_ok=True)
    quality = SETTINGS["quality"]
    max_count = SETTINGS["image_count"]
    downloaded = []

    for iid in illust_ids:
        if len(downloaded) >= max_count:
            break
        try:
            detail = api.illust_detail(iid)
            illust = detail["illust"]
            url = illust["image_urls"].get(quality, illust["image_urls"]["large"])
            ext = url.split(".")[-1].split("?")[0]
            fname = f"{iid}.{ext}"
            fpath = os.path.join(IMAGES_DIR, fname)
            api.download(url, path=IMAGES_DIR, fname=fname)
            downloaded.append(fpath)
            logging.info(f"Downloaded: {fname}")
            time.sleep(0.5)
        except Exception as e:
            logging.warning(f"Download illust {iid} failed: {e}")
            continue

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
    password = os.environ["GMAIL_APP_PASSWORD"]
    receiver = os.environ.get("GMAIL_RECEIVER", sender)
    today = datetime.now().strftime("%Y年%m月%d日")

    msg = MIMEMultipart()
    msg["From"] = sender
    msg["To"] = receiver
    msg["Subject"] = f"[Pixiv Daily] {today} 二次元美少女图包"

    body = f"今日({today}) Pixiv 热门二次元美少女图片已打包，请查收附件。\n\n此邮件由 GitHub Actions 自动发送，无需回复。"
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
        srv.login(sender, password)
        srv.send_message(msg)
    logging.info(f"Email sent to {receiver}")


def main():
    logging.info("=" * 50)
    logging.info("Pixiv Daily (cloud) starting")

    api = AppPixivAPI()

    try:
        pixiv_login(api)
        illust_ids = fetch_illustrations(api)
        if not illust_ids:
            logging.error("No illustrations found")
            sys.exit(1)

        files = download_images(api, illust_ids)
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
