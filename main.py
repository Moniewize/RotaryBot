import os
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, urlunparse
from flask import Flask, make_response
import requests
import feedparser
from dateutil import parser as date_parser

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
    """Strips tracking query parameters to ensure clean, direct links."""
    if not raw_url:
        return ""
    parsed = urlparse(raw_url.strip())
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, '', '', ''))

@app.errorhandler(Exception)
def handle_global_exception(e):
    return make_response("OK", 200)


# ==================== 1. FETCH & PARSE RSS FEEDS WITH DATES ====================
def fetch_rotary_rss_news(now_utc):
    """Parses official Rotary RSS feeds and extracts exact publication dates."""
    rss_urls = [
        "https://www.rotary.org/rss.xml",
        "https://rotarynewsonline.org/feed/"
    ]
    
    articles = []
    
    for feed_url in rss_urls:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries:
                title = getattr(entry, "title", "").strip()
                link = getattr(entry, "link", "").strip()
                
                if not title or not link:
                    continue

                # Extract publication timestamp
                pub_dt = None
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    pub_dt = datetime.fromtimestamp(time.mktime(entry.published_parsed), tz=timezone.utc)
                elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                    pub_dt = datetime.fromtimestamp(time.mktime(entry.updated_parsed), tz=timezone.utc)

                if not pub_dt:
                    pub_dt = now_utc  # Fallback to current time if no date header exists

                hours_old = (now_utc - pub_dt).total_seconds() / 3600.0

                articles.append({
                    "title": title,
                    "url": clean_url(link),
                    "published_at": pub_dt,
                    "hours_old": hours_old,
                    "within_2_weeks": hours_old <= 336.0  # 14 days * 24 hours
                })
        except Exception as e:
            print(f"Error parsing RSS {feed_url}: {e}")

    return articles


# ==================== 2. FETCH NEWSAPI FALLBACK WITH DATES ====================
def fetch_newsapi_articles(now_utc, limit=20):
    """Fetches secondary news via NewsAPI with strict title checks and date parsing."""
    two_weeks_ago = now_utc - timedelta(days=14)
    
    query = '(Rotary OR Rotaract OR "Interact Club" OR PolioPlus OR "Paul Harris")'

    params = {
        "q": query,
        "sortBy": "publishedAt",
        "language": "en",
        "pageSize": 50,
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

    raw_articles = data.get("articles", [])
    valid_articles = []

    required_keywords = [
        "rotary", "rotaract", "interact", "polio", 
        "paul harris", "district governor", "drr", "ryla"
    ]

    junk_keywords = [
        "tool", "saw", "compressor", "encoder", "switch", 
        "engine", "drill", "rig", "piston", "wrestler", "hardware"
    ]

    for article in raw_articles:
        title = (article.get("title") or "").strip()
        raw_url = (article.get("url") or "").strip()
        pub_time_str = article.get("publishedAt")
        title_lower = title.lower()

        if any(junk in title_lower for junk in junk_keywords):
            continue

        if any(req in title_lower for req in required_keywords):
            try:
                pub_dt = date_parser.parse(pub_time_str)
                if pub_dt.tzinfo is None:
                    pub_dt = pub_dt.replace(tzinfo=timezone.utc)
            except Exception:
                pub_dt = now_utc

            hours_old = (now_utc - pub_dt).total_seconds() / 3600.0

            valid_articles.append({
                "title": title,
                "url": clean_url(raw_url),
                "published_at": pub_dt,
                "hours_old": hours_old,
                "within_2_weeks": hours_old <= 336.0
            })

    return valid_articles[:limit]


# ==================== 3. AGGREGATE & ENFORCE 2-WEEK WINDOW ====================
def get_top_10_rotary_news():
    """Selects 10 stories, placing <= 2-week news at the top and older fallback news at the bottom."""
    now_utc = datetime.now(timezone.utc)
    
    # Collect candidates from both RSS and NewsAPI
    all_candidates = fetch_rotary_rss_news(now_utc)
    newsapi_candidates = fetch_newsapi_articles(now_utc, limit=20)
    
    all_candidates.extend(newsapi_candidates)

    recent_articles = []  # <= 14 days
    older_articles = []   # > 14 days
    seen_titles = set()

    for item in all_candidates:
        title_key = item["title"].lower()[:30]
        if title_key in seen_titles:
            continue
        seen_titles.add(title_key)

        if item["within_2_weeks"]:
            recent_articles.append(item)
        else:
            older_articles.append(item)

    # Sort each tier from newest to oldest
    recent_articles.sort(key=lambda x: x["published_at"], reverse=True)
    older_articles.sort(key=lambda x: x["published_at"], reverse=True)

    # Fill up to 10 stories: Priority to 2-week news, fallbacks pushed to bottom
    final_selection = recent_articles[:10]
    
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
        no_news_msg = "📌 *Rotary & Rotaract Global News Update*\n\nNo major Rotary news reports were found in the last two weeks.\n\n" + CUSTOM_FOOTER.strip()
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