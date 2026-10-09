import os
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

CUSTOM_FOOTER = os.getenv(
    "CUSTOM_FOOTER",
    "*Source:* rotary.org\n*Brought by:* RAC-FUTO Editorial Team"
)

def clean_url(raw_url):
    """Strips query parameters to ensure clean, direct rotary.org links."""
    if not raw_url:
        return ""
    parsed = urlparse(raw_url.strip())
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, '', '', ''))

@app.errorhandler(Exception)
def handle_global_exception(e):
    return make_response("OK", 200)


# ==================== 1. FETCH EXCLUSIVELY FROM ROTARY.ORG RSS ====================
def fetch_rotary_org_rss(now_utc):
    """Parses official rotary.org RSS feed."""
    rss_url = "https://www.rotary.org/rss.xml"
    articles = []

    try:
        feed = feedparser.parse(rss_url)
        for entry in feed.entries:
            title = getattr(entry, "title", "").strip()
            link = getattr(entry, "link", "").strip()

            if not title or not link or "rotary.org" not in link:
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
                "within_2_weeks": hours_old <= 336.0
            })
    except Exception as e:
        print(f"Error reading rotary.org RSS: {e}")

    return articles


# ==================== 2. SCRAPE ROTARY.ORG DIRECTLY (EXCLUSIVE FALLBACK) ====================
def scrape_rotary_org_newsroom(now_utc):
    """Scrapes rotary.org news section if RSS returns fewer than 10 stories."""
    url = "https://www.rotary.org/en/news-and-stories"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    scraped = []

    try:
        res = requests.get(url, headers=headers, timeout=8)
        if res.status_code != 200:
            return []

        soup = BeautifulSoup(res.text, "html.parser")

        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            if "/en/articles/" in href:
                title = a_tag.get_text(strip=True)
                if not title or len(title) < 15 or title.lower() in ["read more", "news and stories"]:
                    continue

                full_url = urljoin("https://www.rotary.org", href)
                scraped.append({
                    "title": title,
                    "url": clean_url(full_url),
                    "published_at": now_utc - timedelta(days=15),  # Secondary fallback priority
                    "within_2_weeks": False
                })
    except Exception as e:
        print(f"Error scraping rotary.org: {e}")

    return scraped


# ==================== 3. AGGREGATE 10 EXCLUSIVE ROTARY.ORG STORIES ====================
def get_top_10_rotary_news():
    """Aggregates strictly from rotary.org, prioritizing <= 2-week articles."""
    now_utc = datetime.now(timezone.utc)

    rss_items = fetch_rotary_org_rss(now_utc)
    scraped_items = scrape_rotary_org_newsroom(now_utc)

    all_candidates = rss_items + scraped_items

    recent_articles = []
    older_articles = []
    seen_urls = set()

    for item in all_candidates:
        clean_link = item["url"]
        if clean_link in seen_urls:
            continue
        seen_urls.add(clean_link)

        if item["within_2_weeks"]:
            recent_articles.append(item)
        else:
            older_articles.append(item)

    recent_articles.sort(key=lambda x: x["published_at"], reverse=True)
    older_articles.sort(key=lambda x: x["published_at"], reverse=True)

    # Combine: Priority to 2-week news, older rotary.org news at bottom
    final_selection = recent_articles[:10]

    if len(final_selection) < 10:
        needed = 10 - len(final_selection)
        final_selection.extend(older_articles[:needed])

    return final_selection


# ==================== 4. PAYLOAD FORMATTING ====================
def format_whatsapp_message(articles, footer_text=""):
    message_lines = [
        "*Rotary & Rotaract Global News Update*\n",
        "Here are top official reports and featured initiatives that you don't want to miss:\n"
    ]

    for idx, art in enumerate(articles, 1):
        message_lines.append(f"{idx}. *{art['title']}*")
        message_lines.append(f"🔗 {art['url']}\n")

    if footer_text.strip():
        message_lines.append(footer_text.strip())

    return "\n".join(message_lines)


# ==================== 5. DISPATCH VIA GREEN API ====================
def send_whatsapp_message(message_text):
    chat_id = f"{RECIPIENT_PHONE_NUMBER}@c.us"
    url = f"https://api.green-api.com/waInstance{GREEN_API_ID_INSTANCE}/sendMessage/{GREEN_API_TOKEN_INSTANCE}"

    payload = {
        "chatId": chat_id,
        "message": message_text
    }
    headers = {'Content-Type': 'application/json'}

    try:
        requests.post(url, json=payload, headers=headers, timeout=8)
    except Exception:
        pass


# ==================== FLASK ENDPOINTS ====================
@app.route('/run-cron', methods=['GET', 'POST'])
def run_cron_job():
    if not all([GREEN_API_ID_INSTANCE, GREEN_API_TOKEN_INSTANCE, RECIPIENT_PHONE_NUMBER]):
        return make_response("OK", 200)

    selected_articles = get_top_10_rotary_news()

    if not selected_articles:
        no_news_msg = "📌 *Rotary & Rotaract Global News Update*\n\nNo major Rotary news reports were found.\n\n" + CUSTOM_FOOTER.strip()
        send_whatsapp_message(no_news_msg)
        return make_response("OK", 200)

    compiled_message = format_whatsapp_message(selected_articles, CUSTOM_FOOTER)
    send_whatsapp_message(compiled_message)

    return make_response("OK", 200)


@app.route('/', methods=['GET'])
def health_check():
    return make_response("OK", 200)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)