import os
import sys
import json
import argparse
import ssl
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
import warnings
import datetime
import email.utils
import re
from dotenv import load_dotenv

# Optional external imports
try:
    import google.generativeai as genai
except ImportError:
    genai = None

try:
    from requests_oauthlib import OAuth1Session
except ImportError:
    OAuth1Session = None

warnings.filterwarnings("ignore")
load_dotenv(override=True)

INSTAPAPER_ADD_URL = "https://www.instapaper.com/api/add"

# ==========================================
# 1. EXTENDED AUSTRALIAN RSS FEEDS
# ==========================================

FEEDS_POLITICAL_RISK = [
    "https://www.abc.net.au/news/feed/51120/rss.xml",
    "https://www.sbs.com.au/news/feed",
    "https://www.theage.com.au/rss/politics/federal.xml",
    "https://www.theage.com.au/rss/national/victoria.xml",
    "https://www.crikey.com.au/feed"
]

FEEDS_AI_POLICY = [
    "https://www.theage.com.au/rss/technology.xml",
    "https://ausretrogamer.com/feed/",
    "https://press-start.com.au/feed",
    "https://news.google.com/rss/search?q=Nintendo+OR+%22retrotech%22+OR+%22retro+gaming%22&hl=en-AU&gl=AU&ceid=AU:en"
]

FEEDS_AI_INDUSTRY = [
    "https://www.theage.com.au/rss/sport/afl.xml",
    "https://www.foxsports.com.au/content-hosts/afl/rss",
    "https://www.theage.com.au/rss/culture.xml",
    "https://checkpointgaming.net/news/feed",
    "https://news.google.com/rss/search?q=%22Australian+radio%22+OR+%22community+radio%22+OR+%22Essendon%22+OR+%22Albury%22&hl=en-AU&gl=AU&ceid=AU:en"
]

