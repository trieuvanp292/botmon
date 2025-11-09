# api/webhook.py
import os
import re
import time
import logging
from flask import Flask, request, jsonify
import requests
import cloudscraper

# Cấu hình logging đơn giản
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("webhook")

TELEGRAM_TOKEN = "6687980234:AAFV-64WHa6HQaDX6LkvYNAb70C9ave_rjs"
if not TELEGRAM_TOKEN:
    log.warning("TELEGRAM_TOKEN not set in env. Set TELEGRAM_TOKEN in Vercel env variables.")

TG_API_BASE = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

app = Flask(__name__)

def send_telegram_message(chat_id, text, parse_mode=None):
    """Gửi message qua Telegram sendMessage"""
    url = f"{TG_API_BASE}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    try:
        r = requests.post(url, json=payload, timeout=10)
        log.info("Telegram sendMessage status: %s", r.status_code)
        return r.ok
    except Exception as e:
        log.exception("Failed to send message to Telegram: %s", e)
        return False

def get_key_from_site(timeout=20):
    """
    Phiên bản gọn của script cloudscraper của bạn.
    Trả về (success, message) - nếu success True, message là key.
    """
    try:
        scraper = cloudscraper.create_scraper()
        # Bước 1: lấy redirect link chứa code
        url_start = "https://duymmo.io.vn/vip/mondz.php"
        headers_base = {
            'User-Agent': ("Mozilla/5.0 (Linux; Android 11; RMX3269 Build/RP1A.201005.001) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.7390.122 Mobile Safari/537.36"),
            'Accept': "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            'sec-ch-ua': "\"Android WebView\";v=\"141\", \"Not?A_Brand\";v=\"8\", \"Chromium\";v=\"141\"",
            'sec-ch-ua-mobile': "?1",
            'sec-ch-ua-platform': "\"Android\"",
            'upgrade-insecure-requests': "1",
            'dnt': "1",
            'x-requested-with': "mark.via.gp",
            'sec-fetch-site': "none",
            'sec-fetch-mode': "navigate",
            'sec-fetch-user': "?1",
            'sec-fetch-dest': "document",
            'accept-language': "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
        }

        r1 = scraper.get(url_start, headers=headers_base, timeout=timeout)
        # tìm pattern redirect -> lấy code sau domain
        m = re.search(r'window\.location\.href\s*=\s*"https?://[^/]+/([^"]+)"', r1.text)
        if not m:
            return False, "Không tìm thấy redirect code (step 1)"
        code = m.group(1)

        # Bước 2: truy cập /api-mode/{code}
        url_api_mode = f"https://linkday.xyz/api-mode/{code}"
        r2 = scraper.get(url_api_mode, headers=headers_base, timeout=timeout)

        # Lấy csrf token từ meta
        m2 = re.search(r'<meta\s+[^>]*name=["\']csrf-token["\'][^>]*content=["\']([^"\']+)["\']', r2.text, re.IGNORECASE)
        csrf = m2.group(1) if m2 else None

        payload = {
            '_token': csrf,
            'screen': "391 x 868",
            'browser': "Chrome",
            'browser_version': "140.0.7339.155",
            'browser_major_version': "140",
            'has_cookie': "true",
            'os': "Android",
            'os_version': "11",
            'flash_version': "no check",
            'client_id': "",
            'pathname': "",
            'href': "",
            'hostname': ""
        }

        # post đầu tiên (theo flow của bạn)
        r3 = scraper.post(r2.url, data=payload, headers=headers_base, timeout=timeout, allow_redirects=True)
        # Lấy code từ redirect URL (funlink)
        # Nếu flow khác thì cần điều chỉnh
        if not r3 or not r3.url:
            return False, "Không có response redirect (step 2)"
        # Theo script bạn: r.url.replace("https://funlink.io/st?apikey=...&url=https://linkday.xyz/", "")
        # Tách code cuối
        # Nếu funlink redirect chứa url=..., ta lấy phần sau
        mcode = re.search(r'url=https?://[^/]+/([^&]+)', r3.url)
        code2 = mcode.group(1) if mcode else None
        if code2:
            url_api_mode2 = f"https://linkday.xyz/api-mode/{code2}"
            r4 = scraper.post(url_api_mode2, data=payload, headers=headers_base, timeout=timeout, allow_redirects=True)
            if not r4 or not r4.url:
                return False, "Không có response redirect (step 3)"
            # cuối cùng theo bạn: r.url.replace("https://funlink.io/st?apikey=...&url=https://duymmo.io.vn/key2.php?key=", "")
            # ta cố gắng lấy key cuối cùng từ url
            final_m = re.search(r'key=([^&]+)', r4.url)
            if final_m:
                final_key = final_m.group(1)
                return True, final_key
            else:
                # fallback: nếu url chứa funlink prefix + linkday etc, trả về toàn bộ url để debug
                return False, f"Không tìm thấy key cuối cùng, url cuối: {r4.url}"
        else:
            # fallback: nếu r3.url chứa key trực tiếp
            final_m = re.search(r'key=([^&]+)', r3.url)
            if final_m:
                return True, final_m.group(1)
            else:
                return False, f"Không tìm thấy code giữa các bước, r3.url={r3.url}"

    except Exception as e:
        log.exception("get_key error")
        return False, f"Exception: {e}"

@app.route("/api/webhook", methods=["POST"])
def telegram_webhook():
    """
    Endpoint webhook cho Telegram. Vercel sẽ gọi file này khi có update.
    """
    try:
        data = request.get_json(force=True)
    except Exception:
        return jsonify({"ok": False, "error": "Invalid JSON"}), 400

    # simple guard: chỉ xử lý message text
    message = data.get("message") or data.get("edited_message")
    if not message:
        # trả 200 để Telegram không retry
        return jsonify({"ok": True}), 200

    chat_id = message["chat"]["id"]
    text = message.get("text", "").strip()

    log.info("Received message from %s: %s", chat_id, text)

    if text.startswith("/start"):
        send_telegram_message(chat_id, "Chào! Gửi /bypass để lấy key.")
        return jsonify({"ok": True}), 200

    if text.startswith("/bypass"):
        # Gửi tin nhắn xác nhận (nếu muốn)
        send_telegram_message(chat_id, "Đang chạy bypass, vui lòng chờ (tối đa ~20s)...")

        # Thực hiện lấy key (chạy synchronously)
        success, result = get_key_from_site(timeout=20)
        if success:
            send_telegram_message(chat_id, f"✅ Lấy key thành công:\n`{result}`", parse_mode="Markdown")
        else:
            send_telegram_message(chat_id, f"❌ Lỗi khi lấy key: {result}")

        # Trả 200 cho Telegram
        return jsonify({"ok": True}), 200

    # nếu lệnh khác
    send_telegram_message(chat_id, "Lệnh không hợp lệ. Dùng /start hoặc /bypass.")
    return jsonify({"ok": True}), 200

# Vercel expects the file to expose "app"
# No need for if __name__ == '__main__' here.