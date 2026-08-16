import os
import re
import html
import json
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

# 💡 復活ポイント1: 以前の豊富なデータ項目（news_type, primary_source, is_lineup等）を完全復元
class ArticleAnalysis(BaseModel):
    is_football: bool
    title_ja: str
    summary_ja: str
    genre: str
    news_type: str = "news"
    primary_source: str = "独自記事"
    is_lineup: bool = False
    lineup_team: Optional[str] = None

def clean_html(text: str) -> str:
    if not text: return ""
    cleanr = re.compile('<.*?>')
    return re.sub(cleanr, '', html.unescape(text)).strip()

def fetch_rss_feeds(feeds_config: List[dict], max_articles=5) -> List[ArticleItem]:
    articles = []
    # RSSサイトからのブロックを防ぐための偽装ヘッダー
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    
    for feed in feeds_config:
        url = feed.get("url", "")
        name = feed.get("name", "Unknown")
        try:
            res = requests.get(url, headers=headers, timeout=12)
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
    """Groq APIで翻訳と厳密なジャンル分けを行う"""
    if not GROQ_API_KEY:
        # 💡 ここもFalseに変更（APIキーがない場合に英語で誤爆するのを防ぐ）
        return ArticleAnalysis(is_football=False, title_ja=article.title, summary_ja=article.summary, genre="general")

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    
    system_prompt = (
        "あなたは海外サッカーに詳しいプロのスポーツジャーナリスト兼翻訳家です。\n"
        "【絶対命令】title_ja と summary_ja は絶対に100%日本語に翻訳・要約してください。英語のままは厳禁です。\n\n"
        "【ジャンル(genre)分類の厳密なルール】\n"
        "ニュースの内容を深く分析し、以下のいずれか1つを正確に選んでください。\n"
        "ニュースの「発信元メディア」ではなく、ニュースの「主役となっているクラブ・選手が所属するリーグ」で判定してください。\n"
        "- transfers : 選手や監督の「移籍」「獲得」「ローン」「契約延長」に関する公式発表や噂のみ。\n"
        "- japanese : 日本人選手や日本代表に関するニュース。\n"
        "- national : 日本以外の各国代表チームに関するニュース。\n"
        "- laliga : レアル・マドリード、バルセロナ、アトレティコ・マドリード等、スペイン1部リーグ所属クラブの話題。\n"
        "- premier : マンチェスター・シティ、マンチェスター・ユナイテッド、リバプール、アーセナル、チェルシー等、イングランド・プレミアリーグの話題。\n"
        "- bundesliga : バイエルン、ドルトムント等、ドイツ・ブンデスリーガ所属クラブの話題。\n"
        "- serie_a : ユベントス、ミラン、インテル等、イタリア・セリエA所属クラブの話題。\n"
        "- ligue_1 : PSG、マルセイユ等、フランス・リーグアン所属クラブの話題。\n"
        "- general : どのカテゴリにも属さないもの（引退、ビジネス、事件など）。\n\n"
        "【JSON出力フォーマット】\n"
        "以下のキーを必ず含むJSONオブジェクトのみを出力してください。\n"
        "is_football (bool), title_ja (str), summary_ja (str), genre (上記から1つ), news_type ('official', 'rumor', 'news' のいずれか), primary_source (大元の情報源、なければ '独自記事'), is_lineup (bool), lineup_team (スタメン対象チーム名、なければnull)"
    )

    user_prompt = f"配信元メディア: {article.source_name}\nタイトル: {article.title}\n概要: {article.summary}"
    
    payload = {
        "model": "llama-3.1-8b-instant",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.1
    }
    
    # 💡 修正1: リトライ回数を3回に増やす
    for attempt in range(3):
        try:
            res = requests.post(url, headers=headers, json=payload, timeout=20)
            if res.status_code == 200:
                data = json.loads(res.json()["choices"][0]["message"]["content"])
                
                return ArticleAnalysis(
                    is_football=data.get("is_football", True),
                    title_ja=data.get("title_ja", article.title),
                    summary_ja=data.get("summary_ja", article.summary[:100]),
                    genre=data.get("genre", "general").lower(),
                    news_type=data.get("news_type", "news"),
                    primary_source=data.get("primary_source", "独自記事"),
                    is_lineup=data.get("is_lineup", False),
                    lineup_team=data.get("lineup_team", None)
                )
        except Exception as e:
            print(f"🔄 Groq APIエラー (試行 {attempt+1}/3): {e}")
            # 💡 修正2: 待機時間を5秒に延長し、APIの混雑をやり過ごす
            time.sleep(5)
            
    # 💡 修正3: 全て失敗した場合は is_football=False にして安全に破棄（英語での誤爆を防ぐ）
    return ArticleAnalysis(is_football=False, title_ja=article.title, summary_ja=article.summary[:100], genre="general")