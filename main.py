import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse, urlunparse
from flask import Flask, make_response
import requests
from bs4 import BeautifulSoup
from dateutil import parser

# ==================== FLASK APPLICATION INSTANCE ====================
app = Flask(__name__)

# Keep HTTP headers minimal for cron-job.org compatibility
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
    """Strips query parameters (e.g., tracking tags) to yield a clean, direct link."""
    if not raw_url:
        return ""
    parsed = urlparse(raw_url.strip())
    # Rebuild URL without query strings or fragments
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, '', '', ''))


@app.errorhandler(Exception)
def handle_global_exception(e):
    return make_response("OK", 200)


# ==================== 1. DIRECT SCRAPER FOR ROTARY.ORG ====================
def scrape_official_rotary_site():
    """Scrapes official news directly from Rotary International's portal."""
    url = "https://www.rotary.org/en/news-and-stories"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    scraped_articles = []

    try:
        res = requests.get(url, headers=headers, timeout=8)
        if res.status_code != 200:
            return []

        soup = BeautifulSoup(res.text, "html.parser")

        # Locate all article cards and anchor tags containing links
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            
            # Select relevant article paths
            if "/en/articles/" in href or "/en/news-and-stories" in href:
                title = a_tag.get_text(strip=True)
                
                # Filter out navigation links, empty titles, and short buttons
                if not title or len(title) < 20 or title.lower() in ["read more", "news and stories", "all news and stories"]:
                    continue

                full_url = urljoin("https://www.rotary.org", href)
                direct_url = clean_url(full_url)

                scraped_articles.append({
                    "title": title,
                    "url": direct_url,
                    "source": "Rotary International (Official)"
                })

    except Exception as e:
        print(f"Error scraping rotary.org: {e}")

    return scraped_articles


# ==================== 2. FALLBACK FETCH FROM NEWSAPI ====================
def fetch_newsapi_articles(limit=10):
    """Fetches additional news from global news providers via NewsAPI as backup/supplement."""
    now = datetime.now(timezone.utc)
    two_weeks_ago = now - timedelta(days=14)

    query = '(Rotary OR Rotaract OR "Interact Club" OR PolioPlus OR "Paul Harris")'

    params = {
        "q": query,
        "from": two_weeks_ago.strftime("%Y-%m-%d"),
        "to": now.strftime("%Y-%m-%d"),
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

    articles = data.get("articles", [])
    valid_articles = []
    junk_keywords = ["tool", "saw", "compressor", "encoder", "engine", "drill", "rig", "piston"]

    for article in articles:
        title = (article.get("title") or "").strip()
        raw_url = (article.get("url") or "").strip()
        
        if not title or not raw_url or any(junk in title.lower() for junk in junk_keywords):
            continue

        valid_articles.append({
            "title": title,
            "url": clean_url(raw_url),
            "source": "Global News"
        })

    return valid_articles[:limit]


# ==================== 3. COMBINE & DEDUPLICATE RESULTS ====================
def get_combined_rotary_news():
    """Combines direct official site scrapes with fallback global news to guarantee 10 stories."""
    official_news = scrape_official_rotary_site()
    
    final_list = []
    seen_titles = set()

    # Priority 1: Add official rotary.org articles
    for item in official_news:
        title_key = item["title"].lower()[:30]
        if title_key not in seen_titles:
            seen_titles.add(title_key)
            final_list.append(item)

    # Priority 2: Fill remaining slots up to 10 with NewsAPI results
    if len(final_list) < 10:
        needed = 10 - len(final_list)
        fallback_news = fetch_newsapi_articles(limit=needed * 2)
        
        for item in fallback_news:
            title_key = item["title"].lower()[:30]
            if title_key not in seen_titles:
                seen_titles.add(title_key)
                final_list.append(item)
                if len(final_list) == 10:
                    break

    return final_list[:10]


# ==================== 4. PAYLOAD FORMATTING ====================
def format_whatsapp_message(articles, footer_text=""):
    message_lines = [
        "📌 *Rotary & Rotaract Global News Update*\n",
        "Here are the top reports and featured initiatives directly from Rotary networks:\n"
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

    selected_articles = get_combined_rotary_news()

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