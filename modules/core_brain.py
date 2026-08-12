import os
import re
import html
import time
import requests
import feedparser
from pydantic import BaseModel
from typing import Optional, List

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

class ArticleItem(BaseModel):
    title: str
    link: str
    summary: str
    source_name: str

class ArticleAnalysis(BaseModel):
    is_football: bool
    title_ja: str
    summary_ja: str
    genre: str

def clean_html(text: str) -> str:
    if not text: return ""
    cleanr = re.compile('<.*?>')
    return re.sub(cleanr, '', html.unescape(text)).strip()

def fetch_rss_feeds(feeds_config: List[dict], max_articles=5) -> List[ArticleItem]:
    articles = []
    for feed in feeds_config:
        url = feed.get("url", "")
        name = feed.get("name", "Unknown")
        try:
            res = requests.get(url, timeout=10)
            parsed = feedparser.parse(res.content)
            for entry in getattr(parsed, "entries", [])[:max_articles]:
                title = clean_html(getattr(entry, "title", ""))
                link = getattr(entry, "link", "")
                summary = clean_html(getattr(entry, "summary", getattr(entry, "description", ""))) or title
                if title and link:
                    articles.append(ArticleItem(title=title, link=link, summary=summary, source_name=name))
        except Exception as e:
            print(f"⚠️ RSS取得エラー [{name}]: {e}")
    return articles

def process_with_groq(article: ArticleItem) -> ArticleAnalysis:
    """Groq APIで翻訳とジャンル分けを行う"""
    if not GROQ_API_KEY:
        return ArticleAnalysis(is_football=True, title_ja=article.title, summary_ja=article.summary, genre="general")

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    
    prompt = (
        f"欧州サッカー専門の翻訳家として以下のニュースを処理してください。\n"
        f"タイトル: {article.title}\n概要: {article.summary}\n\n"
        f"出力は以下のJSONフォーマットのみにしてください。\n"
        f"{{\"is_football\": true/false, \"title_ja\": \"日本語タイトル\", \"summary_ja\": \"短めの日本語要約\", \"genre\": \"transfers/laliga/premier/general等\"}}"
    )
    
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
        "temperature": 0.2
    }
    
    for _ in range(2):
        try:
            res = requests.post(url, headers=headers, json=payload, timeout=15)
            if res.status_code == 200:
                import json
                data = json.loads(res.json()["choices"][0]["message"]["content"])
                return ArticleAnalysis(**data)
        except Exception:
            time.sleep(2)
            
    return ArticleAnalysis(is_football=True, title_ja=article.title, summary_ja=article.summary[:100], genre="general")