import os
from datetime import datetime, timedelta, timezone
from flask import Flask, make_response
import requests
from dateutil import parser
import pyshorteners

# ==================== FLASK APPLICATION INSTANCE ====================
app = Flask(__name__)

# Strip excess HTTP headers to keep payloads lightweight for cron-job.org
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

# Initialize URL shortener
shortener = pyshorteners.Shortener()


def shorten_url(url):
    """Shortens long news links to TinyURL for a clean WhatsApp layout."""
    try:
        return shortener.tinyurl.short(url)
    except Exception:
        return url


@app.errorhandler(Exception)
def handle_global_exception(e):
    return make_response("OK", 200)


# ==================== 1. FETCH & FILTER NEWS ====================
def fetch_rotary_news():
    """Fetches high-relevance Rotary/Rotaract news across the last 14 days (2 weeks)."""
    now = datetime.now(timezone.utc)
    two_weeks_ago = now - timedelta(days=14)

    # Multi-term parenthesized query
    query = '(Rotary OR Rotaract OR "Interact Club" OR PolioPlus OR "Paul Harris")'

    params = {
        "q": query,
        "from": two_weeks_ago.strftime("%Y-%m-%d"),
        "to": now.strftime("%Y-%m-%d"),
        "sortBy": "publishedAt",
        "language": "en",
        "pageSize": 100,  # Max payload size to ensure we get at least 10 valid headlines
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
    seen_titles = set()

    junk_keywords = [
        "tool", "saw", "compressor", "encoder", "switch", 
        "amazon", "ebay", "valve", "engine", "hammer", "drill",
        "rotary phone", "rotary engine", "rotary dial", "rotary rig", "drilling rig",
        "machinery", "automotive", "piston", "internal combustion"
    ]

    rotary_vocab = [
        "rotary", "rotaract", "interact club", "district governor", 
        "district rotaract representative", "drr", "ri president", 
        "rotary international", "rotary district", "rotaract district", 
        "paul harris", "end polio", "polioplus", "polio plus", "rotary foundation", 
        "service above self", "people of action", "rotary club", "rotaract club",
        "rotary project", "rotaract project", "world polio day", "ryla",
        "rotary youth leadership", "rotary youth exchange", "four-way test",
        "4-way test", "discon", "rotary fellowships"
    ]

    for article in articles:
        if not isinstance(article, dict):
            continue

        pub_time_str = article.get("publishedAt")
        if not pub_time_str:
            continue

        try:
            pub_time = parser.parse(pub_time_str)
        except Exception:
            continue

        if pub_time < two_weeks_ago:
            continue

        title = (article.get("title") or "").strip()
        desc = (article.get("description") or "").strip()
        article_url = (article.get("url") or "").strip()

        # Prevent duplicate stories from different RSS feeds
        title_key = title.lower()[:30]
        if title_key in seen_titles:
            continue
        seen_titles.add(title_key)

        text_content = f"{title} {desc} {article_url}".lower()

        # Filter out industrial or mechanical junk
        if any(junk in text_content for junk in junk_keywords):
            continue

        # Check organizational relevance
        matched_terms = [kw for kw in rotary_vocab if kw in text_content]
        if matched_terms:
            high_priority_terms = [
                "rotary club", "rotaract club", "district governor", 
                "district rotaract representative", "ri president", "rotary international",
                "rotary district", "rotaract district", "paul harris", "rotary foundation",
                "polioplus", "polio plus", "rotary project", "rotaract project",
                "world polio day", "ryla"
            ]
            relevance_score = 1 if any(term in matched_terms for term in high_priority_terms) else 2

            if "rotary" in article_url or "rotaract" in article_url:
                relevance_score = 1

            valid_articles.append({
                "title": title,
                "url": article_url,
                "published_at": pub_time,
                "relevance": relevance_score,
                "hours_old": (now - pub_time).total_seconds() / 3600.0
            })

    return valid_articles


# ==================== 2. SELECT TOP 10 ARTICLES ====================
def sort_and_select_articles(articles):
    tier_1, tier_2, tier_3, tier_4 = [], [], [], []

    for art in articles:
        is_recent = art["hours_old"] <= 336  # Within 14 days
        is_relevant = art["relevance"] == 1

        if is_recent and is_relevant:
            tier_1.append(art)
        elif is_recent and not is_relevant:
            tier_2.append(art)
        elif not is_recent and is_relevant:
            tier_3.append(art)
        else:
            tier_4.append(art)

    tier_1.sort(key=lambda x: x["published_at"], reverse=True)
    tier_2.sort(key=lambda x: x["published_at"], reverse=True)
    tier_3.sort(key=lambda x: x["published_at"], reverse=True)
    tier_4.sort(key=lambda x: x["published_at"], reverse=True)

    sorted_list = tier_1 + tier_2 + tier_3 + tier_4
    
    # Enforce exactly 10 headlines (or as many as exist up to 10)
    return sorted_list[:10]


# ==================== 3. PAYLOAD FORMATTING ====================
def format_whatsapp_message(articles, footer_text=""):
    message_lines = [
        "📌 *Rotary & Rotaract Global News Update*\n",
        "Here are the top reports and featured initiatives from the past two weeks:\n"
    ]

    for idx, art in enumerate(articles, 1):
        short_link = shorten_url(art['url'])
        message_lines.append(f"{idx}. *{art['title']}*")
        message_lines.append(f"🔗 {short_link}\n")

    if footer_text.strip():
        message_lines.append(footer_text.strip())

    return "\n".join(message_lines)


# ==================== 4. DISPATCH VIA GREEN API ====================
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
    if not all([NEWS_API_KEY, GREEN_API_ID_INSTANCE, GREEN_API_TOKEN_INSTANCE, RECIPIENT_PHONE_NUMBER]):
        return make_response("OK", 200)

    raw_articles = fetch_rotary_news()
    selected_articles = sort_and_select_articles(raw_articles)

    if not selected_articles:
        no_news_msg = "📌 *Rotary & Rotaract Global News Update*\n\nNo major Rotary or Rotaract news reports were found in the last two weeks.\n\n" + CUSTOM_FOOTER.strip()
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