import os
from datetime import datetime, timedelta, timezone
from flask import Flask, jsonify
import requests
from dateutil import parser
import pyshorteners

# ==================== FLASK APPLICATION INSTANCE ====================
# Must be declared at the module level for Gunicorn (gunicorn main:app)
app = Flask(__name__)

# ==================== CONFIGURATION (FROM ENVIRONMENT VARIABLES) ====================
NEWS_API_KEY = os.getenv("NEWS_API_KEY", "")
GREEN_API_ID_INSTANCE = os.getenv("GREEN_API_ID_INSTANCE", "")
GREEN_API_TOKEN_INSTANCE = os.getenv("GREEN_API_TOKEN_INSTANCE", "")
RECIPIENT_PHONE_NUMBER = os.getenv("RECIPIENT_PHONE_NUMBER", "")

CUSTOM_PARAGRAPH_1 = os.getenv(
    "CUSTOM_PARAGRAPH_1",
    "Stay connected with us for daily updates on global community impact, leadership initiatives, and service projects across Rotary and Rotaract networks worldwide."
)
CUSTOM_PARAGRAPH_2 = os.getenv(
    "CUSTOM_PARAGRAPH_2",
    "— Brought to you by The Editorial Team"
)


# ==================== HELPER: URL SHORTENER ====================
def shorten_url(long_url):
    """Shortens a URL using TinyURL via pyshorteners."""
    if not long_url:
        return ""
    try:
        s = pyshorteners.Shortener()
        return s.tinyurl.short(long_url)
    except Exception as e:
        print(f"Failed to shorten URL '{long_url}': {e}")
        return long_url


# ==================== 1. FETCH & FILTER NEWS ====================
def fetch_rotary_news():
    """Fetches Rotary/Rotaract news and applies strictly enforced 7-day cutoff."""
    now = datetime.now(timezone.utc)
    seven_days_ago = now - timedelta(days=7)

    url = (
        f"https://newsapi.org/v2/everything?"
        f"q=(Rotary OR Rotaract)&"
        f"from={seven_days_ago.strftime('%Y-%m-%d')}&"
        f"sortBy=publishedAt&"
        f"language=en&"
        f"apiKey={NEWS_API_KEY}"
    )

    try:
        response = requests.get(url, timeout=15)
        data = response.json()
    except Exception as e:
        print(f"Error making HTTP request to NewsAPI: {e}")
        return []

    if data.get("status") != "ok":
        print(f"Error fetching news from API: {data.get('message')}")
        return []

    articles = data.get("articles", [])
    valid_articles = []

    for article in articles:
        pub_time_str = article.get("publishedAt")
        if not pub_time_str:
            continue

        try:
            pub_time = parser.parse(pub_time_str)
        except Exception:
            continue

        # Enforce strict 7-day age limit
        if pub_time < seven_days_ago:
            continue

        title = article.get("title") or ""
        desc = article.get("description") or ""

        # Relevance filtering
        text_content = f"{title} {desc}".lower()
        if "rotary" in text_content or "rotaract" in text_content:
            core_keywords = ["rotary club", "rotaract club", "rotary international", "polio", "paul harris"]
            relevance_score = 1 if any(kw in text_content for kw in core_keywords) else 2

            valid_articles.append({
                "title": title.strip(),
                "url": article.get("url"),
                "published_at": pub_time,
                "relevance": relevance_score,
                "hours_old": (now - pub_time).total_seconds() / 3600.0
            })

    return valid_articles


# ==================== 2. CUSTOM SORTING & EXPANSION ====================
def sort_and_select_articles(articles):
    """
    Sort Order Rules:
    1. MOST RECENT AND MOST RELEVANT FIRST (Recent <= 48h AND Relevance == 1)
    2. MORE RECENT AND LESS RELEVANT SECOND (Recent <= 48h AND Relevance == 2)
    3. MORE RELEVANT AND LESS RECENT THIRD (Recent > 48h AND Relevance == 1)
    """
    tier_1, tier_2, tier_3, tier_4 = [], [], [], []

    for art in articles:
        is_recent = art["hours_old"] <= 48
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
    target_count = 12 if len(sorted_list) >= 12 else min(10, len(sorted_list))

    return sorted_list[:target_count]


# ==================== 3. PAYLOAD FORMATTING ====================
def format_whatsapp_message(articles, p1="", p2=""):
    """Formats payload matching template with shortened links and two footer paragraphs."""
    message = "Today's Biggest Headlines\n\n"
    message += "Here are some of the news reports that you shouldn’t miss this morning:\n\n"

    for idx, art in enumerate(articles, 1):
        short_link = shorten_url(art['url'])
        message += f"{idx}. {art['title']}\n{short_link}\n\n"

    if p1.strip():
        message += f"{p1.strip()}\n\n"

    if p2.strip():
        message += f"{p2.strip()}\n"

    return message.strip()


# ==================== 4. DISPATCH VIA GREEN API ====================
def send_whatsapp_message(message_text):
    """Sends single compiled chat message via GREEN API."""
    chat_id = f"{RECIPIENT_PHONE_NUMBER}@c.us"
    url = f"https://api.green-api.com/waInstance{GREEN_API_ID_INSTANCE}/sendMessage/{GREEN_API_TOKEN_INSTANCE}"

    payload = {
        "chatId": chat_id,
        "message": message_text
    }
    headers = {'Content-Type': 'application/json'}

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=15)
        return response.json()
    except Exception as e:
        return {"error": str(e)}


# ==================== FLASK ENDPOINTS ====================
@app.route('/run-cron', methods=['GET', 'POST'])
def run_cron_job():
    """Webhook endpoint triggered by cron-job.org."""
    if not all([NEWS_API_KEY, GREEN_API_ID_INSTANCE, GREEN_API_TOKEN_INSTANCE, RECIPIENT_PHONE_NUMBER]):
        return jsonify({
            "status": "error",
            "message": "Missing environment variables on server."
        }), 500

    raw_articles = fetch_rotary_news()
    selected_articles = sort_and_select_articles(raw_articles)

    if not selected_articles:
        return jsonify({"status": "success", "message": "No qualifying Rotary news found within 7 days."}), 200

    compiled_message = format_whatsapp_message(selected_articles, CUSTOM_PARAGRAPH_1, CUSTOM_PARAGRAPH_2)
    green_api_res = send_whatsapp_message(compiled_message)

    return jsonify({"status": "success", "green_api_response": green_api_res}), 200


@app.route('/', methods=['GET'])
def health_check():
    """Root health check endpoint."""
    return "Rotary News Automation Bot is Live!", 200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)