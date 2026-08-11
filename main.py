#!/usr/bin/env python3
"""
海外サッカー RSS 自動収集・Groq(Llama3)翻訳・分類・Discord自動配信スクリプト
"""

import os
import sys
import json
import time
import re
import html
import argparse
import warnings
import traceback
from typing import List, Dict, Optional, Set
from datetime import datetime, timezone, timedelta

warnings.filterwarnings("ignore")
import requests
import feedparser
from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SENT_HISTORY_FILE = os.path.join(BASE_DIR, "sent_history.json")
FEEDS_CONFIG_FILE = os.path.join(BASE_DIR, "feeds_config.json")
LINEUP_HISTORY_FILE = os.path.join(BASE_DIR, "lineup_history.json")

class MatchFixture(BaseModel):
    date_jst: str
    time_jst: str
    league_or_tournament: str
    home_team: str
    away_team: str
    is_featured: bool
    featured_reason: Optional[str] = None

class WeeklySchedule(BaseModel):
    period_str: str
    featured_matches: List[MatchFixture]
    all_matches: List[MatchFixture]

class ArticleAnalysis(BaseModel):
    is_football: bool
    title_ja: str
    summary_ja: str
    genre: str
    news_type: str = "news"
    primary_source: str = "独自記事"
    is_lineup: bool = False
    lineup_team: Optional[str] = None

class LineupHistory:
    def __init__(self, filepath: str = LINEUP_HISTORY_FILE):
        self.filepath = filepath
        self.records: Set[str] = set()
        self.load()

    def load(self):
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        self.records = set(data)
                        return
            except Exception as e:
                print(f"⚠️ [LineupHistory] スタメン送信履歴の読み込み失敗 ({e})。新規作成します。")
        self.records = set()
        self.save()

    def is_lineup_sent(self, team_name: str) -> bool:
        if not team_name:
            return False
        today_str = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")
        key = f"{today_str}:{team_name.strip().lower()}"
        return key in self.records

    def add(self, team_name: str):
        if not team_name:
            return
        today_str = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")
        key = f"{today_str}:{team_name.strip().lower()}"
        self.records.add(key)
        self.save()

    def save(self):
        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(list(self.records), f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"❌ [LineupHistory] 保存失敗: {e}")

