#!/usr/bin/env python3
"""
Anime Daily — fetch images from Konachan/Danbooru, zip and email via GitHub Actions.
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
from urllib.parse import quote

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

IMAGE_COUNT = 10
IMAGES_DIR = "/tmp/anime_images"
SEARCH_TAGS = os.environ.get("SEARCH_TAGS", "large_breasts")
MIN_SCORE = int(os.environ.get("MIN_SCORE", "0"))
USER_AGENT = "AnimeDaily/1.0 (GitHub Actions)"


def fetch_posts():
    """Fetch from yande.re or konachan (no auth needed)."""
    tag_string = SEARCH_TAGS
    limit = 100  # fetch more, then random select

    # Try yande.re first
    url = f"https://yande.re/post.json?tags={quote(tag_string)}&limit={limit}"
    logging.info(f"Trying yande.re: {tag_string}")
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)

    if r.status_code != 200:
        logging.warning(f"yande.re: {r.status_code}, trying konachan.net...")
        url = f"https://konachan.net/post.json?tags={quote(tag_string)}&limit={limit}"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)

    if r.status_code != 200:
        logging.warning(f"konachan.net: {r.status_code}, trying lolibooru...")
        url = f"https://lolibooru.moe/post.json?tags={quote(tag_string)}&limit={limit}"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)

    r.raise_for_status()
    posts = r.json()
    random.shuffle(posts)
    logging.info(f"Got {len(posts)} posts from {r.url}, shuffled")
    return posts


def download_images(posts):
    """Download images from CDN."""
    os.makedirs(IMAGES_DIR, exist_ok=True)
    for f in os.listdir(IMAGES_DIR):
        os.remove(os.path.join(IMAGES_DIR, f))

    downloaded = []
    for post in posts:
        if len(downloaded) >= IMAGE_COUNT:
            break

        # Booru sites: yande.re/konachan use file_url/sample_url, danbooru uses large_file_url
        url = (post.get("sample_url") or post.get("file_url") or post.get("jpeg_url") or
               post.get("large_file_url") or "")
        if not url:
            continue

        ext = url.split(".")[-1].split("?")[0]
        pid = post.get("id", "0")
        fname = f"anime_{pid}.{ext}"
        fpath = os.path.join(IMAGES_DIR, fname)
        try:
            r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30, stream=True)
            r.raise_for_status()
            with open(fpath, "wb") as f:
                for chunk in r.iter_content(8192):
                    f.write(chunk)
            downloaded.append(fpath)
            score = post.get("score", 0)
            tags = post.get("tags", post.get("tag_string", ""))
            logging.info(f"Downloaded: {fname} (score:{score})")
            time.sleep(0.3)
        except Exception as e:
            logging.warning(f"Download {pid} failed: {e}")
            continue

    logging.info(f"Downloaded {len(downloaded)} images total")
    return downloaded


def create_zip(files):
    today = datetime.now().strftime("%Y-%m-%d")
    zip_name = f"anime_daily_{today}.zip"
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
    msg["Subject"] = f"[Anime Daily] {today} {SEARCH_TAGS} 图包"

    body = f"今日({today})「{SEARCH_TAGS}」高分热门插画已打包。\n\nGitHub Actions 自动发送。"
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
    logging.info(f"Anime Daily starting — tags: {SEARCH_TAGS}")

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
        logging.info("Anime Daily finished successfully")
    except Exception as e:
        logging.error(f"Fatal error: {e}")
        raise


if __name__ == "__main__":
    main()
