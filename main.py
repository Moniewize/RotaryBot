import os
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, urlunparse, urljoin
from flask import Flask, make_response
import requests
import feedparser
from bs4 import BeautifulSoup

# ==================== FLASK APPLICATION INSTANCE ====================
app = Flask(__name__)

@app.after_request
def minimize_headers(response):
    response.headers.clear()
    response.headers['Content-Type'] = 'text/plain'
    return response

# ==================== CONFIGURATION ====================
GREEN_API_ID_INSTANCE = os.getenv("GREEN_API_ID_INSTANCE", "")
GREEN_API_TOKEN_INSTANCE = os.getenv("GREEN_API_TOKEN_INSTANCE", "")
RECIPIENT_PHONE_NUMBER = os.getenv("RECIPIENT_PHONE_NUMBER", "")

# NEW: Telegram settings
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

CUSTOM_FOOTER = os.getenv(
    "CUSTOM_FOOTER",
    "*Source:* rotary.org and other affiliated sources\n*Brought by:* RAC-FUTO Editorial Team"
)

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


def whatsapp_ready():
    return bool(GREEN_API_ID_INSTANCE and GREEN_API_TOKEN_INSTANCE and RECIPIENT_PHONE_NUMBER)


def telegram_ready():  # NEW
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


def clean_url(raw_url):
    """Strips query parameters to ensure clean, direct links."""
    if not raw_url:
        return ""
    parsed = urlparse(raw_url.strip())
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, '', '', ''))


def parse_feed(url):  # NEW: downloads with a time limit, so a slow site cannot hang the run
    response = requests.get(url, headers=BROWSER_HEADERS, timeout=8)
    response.raise_for_status()
    return feedparser.parse(response.content)


@app.errorhandler(Exception)
def handle_global_exception(e):
    print(f"Unhandled error: {type(e).__name__}")  # NEW: leave a trace in the logs
    return make_response("OK", 200)


# ==================== 1. PRIMARY SOURCE: ROTARY.ORG ====================
def fetch_primary_rotary_org(now_utc):
    """Scrapes primary official news from https://www.rotary.org/en/news-and-stories."""
    articles = []

    # 1A. Direct news feed from rotary.org
    try:
        feed = parse_feed("https://www.rotary.org/rss.xml")
        for entry in feed.entries:
            title = getattr(entry, "title", "").strip()
            link = getattr(entry, "link", "").strip()

            if title and link and "rotary.org" in link:
                pub_dt = None
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    pub_dt = datetime.fromtimestamp(time.mktime(entry.published_parsed), tz=timezone.utc)
                elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                    pub_dt = datetime.fromtimestamp(time.mktime(entry.updated_parsed), tz=timezone.utc)

                if not pub_dt:
                    pub_dt = now_utc

                hours_old = (now_utc - pub_dt).total_seconds() / 3600.0

                articles.append({
                    "title": title,
                    "url": clean_url(link),
                    "published_at": pub_dt,
                    "within_2_weeks": hours_old <= 336.0,
                    "priority": 1
                })
    except Exception as e:
        print(f"Error parsing primary feed: {type(e).__name__}")

    # 1B. Direct Web Scraper for rotary.org/en/news-and-stories
    try:
        url = "https://www.rotary.org/en/news-and-stories"
        res = requests.get(url, headers=BROWSER_HEADERS, timeout=8)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            for a_tag in soup.find_all("a", href=True):
                href = a_tag["href"]
                if "/en/articles/" in href:
                    title = a_tag.get_text(strip=True)
                    if not title or len(title) < 15 or title.lower() in ["read more", "news and stories", "all news and stories"]:
                        continue

                    full_url = urljoin("https://www.rotary.org", href)
                    articles.append({
                        "title": title,
                        "url": clean_url(full_url),
                        "published_at": now_utc - timedelta(days=1),
                        "within_2_weeks": True,
                        "priority": 1
                    })
    except Exception as e:
        print(f"Error scraping primary rotary.org: {type(e).__name__}")

    return articles


# ==================== 2. SECONDARY SOURCES ====================
def fetch_secondary_rotary_feeds(now_utc):
    """Parses strictly Rotary Intercountry Committees and Rotary News Online."""
    secondary_feeds = [
        ("Rotary Intercountry Committees", "https://rotary-icc.org/feed/", 2),
        ("Rotary News Online", "https://rotarynewsonline.org/feed/", 3)
    ]

    articles = []

    for source_name, feed_url, priority in secondary_feeds:
        try:
            feed = parse_feed(feed_url)
            for entry in feed.entries:
                title = getattr(entry, "title", "").strip()
                link = getattr(entry, "link", "").strip()

                if not title or not link:
                    continue

                pub_dt = None
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    pub_dt = datetime.fromtimestamp(time.mktime(entry.published_parsed), tz=timezone.utc)
                elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                    pub_dt = datetime.fromtimestamp(time.mktime(entry.updated_parsed), tz=timezone.utc)

                if not pub_dt:
                    pub_dt = now_utc

                hours_old = (now_utc - pub_dt).total_seconds() / 3600.0

                articles.append({
                    "title": title,
                    "url": clean_url(link),
                    "published_at": pub_dt,
                    "within_2_weeks": hours_old <= 336.0,
                    "priority": priority
                })
        except Exception as e:
            print(f"Error parsing feed {source_name}: {type(e).__name__}")

    return articles


