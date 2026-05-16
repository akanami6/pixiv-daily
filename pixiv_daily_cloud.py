#!/usr/bin/env python3
"""
Danbooru Daily — fetch high-score anime images, zip and email via GitHub Actions.
No login required. Pure HTTP API.
"""
import os
import sys
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
IMAGES_DIR = "/tmp/danbooru_images"
SEARCH_TAGS = os.environ.get("SEARCH_TAGS", "large_breasts rating:s")
MIN_SCORE = int(os.environ.get("MIN_SCORE", "50"))
API_BASE = "https://danbooru.donmai.us"
USER_AGENT = "DanbooruDaily/1.0 (GitHub Actions; automated fetching)"


def fetch_posts():
    """Fetch posts from Danbooru API. No auth needed."""
    from urllib.parse import quote
    tag_string = f"{SEARCH_TAGS} score:>{MIN_SCORE} order:random"
    # Build URL manually to avoid double-encoding of : and >
    url = f"{API_BASE}/posts.json?tags={quote(tag_string)}&limit=100"
    logging.info(f"Fetching: {tag_string}")
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    r.raise_for_status()
    posts = r.json()
    logging.info(f"Got {len(posts)} posts")
    return posts


def download_images(posts):
    """Download images from Danbooru CDN."""
    os.makedirs(IMAGES_DIR, exist_ok=True)
    for f in os.listdir(IMAGES_DIR):
        os.remove(os.path.join(IMAGES_DIR, f))

    downloaded = []
    for post in posts:
        if len(downloaded) >= IMAGE_COUNT:
            break

        # Use sample for speed, fallback to original
        url = post.get("large_file_url") or post.get("file_url")
        if not url:
            continue
        if url.startswith("/"):
            url = "https://danbooru.donmai.us" + url

        ext = url.split(".")[-1].split("?")[0]
        iid = post["id"]
        fname = f"danbooru_{iid}.{ext}"
        fpath = os.path.join(IMAGES_DIR, fname)

        try:
            r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30, stream=True)
            r.raise_for_status()
            with open(fpath, "wb") as f:
                for chunk in r.iter_content(8192):
                    f.write(chunk)
            downloaded.append(fpath)
            artist = post.get("tag_string_artist", "unknown")
            chars = post.get("tag_string_character", "")
            score = post.get("score", 0)
            info = f"score:{score}"
            if chars:
                info += f" char:{chars}"
            logging.info(f"Downloaded: {fname} by {artist} ({info})")
            time.sleep(0.3)
        except Exception as e:
            logging.warning(f"Download {iid} failed: {e}")
            continue

    logging.info(f"Downloaded {len(downloaded)} images total")
    return downloaded


def create_zip(files):
    today = datetime.now().strftime("%Y-%m-%d")
    zip_name = f"danbooru_daily_{today}.zip"
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
    receiver = os.environ.get("GMAIL_RECEIVER", sender).encode("ascii", errors="ignore").decode()
    today = datetime.now().strftime("%Y年%m月%d日")

    msg = MIMEMultipart(policy=email.policy.SMTP)
    msg["From"] = sender
    msg["To"] = receiver
    msg["Subject"] = f"[Danbooru Daily] {today} {SEARCH_TAGS} 图包"

    body = f"今日({today}) Danbooru「{SEARCH_TAGS}」高分热门插画已打包。\n\nGitHub Actions 自动发送。"
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
    logging.info(f"Danbooru Daily starting — tags: {SEARCH_TAGS}")

    try:
        posts = fetch_posts()
        if not posts:
            logging.error("No posts found")
            sys.exit(1)

        files = download_images(posts)
        if not files:
            logging.error("No images downloaded")
            sys.exit(1)

        zip_path = create_zip(files)
        send_email(zip_path)
        logging.info("Danbooru Daily finished successfully")
    except Exception as e:
        logging.error(f"Fatal error: {e}")
        raise


if __name__ == "__main__":
    main()
