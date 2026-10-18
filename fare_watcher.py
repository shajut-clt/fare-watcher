#!/usr/bin/env python3
"""
Fare watcher: polls deal feeds, flags mistake/exceptional fares, alerts you.

Setup:
  pip install feedparser requests
  export TELEGRAM_BOT_TOKEN=...   # create a bot via @BotFather
  export TELEGRAM_CHAT_ID=...     # your chat id (message @userinfobot)
Run every 15 min with cron:
  */15 * * * * /usr/bin/python3 /path/to/fare_watcher.py
"""
import json, os, re, sqlite3, time
import feedparser, requests

# ---------- CONFIG (edit these) ----------
FEEDS = {
    "Secret Flying": "https://www.secretflying.com/feed/",
    "Fly4Free": "https://www.fly4free.com/feed/",
    "r/flightdeals": "https://www.reddit.com/r/flightdeals/new/.rss",
}  # verify each URL still works; add or remove feeds freely

# Only alert on deals mentioning these places (empty list = alert on everything)
HOME_KEYWORDS = ["india", "chennai", "mumbai", "delhi", "bengaluru", "hyderabad", "kochi"]

# Words that signal a mistake fare
MISTAKE_WORDS = ["error fare", "mistake fare", "mistake", "glitch", "pricing error", "error"]

# Optional hard price cap per currency symbol (alert if price <= cap)
PRICE_CAPS = {"$": 400, "£": 350, "€": 380, "₹": 30000}

DB = os.path.join(os.path.dirname(__file__), "fares.db")
OUT_JSON = os.path.join(os.path.dirname(__file__), "deals.json")  # dashboard reads this
UA = {"User-Agent": "fare-watcher/0.1 (personal use)"}
# -----------------------------------------

PRICE_RE = re.compile(r"(?P<cur>[$£€₹])\s?(?P<amt>[\d,]+(?:\.\d+)?)")


def db():
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS deals(
        id TEXT PRIMARY KEY, source TEXT, title TEXT, link TEXT,
        price REAL, currency TEXT, is_mistake INTEGER,
        found_at REAL, alerted INTEGER DEFAULT 0)""")
    return con


def parse_price(text):
    m = PRICE_RE.search(text)
    if not m:
        return None, None
    return float(m["amt"].replace(",", "")), m["cur"]


def classify(title):
    t = title.lower()
    mistake = any(w in t for w in MISTAKE_WORDS)
    price, cur = parse_price(title)
    cheap = price is not None and cur in PRICE_CAPS and price <= PRICE_CAPS[cur]
    relevant = not HOME_KEYWORDS or any(k in t for k in HOME_KEYWORDS)
    return mistake, cheap, relevant, price, cur


def alert(text):
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not (token and chat):
        print("[no telegram configured]", text)
        return False
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat, "text": text}, timeout=15)
    return r.ok


def poll():
    con = db()
    for source, url in FEEDS.items():
        try:
            resp = requests.get(url, headers=UA, timeout=20)
            feed = feedparser.parse(resp.content)
        except Exception as e:
            print(f"{source}: fetch failed: {e}")
            continue
        for e in feed.entries:
            uid = e.get("id") or e.get("link")
            if not uid or con.execute("SELECT 1 FROM deals WHERE id=?", (uid,)).fetchone():
                continue
            title = e.get("title", "")
            mistake, cheap, relevant, price, cur = classify(title)
            if not (mistake or cheap):
                continue
            if not relevant:
                continue
            con.execute("INSERT INTO deals VALUES(?,?,?,?,?,?,?,?,0)",
                        (uid, source, title, e.get("link"), price, cur,
                         int(mistake), time.time()))
            tag = "MISTAKE FARE" if mistake else "CHEAP FARE"
            if alert(f"{tag} ({source})\n{title}\n{e.get('link')}"):
                con.execute("UPDATE deals SET alerted=1 WHERE id=?", (uid,))
    con.commit()
    export(con)


def export(con):
    rows = con.execute("""SELECT source,title,link,price,currency,is_mistake,found_at
                          FROM deals ORDER BY found_at DESC LIMIT 200""").fetchall()
    keys = ["source", "title", "link", "price", "currency", "is_mistake", "found_at"]
    with open(OUT_JSON, "w") as f:
        json.dump([dict(zip(keys, r)) for r in rows], f, indent=1)


if __name__ == "__main__":
    poll()
