import os
import requests
from datetime import datetime, timedelta, timezone
from dateutil import parser

# Replace with your actual NewsAPI key or set it as an environment variable
NEWS_API_KEY = os.getenv("NEWS_API_KEY", "YOUR_NEWS_API_KEY_HERE")

def fetch_rotary_news():
    """Fetches Rotary/Rotaract news with an optimized API query and broad local keyword filtering."""
    now = datetime.now(timezone.utc)
    seven_days_ago = now - timedelta(days=7)

    # Simplified query string passed as URL parameters to avoid NewsAPI internal 500 errors
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
            print(f"NewsAPI error: HTTP {response.status_code} - {response.text}")
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

    # Irrelevant mechanical/commercial keywords to filter out
    junk_keywords = [
        "tool", "saw", "compressor", "encoder", "switch", 
        "amazon", "ebay", "valve", "engine", "hammer", "drill",
        "rotary phone", "rotary engine", "rotary dial"
    ]

    # Broad vocabulary list for local verification and context matching
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

        # Exclude articles containing junk keywords
        if any(junk in text_content for junk in junk_keywords):
            continue

        # Keep articles matching any terms in the extended vocabulary list
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


if __name__ == "__main__":
    results = fetch_rotary_news()
    print(f"Retrieved {len(results)} valid articles:\n")
    for item in results:
        print(f"-[Priority {item['relevance']}] {item['title']}")
        print(f" Link: {item['url']}\n")