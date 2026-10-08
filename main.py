import os
import time
import requests
from datetime import datetime, timedelta, timezone
from dateutil import parser
import pyshorteners

# ==========================================
# CONFIGURATION & ENVIRONMENT VARIABLES
# ==========================================
NEWS_API_KEY = os.getenv("NEWS_API_KEY", "YOUR_NEWS_API_KEY_HERE")
GREEN_API_INSTANCE_ID = os.getenv("GREEN_API_INSTANCE_ID", "YOUR_INSTANCE_ID_HERE")
GREEN_API_TOKEN = os.getenv("GREEN_API_TOKEN", "YOUR_API_TOKEN_HERE")
CHAT_ID = os.getenv("CHAT_ID", "YOUR_CHAT_ID_HERE")  # e.g., "120363xxxxxxxxxx@g.us" or "234xxxxxxxxxx@c.us"

# URL Shortener setup
shortener = pyshorteners.Shortener()


def shorten_url(url):
    """Shortens a URL using TinyURL, falling back to original if it fails."""
    try:
        return shortener.tinyurl.short(url)
    except Exception as e:
        print(f"URL shortener error: {e}")
        return url


def fetch_rotary_news():
    """Fetches Rotary/Rotaract news and filters using extended context vocabulary."""
    now = datetime.now(timezone.utc)
    seven_days_ago = now - timedelta(days=7)

    # Clean query passed as parameters to prevent NewsAPI internal 500 errors
    query = 'Rotary OR Rotaract OR "Interact Club" OR "PolioPlus" OR "Paul Harris"'

    params = {
        "q": query,
        "from": seven_days_ago.strftime("%Y-%m-%d"),
        "sortBy": "publishedAt",
        "language": "en",
        "apiKey": NEWS_API_KEY
    }

    try:
        response = requests.get("https://newsapi.org/v2/everything", params=params, timeout=15)
        if response.status_code != 200:
            print(f"Error fetching news: HTTP {response.status_code} - {response.text}")
            return []
            
        data = response.json()
    except Exception as e:
        print(f"Error making HTTP request to NewsAPI: {e}")
        return []

    if data.get("status") != "ok":
        print(f"Error fetching news from API: {data.get('message')}")
        return []

    articles = data.get("articles", [])
    valid_articles = []

    # Irrelevant mechanical/commercial terms
    junk_keywords = [
        "tool", "saw", "compressor", "encoder", "switch", 
        "amazon", "ebay", "valve", "engine", "hammer", "drill",
        "rotary phone", "rotary engine", "rotary dial"
    ]

    # Extended Rotaract Handbook 2024–2025 context vocabulary
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

        # Reject mechanical junk
        if any(junk in text_content for junk in junk_keywords):
            continue

        # Keep relevant Rotary content
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

            valid_articles.append({
                "title": title.strip(),
                "url": article_url.strip(),
                "published_at": pub_time,
                "relevance": relevance_score,
                "hours_old": (now - pub_time).total_seconds() / 3600.0
            })

    return valid_articles


def send_whatsapp_message(message):
    """Sends a text message using Green API."""
    url = f"https://api.green-api.com/waInstance{GREEN_API_INSTANCE_ID}/sendMessage/{GREEN_API_TOKEN}"
    payload = {
        "chatId": CHAT_ID,
        "message": message
    }
    headers = {"Content-Type": "application/json"}

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=15)
        if response.status_code == 200:
            print("Successfully sent WhatsApp message.")
        else:
            print(f"Failed to send WhatsApp message: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"Error sending WhatsApp message: {e}")


def main():
    """Main execution flow."""
    print("Fetching Rotary & Rotaract news...")
    articles = fetch_rotary_news()

    if not articles:
        print("No new valid Rotary articles found.")
        return

    # Sort articles by priority relevance, then by newest date
    articles.sort(key=lambda x: (x["relevance"], -x["published_at"].timestamp()))

    # Select top articles to dispatch
    top_articles = articles[:5]

    message_lines = ["📌 *Rotary & Rotaract Global News Update*\n"]

    for idx, item in enumerate(top_articles, 1):
        short_link = shorten_url(item["url"])
        message_lines.append(f"{idx}. *{item['title']}*")
        message_lines.append(f"🔗 {short_link}\n")

    full_message = "\n".join(message_lines)
    
    print("Dispatching update via Green API...")
    send_whatsapp_message(full_message)


if __name__ == "__main__":
    main()