def is_within_24_hours(pub_date_str):
    if not pub_date_str:
        return True
    try:
        dt = email.utils.parsedate_to_datetime(pub_date_str)
        now = datetime.datetime.now(datetime.timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return (now - dt).total_seconds() <= 24 * 3600
    except Exception:
        return True

def fetch_rss_items(url):
    context = ssl._create_unverified_context()
    headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'}
    req = urllib.request.Request(url, headers=headers)
    items = []
    try:
        with urllib.request.urlopen(req, context=context, timeout=10) as response:
            xml_data = response.read()
            root = ET.fromstring(xml_data)
            
            for item in root.findall('.//item'):
                title = item.find('title')
                link = item.find('link')
                desc = item.find('description')
                pub_date = item.find('pubDate')
                
                title_text = title.text if title is not None and title.text else ""
                link_text = link.text if link is not None and link.text else ""
                desc_text = desc.text if desc is not None and desc.text else ""
                pub_date_text = pub_date.text if pub_date is not None and pub_date.text else ""
                
                if desc_text:
                    desc_text = re.sub('<[^<]+?>', '', desc_text).strip()

                if is_within_24_hours(pub_date_text):
                    items.append({
                        'title': title_text.strip(),
                        'link': link_text.strip(),
                        'description': desc_text,
                        'pub_date': pub_date_text
                    })
    except Exception as e:
        print(f"Error fetching feed {url}: {e}", file=sys.stderr)
    return items

def fetch_full_content(url):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    try:
        req = urllib.request.Request(url, headers=headers)
        context = ssl._create_unverified_context()
        with urllib.request.urlopen(req, context=context, timeout=10) as resp:
            html_content = resp.read().decode('utf-8', errors='ignore')
            paragraphs = re.findall(r'<p[^>]*>(.*?)</p>', html_content, re.DOTALL)
            clean_paragraphs = [re.sub(r'<[^<]+?>', '', p).strip() for p in paragraphs if len(re.sub(r'<[^<]+?>', '', p).strip()) > 30]
            if clean_paragraphs:
                return "\n\n".join(clean_paragraphs)
    except Exception as e:
        print(f"Error fetching content for {url}: {e}", file=sys.stderr)
    return None

# ==========================================
# 2. GEMINI SCORING & INTEREST PROMPTS
# ==========================================

USER_INTEREST_TOPICS = [
    "AFL (Australian Football League), Essendon Football Club, and Victorian sports news",
    "Australian Federal Politics, Victorian State Politics, and public policy in Victoria",
    "Australian Media, Commercial Radio, Community Radio, and broadcasting regulation",
    "American Politics, US elections, and geopolitical news",
    "Melbourne Gigs and Music"
    "Australian Comedy, satire, and local entertainment culture",
    "Retrotech, retro gaming, Nintendo, and historical computing technology",
    "Local regional Victorian news (including Albury-Wodonga and border communities)"
]

formatted_interests = "\n".join([f"- {topic}" for topic in USER_INTEREST_TOPICS])

PROMPT_PERSONALIZED_CURATION = f"""You are an executive editor scoring daily news articles for a user with specific interest domains.

Evaluate candidate articles by assigning a score from 1 to 100 and eliminating duplicate coverage.
scores_data = json.loads(text_resp)
        score_map = {item["index"]: item["score"] for item in scores_data}

        for idx, art in enumerate(articles):
            art["score"] = score_map.get(idx, 0)

        articles.sort(key=lambda x: x.get("score", 0), reverse=True)
    except Exception as e:
        print(f"⚠️ Gemini scoring error, falling back to keyword logic: {e}", file=sys.stderr)

    return articles

# ==========================================
# 3. BACKUP KEYWORD SCORING (FAILSAFE)
# ==========================================

def calculate_fallback_score(title, description):
    text = f"{title} {description}".lower()
    score = 50
    
    keywords = [
        r'\bafl\b', r'\bessendon\b', r'\bvictoria\b', r'\bvictorian\b', 
        r'\balbury\b', r'\bradio\b', r'\bcommunity radio\b', r'\bnintendo\b', 
        r'\bretrotech\b', r'\bretro gaming\b', r'\baustralian politics\b',
        r'\bcomedy\b', r'\bmedia\b', r'\bus politics\b'
    ]
    
    for kw in keywords:
        if re.search(kw, text):
            score += 15

    negative_keywords = [r'\bsoccer\b', r'\bcricket\b', r'\bcelebrity gossip\b']
    for nkw in negative_keywords:
        if re.search(nkw, text):
            score -= 20

    return max(0, min(100, score))

# ==========================================
# 4. DIVERSITY CAP & INSTAPAPER SYNC
# ==========================================

def apply_diversity_cap(articles, max_per_domain=2):
    ...
    
# ==========================================
# 5. MAIN EXECUTION PIPELINE
# ==========================================

def main():
    parser = argparse.ArgumentParser(description="Sync curated news feeds to Instapaper.")
    parser.add_argument("--dry-run", action="store_true", help="Run scoring without sending to Instapaper")
    parser.add_argument("--auto", action="store_true", help="Run in automated mode via GitHub Actions")
    args, unknown = parser.parse_known_args()

    negative_keywords = [r'\bsoccer\b', r'\bcricket\b', r'\bcelebrity gossip\b']
    for nkw in negative_keywords:
        if re.search(nkw, text):
            score -= 20

    return max(0, min(100, score))

# ==========================================
# 4. DIVERSITY CAP & INSTAPAPER SYNC
# ==========================================

def apply_diversity_cap(articles, max_per_domain=2):
    domain_counts = {}
    capped_list = []
    
    for art in articles:
        domain = urllib.parse.urlparse(art['link']).netloc
        count = domain_counts.get(domain, 0)
        if count < max_per_domain:
            capped_list.append(art)
            domain_counts[domain] = count + 1
            
    return capped_list

def post_to_instapaper(url, title="", summary=""):
    username = os.getenv("INSTAPAPER_USERNAME")
    password = os.getenv("INSTAPAPER_PASSWORD")
    
    if not username or not password:
        print("Instapaper credentials missing. Skipping post.", file=sys.stderr)
        return False

    payload = {'username': username, 'password': password, 'url': url}
    if title:
        payload['title'] = title
    if summary:
        payload['selection'] = summary

    try:
        data = urllib.parse.urlencode(payload).encode('utf-8')
        req = urllib.request.Request(INSTAPAPER_ADD_URL, data=data)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 201 or resp.status == 200
    except Exception as e:
        print(f"Error syncing to Instapaper: {e}", file=sys.stderr)
        return False

# ==========================================
# 5. MAIN EXECUTION PIPELINE
# ==========================================

def main():
    parser = argparse.ArgumentParser(description="Sync curated news feeds to Instapaper.")
    parser.add_argument("--dry-run", action="store_true", help="Run scoring without sending to Instapaper")
    args = parser.parse_args()

    print("🚀 Fetching Australian news & personal interest feeds...")
    all_raw_articles = []
    
    for feed_list in [FEEDS_POLITICAL_RISK, FEEDS_AI_POLICY, FEEDS_AI_INDUSTRY]:
        for url in feed_list:
            all_raw_articles.extend(fetch_rss_items(url))

    print(f"📥 Fetched {len(all_raw_articles)} total candidate items from last 24h.")

    if not all_raw_articles:
        print("No recent articles found.")
        return

    # Score via Gemini
    scored_articles = gemini_score_articles(all_raw_articles)

    # Fallback score if Gemini omitted
    for art in scored_articles:
        if "score" not in art:
            art["score"] = calculate_fallback_score(art["title"], art.get("description", ""))

    # Sort and filter top candidates
    scored_articles.sort(key=lambda x: x.get("score", 0), reverse=True)
    filtered_articles = [a for a in scored_articles if a.get("score", 0) >= 60]
    
    # Enforce domain diversity cap
    final_selection = apply_diversity_cap(filtered_articles, max_per_domain=2)[:10]

    print(f"\n🎯 Selected {len(final_selection)} top curated articles:")
    for idx, art in enumerate(final_selection, 1):
        print(f"{idx}. [{art.get('score', 0)} pts] {art['title']} ({art['link']})")

    if args.dry-run:
        print("\n🧪 Dry run complete. No articles sent to Instapaper.")
        return

    print("\n📤 Syncing selected articles to Instapaper...")
    success_count = 0
    for art in final_selection:
        if post_to_instapaper(art['link'], title=art['title'], summary=art.get('description', '')):
            success_count += 1

    print(f"✅ Successfully synced {success_count}/{len(final_selection)} articles to Instapaper!")

if __name__ == "__main__":
    main()