def is_japanese_text(text: str) -> bool:
    if not text:
        return False
    return bool(re.search(r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]', text))

class ArticleItem:
    def __init__(self, title: str, link: str, summary: str, source_name: str, published: str = "", image_url: Optional[str] = None):
        self.original_title = title
        self.link = link
        self.summary = summary
        self.source_name = source_name
        self.published = published
        self.image_url = image_url

class SentHistory:
    def __init__(self, filepath: str = SENT_HISTORY_FILE, max_records: int = 5000):
        self.filepath = filepath
        self.max_records = max_records
        self.sent_urls: Set[str] = set()
        self.sent_titles: Set[str] = set()
        self.load()

    def load(self):
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        self.sent_urls = set(data)
                    elif isinstance(data, dict):
                        self.sent_urls = set(data.get("urls", []))
                        self.sent_titles = set(data.get("titles", []))
                    return
            except Exception as e:
                print(f"⚠️ [SentHistory] 読み込み失敗 ({e})。初期化します。")
        self.sent_urls = set()
        self.sent_titles = set()
        self.save()

    def is_sent(self, url: str, title: str) -> bool:
        if url in self.sent_urls:
            return True
        clean_target = re.sub(r'\W+', '', title.lower())
        for saved_title in self.sent_titles:
            if clean_target == re.sub(r'\W+', '', saved_title.lower()):
                return True
        return False

    def add(self, url: str, title: str):
        self.sent_urls.add(url)
        self.sent_titles.add(title)
        self.save()

    def save(self):
        try:
            urls_list = list(self.sent_urls)[-self.max_records:]
            titles_list = list(self.sent_titles)[-self.max_records:]
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump({"urls": urls_list, "titles": titles_list}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"❌ [SentHistory] 保存失敗: {e}")


class GroqProcessor:
    def __init__(self, api_key: Optional[str] = None):
        key = api_key or os.getenv("GROQ_API_KEY", "")
        self.api_key = key.strip() if isinstance(key, str) else ""
        self.api_url = "https://api.groq.com/openai/v1/chat/completions"
        self.primary_model = "llama-3.3-70b-versatile"
        self.fallback_model = "llama-3.1-8b-instant"

        if not self.api_key:
            print("ℹ️ [GroqProcessor] 有効な GROQ_API_KEY が未設定。簡易処理モードで動きます。")

    def process(self, article: ArticleItem, max_retries: int = 2) -> ArticleAnalysis:
        if not self.api_key:
            return self._fallback_process(article)

        # ✨ ここを大幅に修正し、AIに「移籍とそれ以外」の違いを厳密に学習させます
        system_prompt = """
        あなたは海外サッカーに詳しいプロのスポーツジャーナリスト兼翻訳家です。
        【絶対命令】title_ja と summary_ja は絶対に100%日本語に翻訳・要約してください。英語のままは厳禁です。
        
        【ジャンル(genre)分類の厳密なルール】
        ニュースの内容を深く分析し、以下のいずれか1つを正確に選んでください。
        - transfers : 選手や監督の「移籍」「獲得」「ローン」「契約延長」に関する公式発表や噂のみ。
          ※注意※ 「現役引退(休止)」「解任」「移籍に直接関係ない事件・裁判・逮捕」はここに含めず、generalなどにしてください。
        - japanese : 日本人選手や日本代表に関するニュース。
        - national : 日本以外の各国代表チームに関するニュース。
        - laliga, premier, bundesliga, serie_a, ligue_1 : 各リーグの試合結果、怪我、戦術、クラブ内の出来事。
        - general : どのカテゴリにも属さないもの（引退、サッカー界のビジネス、事件、その他のリーグなど）。

        出力は必ず以下のJSONフォーマット（キーと値の構造）に完全に従うJSONオブジェクトとして返してください。
        """

        user_prompt = f"""
        【対象ニュース】
        配信元メディア: {article.source_name}
        タイトル: {article.original_title}
        概要: {article.summary}

        【JSON出力フォーマット】
        {{
          "is_football": true または false (サッカー関連か),
          "title_ja": "魅力的な日本語タイトル全訳",
          "summary_ja": "日本語で2〜3文の要約",
          "genre": "transfers, japanese, national, laliga, premier, bundesliga, serie_a, ligue_1, general のいずれか",
          "news_type": "official, rumor, news のいずれか",
          "primary_source": "大元の情報源 (なければ '独自記事')",
          "is_lineup": true または false (スタメン発表か),
          "lineup_team": "スタメン発表対象のチーム名 (なければ null)"
        }}
        """

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        for model in [self.primary_model, self.fallback_model]:
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.2
            }

            for attempt in range(1, max_retries + 1):
                try:
                    res = requests.post(self.api_url, headers=headers, json=payload, timeout=20)
                    if res.status_code == 200:
                        data = res.json()
                        content = data["choices"][0]["message"]["content"]
                        
                        bt3 = "`" * 3
                        content = re.sub(r"^" + bt3 + r"(?:json)?\s*", "", content.strip())
                        content = re.sub(r"\s*" + bt3 + r"$", "", content)
                        
                        parsed_data = json.loads(content)
                        analysis = ArticleAnalysis(**parsed_data)

                        if not is_japanese_text(analysis.title_ja):
                            if attempt < max_retries:
                                time.sleep(1.5)
                                continue

                        return analysis
                    else:
                        if res.status_code == 429:
                            time.sleep(3.0 * attempt)
                            continue
                        break
                except Exception:
                    time.sleep(2.0)

        return self._fallback_process(article)

    def generate_schedule(self, max_retries: int = 2) -> Optional[WeeklySchedule]:
        if not self.api_key:
            return None

        jst_now = datetime.now(timezone(timedelta(hours=9)))
        current_date_str = jst_now.strftime("%Y年%m月%d日(%a)")

        system_prompt = "あなたはプロの海外サッカーアナリスト兼スケジュール編成専門家です。必ず以下のJSONフォーマットで返してください。"
        
        user_prompt = f"""
        本日 ({current_date_str}) から直近1週間（7日間）に開催予定の欧州・国内外サッカー試合スケジュールを作成してください。
        キックオフ日時は日本時間(JST)で計算し、注目ピックアップカードを3〜6試合選定してください。

        【JSON出力フォーマット】
        {{
          "period_str": "対象期間（例: '2026年7月27日(月) 〜 8月2日(日)'）",
          "featured_matches": [
            {{
              "date_jst": "開催日（例: '7月28日(火)'）",
              "time_jst": "キックオフ時間",
              "league_or_tournament": "大会名",
              "home_team": "ホームチーム名",
              "away_team": "アウェイチーム名",
              "is_featured": true,
              "featured_reason": "注目理由"
            }}
          ],
          "all_matches": [
             // 上記と同じ形式で直近1週間の全試合リスト
          ]
        }}
        """

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        for model in [self.primary_model, self.fallback_model]:
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.2
            }

            for attempt in range(1, max_retries + 1):
                try:
                    res = requests.post(self.api_url, headers=headers, json=payload, timeout=30)
                    if res.status_code == 200:
                        data = res.json()
                        content = data["choices"][0]["message"]["content"]
                        
                        bt3 = "`" * 3
                        content = re.sub(r"^" + bt3 + r"(?:json)?\s*", "", content.strip())
                        content = re.sub(r"\s*" + bt3 + r"$", "", content)
                        
                        parsed_data = json.loads(content)
                        return WeeklySchedule(**parsed_data)
                    elif res.status_code == 429:
                        time.sleep(3.0 * attempt)
                except Exception:
                    time.sleep(2.0)

        return None

    def _fallback_process(self, article: ArticleItem) -> ArticleAnalysis:
        text = (article.original_title + " " + article.summary).lower()
        non_football_keywords = ["tennis", "f1", "formula 1", "golf", "nba", "basketball", "baseball", "cricket", "rugby", "motogp", "wimbledon", "us open", "テニス", "ゴルフ"]
        is_football = not any(k in text for k in non_football_keywords)

        japanese_keywords = ["mitoma", "kubo", "endo", "tomiyasu", "furuhashi", "minamino", "doan", "sugawara", "kamada", "ito", "japan", "samurai blue", "ゲキサカ", "フットボールチャンネル"]
        transfer_keywords = ["transfer", "sign", "deal", "contract", "target", "bid", "loan", "move", "joined", "fee", "agreed", "rumour", "rumor", "移籍", "獲得", "加入"]
        laliga_keywords = ["real madrid", "barcelona", "barca", "atletico", "la liga", "spain", "real sociedad", "girona", "sevilla", "betis", "バルセロナ", "レアル"]
        premier_keywords = ["manchester city", "man city", "arsenal", "liverpool", "manchester united", "man utd", "chelsea", "tottenham", "spurs", "premier league", "england", "プレミア"]
        bundesliga_keywords = ["bundesliga", "bayern", "dortmund", "leverkusen", "stuttgart", "leipzig", "germany", "バイエルン", "ドルトムント"]
        serie_a_keywords = ["serie a", "inter", "milan", "juventus", "napoli", "roma", "lazio", "italy", "セリエ"]
        ligue_1_keywords = ["ligue 1", "psg", "paris saint-germain", "monaco", "marseille", "lyon", "france", "パリ・サンジェルマン"]

        genre = "general"
        if any(k in text for k in transfer_keywords):
            genre = "transfers"
        elif any(k in text for k in japanese_keywords):
            genre = "japanese"
        elif any(k in text for k in laliga_keywords):
            genre = "laliga"
        elif any(k in text for k in premier_keywords):
            genre = "premier"
        elif any(k in text for k in bundesliga_keywords):
            genre = "bundesliga"
        elif any(k in text for k in serie_a_keywords):
            genre = "serie_a"
        elif any(k in text for k in ligue_1_keywords):
            genre = "ligue_1"

        news_type = "news"
        if any(k in text for k in ["official", "confirmed", "announced", "statement", "公式", "発表"]):
            news_type = "official"
        elif any(k in text for k in ["rumor", "rumour", "reportedly", "target", "interest", "噂", "報道"]):
            news_type = "rumor"

        lineup_keywords = ["starting xi", "lineup", "line-up", "alineacion", "alineaciones", "スタメン", "先発"]
        is_lineup = any(k in text for k in lineup_keywords)

        return ArticleAnalysis(
            is_football=is_football,
            title_ja=article.original_title,
            summary_ja=article.summary[:150] + "..." if len(article.summary) > 150 else article.summary,
            genre=genre,
            news_type=news_type,
            primary_source=article.source_name,
            is_lineup=is_lineup,
            lineup_team=article.source_name if is_lineup else None
        )

