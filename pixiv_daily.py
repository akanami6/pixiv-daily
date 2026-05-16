#!/usr/bin/env python3
"""
Pixiv Daily — fetch popular anime illustrations and email them daily.
"""
import json
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

# ── Paths ──────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
IMAGES_DIR = os.path.join(BASE_DIR, "images")
LOG_PATH = os.path.join(BASE_DIR, "pixiv_daily.log")

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    encoding="utf-8",
)


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    # validate
    for key in ("username", "password"):
        if cfg["pixiv"].get(key, "").startswith("YOUR_"):
            logging.error("Please fill in your Pixiv credentials in config.json")
            sys.exit(1)
    for key in ("sender", "password"):
        if cfg["email"].get(key, "").startswith("YOUR_"):
            logging.error("Please fill in your email credentials in config.json")
            sys.exit(1)
    return cfg


def pixiv_login(api, cfg):
    """Login via refresh token or username/password, cache token."""
    token = cfg["pixiv"].get("refresh_token", "")
    if token:
        try:
            api.auth(refresh_token=token)
            logging.info("Logged in via refresh token")
            return
        except Exception as e:
            logging.warning(f"Refresh token failed: {e}, falling back to password")

    resp = api.login(cfg["pixiv"]["username"], cfg["pixiv"]["password"])
    new_token = resp.get("refresh_token", "")
    if new_token:
        cfg["pixiv"]["refresh_token"] = new_token
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=4)
        logging.info("Saved new refresh token to config")


def fetch_illustrations(api, cfg):
    """Get illust IDs from ranking or search."""
    settings = cfg["settings"]
    ids = []

    if settings.get("use_ranking", True):
        mode = settings.get("ranking_mode", "day")
        resp = api.illust_ranking(mode=mode)
        for item in resp.get("illusts", []):
            if len(ids) >= settings["image_count"] * 3:
                break
            if item.get("total_bookmarks", 0) >= settings.get("min_bookmarks", 0):
                ids.append(item["id"])
        logging.info(f"Got {len(ids)} illust IDs from ranking")

    # supplement with search if not enough
    if len(ids) < settings["image_count"]:
        for tag in settings.get("search_tags", ["女の子"]):
            resp = api.search_illust(tag, search_target="partial_match_for_tags",
                                     sort="popular_desc", duration="within_last_day")
            for item in resp.get("illusts", []):
                if item["id"] not in ids and item.get("total_bookmarks", 0) >= settings.get("min_bookmarks", 0):
                    ids.append(item["id"])
                if len(ids) >= settings["image_count"] * 3:
                    break
            if len(ids) >= settings["image_count"] * 3:
                break
        logging.info(f"Got {len(ids)} illust IDs after search supplement")

    return ids


def download_images(api, illust_ids, cfg):
    """Download images, return list of file paths."""
    os.makedirs(IMAGES_DIR, exist_ok=True)
    # clear old images
    for f in os.listdir(IMAGES_DIR):
        os.remove(os.path.join(IMAGES_DIR, f))

    quality = cfg["settings"].get("quality", "large")
    max_count = cfg["settings"]["image_count"]
    downloaded = []

    for iid in illust_ids:
        if len(downloaded) >= max_count:
            break
        try:
            detail = api.illust_detail(iid)
            illust = detail["illust"]
            if illust.get("page_count", 1) > 1:
                # multi-page — get all pages
                pages = illust.get("meta_pages", [])
                if not pages:
                    pages = [illust.get("meta_single_page", {})]
                for idx, page in enumerate(pages):
                    if len(downloaded) >= max_count:
                        break
                    url = page["image_urls"].get(quality, page["image_urls"]["large"])
                    fname = f"{iid}_p{idx}.{url.split('.')[-1].split('?')[0]}"
                    fpath = os.path.join(IMAGES_DIR, fname)
                    api.download(url, path=IMAGES_DIR, fname=fname)
                    downloaded.append(fpath)
                    time.sleep(0.3)
            else:
                url = illust["image_urls"].get(quality, illust["image_urls"]["large"])
                ext = url.split(".")[-1].split("?")[0]
                fname = f"{iid}.{ext}"
                fpath = os.path.join(IMAGES_DIR, fname)
                api.download(url, path=IMAGES_DIR, fname=fname)
                downloaded.append(fpath)
                time.sleep(0.3)
        except Exception as e:
            logging.warning(f"Download illust {iid} failed: {e}")
            continue

    logging.info(f"Downloaded {len(downloaded)} images")
    return downloaded


def create_zip(files):
    """Zip all downloaded files and return zip path."""
    today = datetime.now().strftime("%Y-%m-%d")
    zip_name = f"pixiv_daily_{today}.zip"
    zip_path = os.path.join(BASE_DIR, zip_name)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(f, os.path.basename(f))
    zip_size = os.path.getsize(zip_path) / 1024 / 1024
    logging.info(f"Created zip: {zip_name} ({zip_size:.1f} MB)")
    return zip_path


def send_email(cfg, zip_path):
    """Send zip via Gmail SMTP."""
    email_cfg = cfg["email"]
    today = datetime.now().strftime("%Y年%m月%d日")

    msg = MIMEMultipart()
    msg["From"] = email_cfg["sender"]
    msg["To"] = email_cfg["receiver"]
    msg["Subject"] = f"[Pixiv Daily] {today} 二次元美少女图包"

    body = f"今日({today}) Pixiv 热门二次元美少女图片已打包，请查收附件。\n\n自动发送，无需回复。"
    msg.attach(MIMEText(body, "plain", "utf-8"))

    with open(zip_path, "rb") as f:
        part = MIMEBase("application", "zip")
        part.set_payload(f.read())
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", "attachment", filename=os.path.basename(zip_path))
    msg.attach(part)

    try:
        with smtplib.SMTP(email_cfg["smtp_server"], email_cfg["smtp_port"], timeout=30) as srv:
            srv.ehlo()
            srv.starttls()
            srv.ehlo()
            srv.login(email_cfg["sender"], email_cfg["password"])
            srv.send_message(msg)
        logging.info(f"Email sent to {email_cfg['receiver']}")
        print(f"[OK] Email sent to {email_cfg['receiver']}")
    except Exception as e:
        logging.error(f"Email failed: {e}")
        raise


def cleanup(zip_path):
    """Remove downloaded images and optionally the zip."""
    for f in os.listdir(IMAGES_DIR):
        os.remove(os.path.join(IMAGES_DIR, f))
    # keep zip for reference, remove old ones (>7 days)
    for f in os.listdir(BASE_DIR):
        if f.startswith("pixiv_daily_") and f.endswith(".zip"):
            fpath = os.path.join(BASE_DIR, f)
            if os.path.getmtime(fpath) < time.time() - 7 * 86400:
                os.remove(fpath)


def main():
    logging.info("=" * 50)
    logging.info("Pixiv Daily starting")

    cfg = load_config()
    api = AppPixivAPI()

    try:
        pixiv_login(api, cfg)
        illust_ids = fetch_illustrations(api, cfg)
        if not illust_ids:
            logging.error("No illustrations found")
            sys.exit(1)

        files = download_images(api, illust_ids, cfg)
        if not files:
            logging.error("No images downloaded")
            sys.exit(1)

        zip_path = create_zip(files)
        send_email(cfg, zip_path)
        cleanup(zip_path)
        logging.info("Pixiv Daily finished successfully")
    except Exception as e:
        logging.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