# ==================== 3. AGGREGATION & HIERARCHICAL SELECTION ====================
def get_top_10_rotary_news():
    """Builds top 10 articles strictly adhering to site hierarchy and date recency."""
    now_utc = datetime.now(timezone.utc)

    primary_candidates = fetch_primary_rotary_org(now_utc)
    secondary_candidates = fetch_secondary_rotary_feeds(now_utc)

    all_candidates = primary_candidates + secondary_candidates

    recent_articles = []
    older_articles = []
    seen_urls = set()
    seen_titles = set()

    for item in all_candidates:
        clean_link = item["url"]
        title_key = item["title"].lower()[:30]

        if clean_link in seen_urls or title_key in seen_titles:
            continue

        seen_urls.add(clean_link)
        seen_titles.add(title_key)

        if item["within_2_weeks"]:
            recent_articles.append(item)
        else:
            older_articles.append(item)

    recent_articles.sort(key=lambda x: (x["priority"], -x["published_at"].timestamp()))
    older_articles.sort(key=lambda x: (x["priority"], -x["published_at"].timestamp()))

    final_selection = recent_articles[:10]

    if len(final_selection) < 10:
        needed = 10 - len(final_selection)
        final_selection.extend(older_articles[:needed])

    return final_selection


# ==================== 4. PAYLOAD FORMATTING ====================
def format_whatsapp_message(articles, footer_text=""):
    """Builds the message used for both WhatsApp and Telegram."""
    message_lines = [
        "📌 *Rotary & Rotaract Global News Update*\n",
        "Here are today's top official reports and featured initiatives:\n"
    ]

    for idx, art in enumerate(articles, 1):
        # NEW: characters that break bold text or links in chat apps are removed
        safe_title = re.sub(r"[*_`\[\]]", "", art["title"])
        message_lines.append(f"{idx}. *{safe_title}*")
        message_lines.append(f"🔗 {art['url']}\n")

    if footer_text.strip():
        message_lines.append(footer_text.strip())

    return "\n".join(message_lines)


# ==================== 5. DISPATCH ====================
def send_whatsapp_message(message_text):
    if not whatsapp_ready():
        print("WhatsApp: skipped, credentials not set.")
        return False

    chat_id = f"{RECIPIENT_PHONE_NUMBER}@c.us"
    url = f"https://api.green-api.com/waInstance{GREEN_API_ID_INSTANCE}/sendMessage/{GREEN_API_TOKEN_INSTANCE}"
    payload = {"chatId": chat_id, "message": message_text}
    headers = {'Content-Type': 'application/json'}

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=8)
        response.raise_for_status()
        print("WhatsApp: message sent.")
        return True
    except Exception as error:
        # the error text is not printed because it contains the web address with the token
        status = getattr(getattr(error, "response", None), "status_code", None)
        print(f"WhatsApp: delivery failed ({type(error).__name__}, status {status}).")
        return False


def split_for_telegram(text, limit=4000):  # NEW: Telegram accepts at most 4096 characters
    chunks, current = [], ""
    for line in text.split("\n"):
        if current and len(current) + len(line) + 1 > limit:
            chunks.append(current.rstrip("\n"))
            current = ""
        current += line + "\n"
    if current.strip():
        chunks.append(current.rstrip("\n"))
    return chunks


def send_telegram_message(message_text):  # NEW
    if not telegram_ready():
        print("Telegram: skipped, credentials not set.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    for chunk in split_for_telegram(message_text):
        sent = False
        for extra in ({"parse_mode": "Markdown"}, {}):  # plain text if Markdown is rejected
            try:
                response = requests.post(
                    url, json={"chat_id": TELEGRAM_CHAT_ID, "text": chunk, **extra}, timeout=10
                )
            except Exception as error:
                print(f"Telegram: not reachable ({type(error).__name__}).")
                return False
            if response.status_code == 400 and extra:
                print(f"Telegram: formatting rejected ({response.text[:150]}). Retrying as plain text.")
                continue
            if response.ok:
                sent = True
            else:
                print(f"Telegram: refused ({response.status_code}): {response.text[:200]}")
            break
        if not sent:
            return False

    print("Telegram: message sent.")
    return True


# ==================== FLASK ENDPOINTS ====================
@app.route('/run-cron', methods=['GET', 'POST'])
def run_cron_job():
    if not (whatsapp_ready() or telegram_ready()):  # CHANGED: either channel is enough
        print("No delivery channel is configured.")
        return make_response("OK", 200)

    selected_articles = get_top_10_rotary_news()

    if not selected_articles:
        message = ("📌 *Rotary & Rotaract Global News Update*\n\n"
                   "There are currently no new posts or updates available.\n\n"
                   + CUSTOM_FOOTER.strip())
    else:
        message = format_whatsapp_message(selected_articles, CUSTOM_FOOTER)

    send_whatsapp_message(message)  # each channel is independent
    send_telegram_message(message)

    return make_response("OK", 200)


@app.route('/', methods=['GET'])
def health_check():
    return make_response("OK", 200)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)