import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, urlunparse
from flask import Flask, make_response
import requests
import feedparser
from dateutil import parser

# ==================== FLASK APPLICATION INSTANCE ====================
app = Flask(__name__)

@app.after_request
def minimize_headers(response):
    response.headers.clear()
    response.headers['Content-Type'] = 'text/plain'
    return response

# ==================== CONFIGURATION ====================
NEWS_API_KEY = os.getenv("NEWS_API_KEY", "")
GREEN_API_ID_INSTANCE = os.getenv("GREEN_API_ID_INSTANCE", "")
GREEN_API_TOKEN_INSTANCE = os.getenv("GREEN_API_TOKEN_INSTANCE", "")
RECIPIENT_PHONE_NUMBER = os.getenv("RECIPIENT_PHONE_NUMBER", "")

CUSTOM_FOOTER = os.getenv(
    "CUSTOM_FOOTER",
    "Stay connected with us for daily updates on global community impact, leadership initiatives, and service projects across Rotary and Rotaract networks worldwide.\n— Brought to you by The Editorial Team"
)

def clean_url(raw_url):
    """Strips query/tracking parameters to return a clean, direct URL."""
    if not raw_url:
        return ""
    parsed = urlparse(raw_url.strip())
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, '', '', ''))

@app.errorhandler(Exception)
def handle_global_exception(e):
    return make_response("OK", 200)


# ==================== 1. FETCH FROM OFFICIAL ROTARY RSS FEEDS ====================
def fetch_rotary_rss_news():
    """Parses official RSS feeds to ensure 100% Rotary and Rotaract content."""
    rss_urls = [
        "https://rotarynewsonline.org/feed/",
        "https://blog.rotary.org/feed/"
    ]
    
    scraped_articles = []
    
    for feed_url in rss_urls:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries:
                title = getattr(entry, "title", "").strip()
                link = getattr(entry, "link", "").strip()
                
                if title and link:
                    scraped_articles.append({
                        "title": title,
                        "url": clean_url(link)
                    })
        except Exception as e:
            print(f"Error reading RSS feed {feed_url}: {e}")

    return scraped_articles


# ==================== 2. FALLBACK VIA STRICT NEWSAPI ====================
def fetch_newsapi_articles(needed_count=10):
    """Fetches secondary news via NewsAPI with strict organizational filtering."""
    now = datetime.now(timezone.utc)
    two_weeks_ago = now - timedelta(days=14)

    # Multi-term query
    query = '(Rotary OR Rotaract OR "Interact Club" OR PolioPlus OR "Paul Harris")'

    params = {
        "q": query,
        "from": two_weeks_ago.strftime("%Y-%m-%d"),
        "to": now.strftime("%Y-%m-%d"),
        "sortBy": "publishedAt",
        "language": "en",
        "pageSize": 60,
        "apiKey": NEWS_API_KEY
    }

    try:
        response = requests.get("https://newsapi.org/v2/everything", params=params, timeout=8)
        if response.status_code != 200:
            return []
        data = response.json()
    except Exception:
        return []

    if not isinstance(data, dict) or data.get("status") != "ok":
        return []

    articles = data.get("articles", [])
    valid_articles = []

    # Mandatory organizational keywords (headline MUST contain one)
    required_keywords = [
        "rotary", "rotaract", "interact", "polio", 
        "paul harris", "district governor", "drr", "ryla"
    ]

    # Explicit junk exclusion list
    junk_keywords = [
        "tool", "saw", "compressor", "encoder", "switch", 
        "engine", "drill", "rig", "piston", "wrestler", "hardware"
    ]

    for article in articles:
        title = (article.get("title") or "").strip()
        raw_url = (article.get("url") or "").strip()
        title_lower = title.lower()

        # Reject non-Rotary mechanical items
        if any(junk in title_lower for junk in junk_keywords):
            continue

        # STRICT CHECK: Title MUST contain a Rotary/Rotaract keyword
        if any(req in title_lower for req in required_keywords):
            valid_articles.append({
                "title": title,
                "url": clean_url(raw_url)
            })

    return valid_articles[:needed_count]


# ==================== 3. AGGREGATE 10 DISTINCT STORIES ====================
def get_top_10_rotary_news():
    """Aggregates RSS feeds and strict NewsAPI items to yield exactly 10 stories."""
    combined = []
    seen_titles = set()

    # Priority 1: RSS Feeds
    rss_news = fetch_rotary_rss_news()
    for item in rss_news:
        title_key = item["title"].lower()[:30]
        if title_key not in seen_titles:
            seen_titles.add(title_key)
            combined.append(item)

    # Priority 2: Strict NewsAPI fallback if RSS yields under 10
    if len(combined) < 10:
        needed = 10 - len(combined)
        backup_news = fetch_newsapi_articles(needed_count=needed * 2)
        
        for item in backup_news:
            title_key = item["title"].lower()[:30]
            if title_key not in seen_titles:
                seen_titles.add(title_key)
                combined.append(item)
                if len(combined) == 10:
                    break

    return combined[:10]


# ==================== 4. PAYLOAD FORMATTING ====================
def format_whatsapp_message(articles, footer_text=""):
    message_lines = [
        "📌 *Rotary & Rotaract Global News Update*\n",
        "Here are today's top reports and featured initiatives:\n"
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