import os
from datetime import datetime, timedelta, timezone
from flask import Flask, jsonify
import requests
from dateutil import parser

# ==================== FLASK APPLICATION INSTANCE ====================
app = Flask(__name__)

# ==================== CONFIGURATION (FROM ENVIRONMENT VARIABLES) ====================
NEWS_API_KEY = os.getenv("NEWS_API_KEY", "")
GREEN_API_ID_INSTANCE = os.getenv("GREEN_API_ID_INSTANCE", "")
GREEN_API_TOKEN_INSTANCE = os.getenv("GREEN_API_TOKEN_INSTANCE", "")
RECIPIENT_PHONE_NUMBER = os.getenv("RECIPIENT_PHONE_NUMBER", "")

# Custom endnote formatted as one single paragraph spanning exactly two lines
CUSTOM_FOOTER = os.getenv(
    "CUSTOM_FOOTER",
    "Stay connected with us for daily updates on global community impact, leadership initiatives, and service projects across Rotary and Rotaract networks worldwide.\n— Brought to you by The Editorial Team"
)


# ==================== 1. FETCH & FILTER NEWS ====================
def fetch_rotary_news():
    """Fetches Rotary/Rotaract news with strict organization-focused matching and exclusion filters."""
    now = datetime.now(timezone.utc)
    seven_days_ago = now - timedelta(days=7)

    # Specific phrase query to avoid general hardware/mechanical "rotary" results
    query = '("Rotary Club" OR "Rotaract Club" OR "Rotary International" OR "Rotaract")'

    url = (
        f"https://newsapi.org/v2/everything?"
        f"q={query}&"
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

    # Unwanted terms to eliminate shopping listings and hardware products
    junk_keywords = [
        "tool", "saw", "compressor", "encoder", "switch", 
        "amazon", "ebay", "valve", "engine", "hammer", "drill"
    ]

    for article in articles:
        pub_time_str = article.get("publishedAt")
        if not pub_time_str:
            continue

        try:
            pub_time = parser.parse(pub_time_str)
        except Exception:
            continue

        if pub_time < seven_days_ago:
            continue

        title = article.get("title") or ""
        desc = article.get("description") or ""
        article_url = article.get("url") or ""

        text_content = f"{title} {desc} {article_url}".lower()

        # Reject hardware, e-commerce, or mechanical tool listings
        if any(junk in text_content for junk in junk_keywords):
            continue

        # Strictly verify organizational relevance
        core_org_keywords = ["rotary club", "rotaract club", "rotary international", "rotaract", "paul harris", "end polio"]
        if any(kw in text_content for kw in core_org_keywords):
            # Relevance tiering (1 = core organizational keywords, 2 = general matches)
            relevance_score = 1 if any(kw in text_content for kw in ["rotary club", "rotaract club", "rotary international"]) else 2

            valid_articles.append({
                "title": title.strip(),
                "url": article_url.strip(),
                "published_at": pub_time,
                "relevance": relevance_score,
                "hours_old": (now - pub_time).total_seconds() / 3600.0
            })

    return valid_articles


# ==================== 2. CUSTOM SORTING & EXPANSION ====================
def sort_and_select_articles(articles):
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
def format_whatsapp_message(articles, footer_text=""):
    message = "Today's Biggest Headlines\n\n"
    message += "Here are some of the news reports that you shouldn’t miss this morning:\n\n"

    for idx, art in enumerate(articles, 1):
        # Uses direct URL directly to eliminate middleman landing pages
        message += f"{idx}. {art['title']}\n{art['url']}\n\n"

    if footer_text.strip():
        message += f"{footer_text.strip()}\n"

    return message.strip()


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
        response = requests.post(url, json=payload, headers=headers, timeout=15)
        return response.json()
    except Exception as e:
        return {"error": str(e)}


# ==================== FLASK ENDPOINTS ====================
@app.route('/run-cron', methods=['GET', 'POST'])
def run_cron_job():
    if not all([NEWS_API_KEY, GREEN_API_ID_INSTANCE, GREEN_API_TOKEN_INSTANCE, RECIPIENT_PHONE_NUMBER]):
        return jsonify({
            "status": "error",
            "message": "Missing environment variables on server."
        }), 500

    raw_articles = fetch_rotary_news()
    selected_articles = sort_and_select_articles(raw_articles)

    if not selected_articles:
        return jsonify({"status": "success", "message": "No qualifying Rotary news found within 7 days."}), 200

    compiled_message = format_whatsapp_message(selected_articles, CUSTOM_FOOTER)
    green_api_res = send_whatsapp_message(compiled_message)

    return jsonify({"status": "success", "green_api_response": green_api_res}), 200


@app.route('/', methods=['GET'])
def health_check():
    return "Rotary News Automation Bot is Live!", 200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)