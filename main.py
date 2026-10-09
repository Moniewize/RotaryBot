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
    "Stay connected with us for daily updates on global community impact, leadership initiatives, and service projects across Rotary and Rotaract networks worldwide.\n— Brought to you by The Editorial Team"
)

def clean_url(raw_url):
    """Strips tracking query parameters to ensure clean, direct links."""
    if not raw_url:
        return ""
    parsed = urlparse(raw_url.strip())
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, '', '', ''))

@app.errorhandler(Exception)
def handle_global_exception(e):
    return make_response("OK", 200)


# ==================== 1. PRIMARY SOURCE: ROTARY.ORG ====================
def fetch_primary_rotary_org(now_utc):
    """Parses primary official rotary.org RSS feed and direct newsroom scraper."""
    articles = []

    # 1A. RSS Feed from rotary.org
    try:
        feed = feedparser.parse("https://www.rotary.org/rss.xml")
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
        print(f"Error parsing primary RSS: {e}")

    # 1B. Direct Web Scraper for rotary.org/en/news-and-stories
    try:
        url = "https://www.rotary.org/en/news-and-stories"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        res = requests.get(url, headers=headers, timeout=8)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            for a_tag in soup.find_all("a", href=True):
                href = a_tag["href"]
                if "/en/articles/" in href:
                    title = a_tag.get_text(strip=True)
                    if not title or len(title) < 15 or title.lower() in ["read more", "news and stories"]:
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
        print(f"Error scraping primary rotary.org: {e}")

    return articles


# ==================== 2. SECONDARY & REGIONAL SOURCES ====================
def fetch_secondary_rotary_feeds(now_utc):
    """Parses Intercountry Committees, Rotary News Online, and Official Rotary Blogs."""
    secondary_feeds = [
        ("Rotary Intercountry Committees", "https://rotary-icc.org/feed/", 2),
        ("Rotary News Online", "https://rotarynewsonline.org/feed/", 3),
        ("Rotary Voices / Blog", "https://blog.rotary.org/feed/", 4)
    ]

    articles = []

    for source_name, feed_url, priority in secondary_feeds:
        try:
            feed = feedparser.parse(feed_url)
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
            print(f"Error parsing feed {source_name}: {e}")

    return articles


# ==================== 3. AGGREGATION & HIERARCHICAL SELECTION ====================
def get_top_10_rotary_news():
    """Builds top 10 articles strictly adhering to site hierarchy and date recency."""
    now_utc = datetime.now(timezone.utc)

    # Fetch primary site news first
    primary_candidates = fetch_primary_rotary_org(now_utc)
    
    # Fetch secondary regional feeds
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

    # Sort recent articles: Primary site priority first, then publication date descending
    recent_articles.sort(key=lambda x: (x["priority"], -x["published_at"].timestamp()))
    older_articles.sort(key=lambda x: (x["priority"], -x["published_at"].timestamp()))

    # Select top 10: Prioritize <= 2-week news first
    final_selection = recent_articles[:10]

    # Fill remaining slots with older official articles if recent items are under 10
    if len(final_selection) < 10:
        needed = 10 - len(final_selection)
        final_selection.extend(older_articles[:needed])

    return final_selection


# ==================== 4. PAYLOAD FORMATTING ====================
def format_whatsapp_message(articles, footer_text=""):
    message_lines = [
        "📌 *Rotary & Rotaract Global News Update*\n",
        "Here are today's top official reports and featured projects:\n"
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