def format_schedule_message(schedule: WeeklySchedule) -> str:
    lines = []
    lines.append("📅 **【欧州サッカー＆注目マッチ 直近1週間試合スケジュール】**")
    lines.append(f"🗓️ **対象期間: {schedule.period_str} (JST)**\n")
    
    lines.append("【🔥 今週の注目ピックアップカード】")
    if schedule.featured_matches:
        for m in schedule.featured_matches:
            reason_str = f" (*{m.featured_reason}*)" if m.featured_reason else ""
            lines.append(f"🌟 **[{m.date_jst} {m.time_jst}]** [{m.league_or_tournament}] **{m.home_team} vs {m.away_team}**{reason_str}")
    else:
        lines.append("※今週の主要ピックアップカードはありません。")
    
    lines.append("\n──────────────────────────────────────────────────\n")
    lines.append("⚽ **【直近7日間の対戦カード一覧 (JST)】**")
    
    current_date = None
    for m in schedule.all_matches:
        if m.date_jst != current_date:
            current_date = m.date_jst
            lines.append(f"\n🗓️ **{current_date}**")
        lines.append(f"  • `{m.time_jst}` [{m.league_or_tournament}] {m.home_team} vs {m.away_team}")
        
    return "\n".join(lines)

def send_discord_chunks(webhook_url: str, text: str, max_length: int = 1900) -> bool:
    if not text: return False
    chunks = []
    current_chunk = []
    current_length = 0

    for line in text.split("\n"):
        line_len = len(line) + 1
        if current_length + line_len > max_length:
            if current_chunk:
                chunks.append("\n".join(current_chunk))
                current_chunk = []
                current_length = 0
        current_chunk.append(line)
        current_length += line_len

    if current_chunk:
        chunks.append("\n".join(current_chunk))

    success = True
    total_parts = len(chunks)
    for idx, chunk in enumerate(chunks, 1):
        try:
            res = requests.post(webhook_url, json={"content": chunk}, timeout=10)
            if res.status_code == 429:
                time.sleep(res.json().get("retry_after", 5))
                res = requests.post(webhook_url, json={"content": chunk}, timeout=10)
            if res.status_code not in (200, 204): success = False
        except Exception:
            success = False
        time.sleep(1)
    return success

