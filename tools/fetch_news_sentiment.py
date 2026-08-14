import json
import os
import time
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

CACHE_FILE = Path(".tmp/news_cache.json")
CACHE_TTL_SECONDS = 6 * 3600
# ETFs have no meaningful per-name news sentiment, so they are skipped.
ETF_TICKERS = {"VOO", "VWRP"}
AV_BASE = "https://www.alphavantage.co/query"


def _load_cache():
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_cache(cache):
    CACHE_FILE.parent.mkdir(exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, indent=2))


def get_sentiment(ticker, api_key=None, cache=None):
    if ticker in ETF_TICKERS:
        return None

    if api_key is None:
        api_key = os.getenv("ALPHA_VANTAGE_KEY", "")

    if cache is None:
        cache = _load_cache()

    # Return cached result if fresh
    if ticker in cache:
        age = time.time() - cache[ticker].get("fetched_at", 0)
        if age < CACHE_TTL_SECONDS:
            return cache[ticker]

    url = (
        f"{AV_BASE}?function=NEWS_SENTIMENT"
        f"&tickers={ticker}"
        f"&apikey={api_key}"
        f"&limit=5"
    )
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    articles = data.get("feed", [])
    if not articles:
        return None

    scores = []
    article_summaries = []
    for article in articles:
        for ts in article.get("ticker_sentiment", []):
            if ts.get("ticker") == ticker:
                try:
                    scores.append(float(ts["ticker_sentiment_score"]))
                    article_summaries.append({
                        "title": article["title"],
                        "url": article["url"],
                        "label": ts["ticker_sentiment_label"],
                        "score": float(ts["ticker_sentiment_score"]),
                    })
                except (ValueError, KeyError):
                    pass

    if not scores:
        return None

    avg_score = sum(scores) / len(scores)
    label = "Bullish" if avg_score > 0.15 else "Bearish" if avg_score < -0.15 else "Neutral"

    result = {
        "ticker": ticker,
        "score": round(avg_score, 4),
        "label": label,
        "articles": article_summaries[:3],
        "fetched_at": time.time(),
    }
    cache[ticker] = result
    _save_cache(cache)
    return result


def batch_get_sentiment(tickers, api_key=None, max_calls=15):
    if api_key is None:
        api_key = os.getenv("ALPHA_VANTAGE_KEY", "")
    cache = _load_cache()
    results = {}
    calls_made = 0

    for ticker in tickers:
        if ticker in ETF_TICKERS:
            continue
        # Use cache if available
        if ticker in cache:
            age = time.time() - cache[ticker].get("fetched_at", 0)
            if age < CACHE_TTL_SECONDS:
                results[ticker] = cache[ticker]
                continue
        if calls_made >= max_calls:
            break
        result = get_sentiment(ticker, api_key=api_key, cache=cache)
        if result:
            results[ticker] = result
        calls_made += 1
        time.sleep(0.5)  # avoid rate limiting

    return results


if __name__ == "__main__":
    import csv
    tickers = []
    with open("config/portfolio.csv") as f:
        for row in csv.DictReader(f):
            tickers.append(row["ticker"])
    results = batch_get_sentiment(tickers)
    non_neutral = {t: r for t, r in results.items() if r["label"] != "Neutral"}
    print(f"Non-neutral: {len(non_neutral)} tickers")
    for ticker, r in non_neutral.items():
        print(f"  {ticker}: {r['label']} ({r['score']:+.3f})")
