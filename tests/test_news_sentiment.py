from unittest.mock import patch, MagicMock
import json
import time


AV_RESPONSE = {
    "feed": [
        {
            "title": "NVDA lands major supply agreement",
            "url": "https://example.com/nvda",
            "time_published": "20260513T120000",
            "ticker_sentiment": [
                {"ticker": "NVDA", "ticker_sentiment_score": "0.35", "ticker_sentiment_label": "Bullish"}
            ],
        }
    ]
}


def test_sentiment_returned_for_ticker():
    from tools.fetch_news_sentiment import get_sentiment
    with patch("tools.fetch_news_sentiment.requests.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=200, json=lambda: AV_RESPONSE)
        result = get_sentiment("NVDA", api_key="TEST")
        assert result["ticker"] == "NVDA"
        assert result["label"] == "Bullish"
        assert result["score"] > 0


def test_etf_skipped():
    from tools.fetch_news_sentiment import get_sentiment
    result = get_sentiment("VOO", api_key="TEST")
    assert result is None


def test_cached_result_returned_without_api_call(tmp_path):
    from tools.fetch_news_sentiment import get_sentiment
    cache_file = tmp_path / "news_cache.json"
    cached = {
        "AMD": {
            "ticker": "AMD",
            "label": "Bullish",
            "score": 0.4,
            "articles": [],
            "fetched_at": time.time(),  # fresh
        }
    }
    cache_file.write_text(json.dumps(cached))
    with patch("tools.fetch_news_sentiment.CACHE_FILE", cache_file):
        with patch("tools.fetch_news_sentiment.requests.get") as mock_get:
            result = get_sentiment("AMD", api_key="TEST")
            mock_get.assert_not_called()
    assert result["label"] == "Bullish"