class DiscordNotifier:
    GENRE_CONFIG = {
        "transfers": {"label": "移籍情報・噂", "color": 0xF1C40F, "env_var": "WEBHOOK_TRANSFERS", "icon": "🔄"},
        "japanese": {"label": "日本人選手", "color": 0xE74C3C, "env_var": "WEBHOOK_JAPANESE", "icon": "🇯🇵"},
        "national": {"label": "代表ニュース", "color": 0x1ABC9C, "env_var": "WEBHOOK_NATIONAL", "icon": "🌐"},
        "laliga": {"label": "ラ・リーガ", "color": 0xE67E22, "env_var": "WEBHOOK_LALIGA", "icon": "🇪🇸"},
        "premier": {"label": "プレミアリーグ", "color": 0x9B59B6, "env_var": "WEBHOOK_PREMIER", "icon": "🏴󠁧󠁢󠁥󠁮󠁧󠁿"},
        "bundesliga": {"label": "ブンデスリーガ", "color": 0xD32F2F, "env_var": "WEBHOOK_BUNDESLIGA", "icon": "🇩🇪"},
        "serie_a": {"label": "セリエA", "color": 0x008C45, "env_var": "WEBHOOK_SERIE_A", "icon": "🇮🇹"},
        "ligue_1": {"label": "リーグ・アン", "color": 0x091C3E, "env_var": "WEBHOOK_LIGUE_1", "icon": "🇫🇷"},
        "general": {"label": "総合ニュース", "color": 0x3498DB, "env_var": "WEBHOOK_GENERAL", "icon": "⚽"},
    }
    NEWS_TYPE_MAP = {"official": "【公式/確定】", "rumor": "【噂/ゴシップ】", "news": "【ニュース】"}

    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.webhooks = {g: os.getenv(c["env_var"], "").strip() for g, c in self.GENRE_CONFIG.items()}

    def send(self, article: ArticleItem, analysis: ArticleAnalysis) -> bool:
        raw_genre = getattr(analysis, "genre", "")
        safe_genre = raw_genre.lower().strip() if isinstance(raw_genre, str) else "general"
        if safe_genre == "transfer": safe_genre = "transfers"

        is_lineup = getattr(analysis, "is_lineup", False)
        
        if is_lineup:
            full_title = f"【🚨 スタメン速報】 {analysis.title_ja}"
            webhook_url = os.getenv("WEBHOOK_LINEUP", "").strip() or self.webhooks.get("general")
            color = 0xE74C3C
            category_label = f"🚨 スタメン速報 ({analysis.lineup_team})" if getattr(analysis, "lineup_team", None) else "🚨 スタメン速報"
            category_icon = "🚨"
        elif safe_genre == "transfers":
            full_title = f"{self.NEWS_TYPE_MAP.get(getattr(analysis, 'news_type', 'news'), '【ニュース】')} {analysis.title_ja}"
            webhook_url = self.webhooks.get("transfers") or self.webhooks.get("general")
            color, category_label, category_icon = self.GENRE_CONFIG["transfers"]["color"], self.GENRE_CONFIG["transfers"]["label"], self.GENRE_CONFIG["transfers"]["icon"]
        elif safe_genre in self.GENRE_CONFIG and safe_genre != "general":
            full_title = f"{self.NEWS_TYPE_MAP.get(getattr(analysis, 'news_type', 'news'), '【ニュース】')} {analysis.title_ja}"
            webhook_url = self.webhooks.get(safe_genre) or self.webhooks.get("general")
            color, category_label, category_icon = self.GENRE_CONFIG[safe_genre]["color"], self.GENRE_CONFIG[safe_genre]["label"], self.GENRE_CONFIG[safe_genre]["icon"]
        else:
            full_title = f"{self.NEWS_TYPE_MAP.get(getattr(analysis, 'news_type', 'news'), '【ニュース】')} {analysis.title_ja}"
            webhook_url = self.webhooks.get("general")
            color, category_label, category_icon = self.GENRE_CONFIG["general"]["color"], self.GENRE_CONFIG["general"]["label"], self.GENRE_CONFIG["general"]["icon"]

        primary_src = getattr(analysis, "primary_source", "") or "独自記事"
        source_display = f"{article.source_name} (引用: {primary_src})" if primary_src and primary_src not in ("独自記事", article.source_name) else f"独自記事（{article.source_name}）"

        print(f"\n📰 [{category_icon} {category_label}] {full_title}")

        if self.dry_run or not webhook_url: return False

        embed = {
            "title": full_title,
            "url": article.link,
            "description": analysis.summary_ja,
            "color": color,
            "fields": [
                {"name": "📰 情報源", "value": source_display, "inline": True},
                {"name": "🏷️ カテゴリ", "value": f"{category_icon} {category_label}", "inline": True},
                {"name": "🔗 原文タイトル", "value": article.original_title[:1024], "inline": False}
            ],
            "footer": {"text": "海外サッカー 自動ニュース配信 Bot | Groq Powered"},
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        if article.image_url: embed["image"] = {"url": article.image_url}

        try:
            res = requests.post(webhook_url, json={"embeds": [embed]}, timeout=10)
            if res.status_code == 429:
                time.sleep(res.json().get("retry_after", 5))
                res = requests.post(webhook_url, json={"embeds": [embed]}, timeout=10)
            return res.status_code in (200, 204)
        except Exception:
            return False

class RSSFetcher:
    @staticmethod
    def _clean_html(text: str) -> str:
        if not text: return ""
        return html.unescape(re.sub(r'<[^>]+>', '', html.unescape(str(text)))).strip()

    def fetch_feed(self, source_name: str, feed_url: str, max_articles: int = 5) -> List[ArticleItem]:
        articles = []
        try:
            if "kicker.de/bundesliga.rss" in feed_url: feed_url = "https://newsfeed.kicker.de/news/bundesliga"
            elif "gazzetta.it/rss/Calcio.xml" in feed_url: feed_url = "https://www.gazzetta.it/rss/calcio.xml"
            elif "xml.lequipe.fr" in feed_url: feed_url = "https://www.footmercato.net/flux-rss"

            res = requests.Session().get(feed_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
            parsed = feedparser.parse(res.content) if res.status_code == 200 else feedparser.parse(feed_url)

            for entry in getattr(parsed, "entries", [])[:max_articles]:
                title = self._clean_html(entry.get("title", getattr(entry, "title", "")))
                link = entry.get("link", getattr(entry, "link", ""))
                summary = self._clean_html(entry.get("summary", getattr(entry, "summary", entry.get("description", getattr(entry, "description", ""))))) or title
                
                image_url = None
                for media in getattr(entry, "media_content", entry.get("media_content", [])) or []:
                    if isinstance(media, dict) and media.get("url"): image_url = media["url"]; break

                if title and link: articles.append(ArticleItem(title, link, summary, source_name, image_url=image_url))
        except Exception:
            pass
        return articles

class SoccerNewsBot:
    def __init__(self, dry_run: bool = False, max_per_feed: int = 5):
        self.dry_run = dry_run
        self.max_per_feed = max_per_feed
        self.sent_history = SentHistory()
        self.lineup_history = LineupHistory()
        self.ai_processor = GroqProcessor()
        self.notifier = DiscordNotifier(dry_run=dry_run)
        self.fetcher = RSSFetcher()
        
        default_feeds = [
            {"name": "BBC Football", "url": "https://feeds.bbci.co.uk/sport/football/rss.xml"},
            {"name": "Sky Sports", "url": "https://www.skysports.com/rss/12040"}
        ]
        if os.path.exists(FEEDS_CONFIG_FILE):
            try:
                with open(FEEDS_CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.feeds = data if isinstance(data, list) and data else default_feeds
            except: self.feeds = default_feeds
        else: self.feeds = default_feeds

    def run_once(self):
        print(f"\n🚀 [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] RSSニュース巡回・処理を開始します...")
        try:
            for feed_info in self.feeds:
                name, url = feed_info.get("name", "Unknown"), feed_info.get("url", "")
                if not url: continue
                
                print(f"🔍 巡回中: {name}")
                for article in self.fetcher.fetch_feed(name, url, self.max_per_feed):
                    if self.sent_history.is_sent(article.link, article.original_title): continue
                    
                    time.sleep(1.0)
                    analysis = self.ai_processor.process(article)

                    if not analysis or not analysis.title_ja or not is_japanese_text(analysis.title_ja) or not analysis.is_football:
                        if not self.dry_run: self.sent_history.add(article.link, article.original_title)
                        continue

                    if getattr(analysis, "is_lineup", False) and getattr(analysis, "lineup_team", None):
                        if self.lineup_history.is_lineup_sent(analysis.lineup_team):
                            if not self.dry_run: self.sent_history.add(article.link, article.original_title)
                            continue

                    if self.notifier.send(article, analysis) and not self.dry_run:
                        self.sent_history.add(article.link, article.original_title)
                        if getattr(analysis, "is_lineup", False) and getattr(analysis, "lineup_team", None):
                            self.lineup_history.add(analysis.lineup_team)
        except Exception as e:
            print(f"💥 エラー: {e}")
            sys.exit(1)

    def run_schedule(self):
        schedule_data = self.ai_processor.generate_schedule()
        if schedule_data and not self.dry_run:
            webhook = os.getenv("WEBHOOK_SCHEDULE", "").strip()
            if webhook: send_discord_chunks(webhook, format_schedule_message(schedule_data))

    def run_loop(self, interval_minutes: int = 15):
        while True:
            self.run_once()
            time.sleep(interval_minutes * 60)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--schedule", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--interval", type=int, default=15)
    args = parser.parse_args()

    bot = SoccerNewsBot(dry_run=args.dry_run, max_per_feed=args.limit)
    if args.schedule: bot.run_schedule()
    elif args.loop: bot.run_loop(interval_minutes=args.interval)
    else: bot.run_once()

if __name__ == "__main__":
    main()