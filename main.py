#!/usr/bin/env python3
"""
海外サッカー RSS 自動収集・Groq(Llama3)翻訳・分類・Discord自動配信スクリプト
＋ Threads安全配信システム（完全無料・ランダム待機・URL排除版）
"""

import os
import sys
import json
import time
import random
import re
import html
import argparse
import warnings
import traceback
import unicodedata
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
THREADS_BUFFER_FILE = os.path.join(BASE_DIR, "threads_buffer.json") # 追加: Threads用のバッファファイル

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

def normalize_text(text: str) -> str:
    """Unicode正規化(NFC)。濁点・合成文字の二重表示（例: Radonjiô̌̌）を防ぐ。"""
    if not text:
        return text
    return unicodedata.normalize("NFC", text)

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

        system_prompt = """
        あなたは海外サッカーに詳しいプロのスポーツジャーナリスト兼翻訳家です。
        【絶対命令】title_ja と summary_ja は絶対に100%日本語に翻訳・要約してください。英語のままは厳禁です。
        
        【ジャンル(genre)分類の厳密なルール】
        ニュースの内容を深く分析し、以下のいずれか1つを正確に選んでください。
        ニュースの「発信元メディア」ではなく、ニュースの「主役となっているクラブ・選手が所属するリーグ」で判定してください。
        - transfers : 選手や監督の「移籍」「獲得」「ローン」「契約延長」に関する公式発表や噂のみ。移籍元・移籍先が複数リーグにまたがる場合は必ずこちらを優先。
        - japanese : 日本人選手や日本代表に関するニュース。
        - national : 日本以外の各国代表チームに関するニュース。
        - laliga : レアル・マドリード、バルセロナ、アトレティコ・マドリード等、スペイン1部リーグ所属クラブの話題。
        - premier : マンチェスター・シティ、マンチェスター・ユナイテッド、リバプール、アーセナル、チェルシー等、イングランド・プレミアリーグ所属クラブの話題。
        - bundesliga : バイエルン、ドルトムント等、ドイツ・ブンデスリーガ所属クラブの話題。
        - serie_a : ユベントス、ミラン、インテル等、イタリア・セリエA所属クラブの話題。
        - ligue_1 : PSG、マルセイユ等、フランス・リーグアン所属クラブの話題。
        - general : どのカテゴリにも属さないもの（引退、サッカー界のビジネス、事件、移籍が絡まない他リーグなど）。

        【注意】記事がどのRSSサイト（例: kicker.de, MARCA等）から取得されたかに惑わされず、必ず記事の「内容（登場するクラブ・選手）」を基準に分類してください。

        出力は必ず以下のJSONフォーマットに完全に従うJSONオブジェクトとして返してください。
        """

        user_prompt = f"""
        【対象ニュース】
        配信元メディア: {article.source_name}
        タイトル: {article.original_title}
        概要: {article.summary}

        【JSON出力フォーマット】
        {{
          "is_football": true または false,
          "title_ja": "魅力的な日本語タイトル全訳",
          "summary_ja": "日本語で2〜3文の要約",
          "genre": "transfers, japanese, national, laliga, premier, bundesliga, serie_a, ligue_1, general のいずれか",
          "news_type": "official, rumor, news のいずれか",
          "primary_source": "大元の情報源 (なければ '独自記事')",
          "is_lineup": true または false,
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
                        analysis.title_ja = normalize_text(analysis.title_ja)
                        analysis.summary_ja = normalize_text(analysis.summary_ja)
                        if analysis.lineup_team:
                            analysis.lineup_team = normalize_text(analysis.lineup_team)

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
        user_prompt = f"本日 ({current_date_str}) から直近1週間（7日間）に開催予定の欧州・国内外サッカー試合スケジュールを作成してください。"
        
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

        genre = "general"
        if any(k in text for k in ["transfer", "sign", "deal", "移籍", "獲得"]): genre = "transfers"
        elif any(k in text for k in ["mitoma", "kubo", "endo"]): genre = "japanese"
        elif any(k in text for k in ["real madrid", "barcelona", "atletico madrid", "la liga"]): genre = "laliga"
        elif any(k in text for k in ["manchester city", "manchester united", "liverpool", "arsenal", "chelsea", "tottenham", "premier league"]): genre = "premier"
        elif any(k in text for k in ["bayern", "dortmund", "bundesliga"]): genre = "bundesliga"
        elif any(k in text for k in ["juventus", "milan", "inter", "serie a"]): genre = "serie_a"
        elif any(k in text for k in ["psg", "paris saint", "marseille", "ligue 1"]): genre = "ligue_1"

        return ArticleAnalysis(
            is_football=is_football,
            title_ja=article.original_title,
            summary_ja=article.summary[:150],
            genre=genre,
            primary_source=article.source_name
        )

# ----------------- Discord 関連 -----------------
def format_schedule_message(schedule: WeeklySchedule) -> str:
    lines = []
    lines.append("📅 **【欧州サッカー＆注目マッチ 直近1週間試合スケジュール】**")
    lines.append(f"🗓️ **対象期間: {schedule.period_str} (JST)**\n")
    return "\n".join(lines)

def send_discord_chunks(webhook_url: str, text: str, max_length: int = 1900) -> bool:
    if not text or not webhook_url:
        return False
    try:
        for i in range(0, len(text), max_length):
            chunk = text[i:i + max_length]
            res = requests.post(webhook_url, json={"content": chunk}, timeout=10)
            if res.status_code not in (200, 204):
                print(f"❌ [Discord] スケジュール配信失敗: {res.text}")
                return False
        return True
    except Exception as e:
        print(f"💥 [Discord] スケジュール配信エラー: {e}")
        return False

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
        webhook_url = self.webhooks.get("general")
        
        if is_lineup:
            full_title = f"【🚨 スタメン速報】 {analysis.title_ja}"
            color, category_label, category_icon = 0xE74C3C, "🚨 スタメン速報", "🚨"
        elif safe_genre in self.GENRE_CONFIG:
            full_title = f"{self.NEWS_TYPE_MAP.get(getattr(analysis, 'news_type', 'news'), '【ニュース】')} {analysis.title_ja}"
            webhook_url = self.webhooks.get(safe_genre) or self.webhooks.get("general")
            color, category_label, category_icon = self.GENRE_CONFIG[safe_genre]["color"], self.GENRE_CONFIG[safe_genre]["label"], self.GENRE_CONFIG[safe_genre]["icon"]
        else:
            full_title = f"{self.NEWS_TYPE_MAP.get(getattr(analysis, 'news_type', 'news'), '【ニュース】')} {analysis.title_ja}"
            color, category_label, category_icon = self.GENRE_CONFIG["general"]["color"], self.GENRE_CONFIG["general"]["label"], self.GENRE_CONFIG["general"]["icon"]

        primary_src = getattr(analysis, "primary_source", "") or "独自記事"
        source_display = f"{article.source_name} (引用: {primary_src})" if primary_src and primary_src not in ("独自記事", article.source_name) else f"独自記事（{article.source_name}）"

        print(f"\n📰 [Discord: {category_icon} {category_label}] {full_title}")

        if self.dry_run or not webhook_url: return False

        embed = {
            "title": full_title,
            "url": article.link,
            "description": analysis.summary_ja,
            "color": color,
            "fields": [
                {"name": "📰 情報源", "value": source_display, "inline": True},
                {"name": "🏷️ カテゴリ", "value": f"{category_icon} {category_label}", "inline": True}
            ]
        }
        try:
            res = requests.post(webhook_url, json={"embeds": [embed]}, timeout=10)
            return res.status_code in (200, 204)
        except: return False


# ----------------- Threads 関連 -----------------
class ThreadsBuffer:
    """Threads用の安全投稿バッファ（完全無料・URL排除・キューシステム）"""
    def __init__(self, filepath: str = THREADS_BUFFER_FILE):
        self.filepath = filepath
        self.queue: List[Dict] = []
        self.load()

    def load(self):
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    self.queue = json.load(f)
                    return
            except Exception as e:
                print(f"⚠️ [ThreadsBuffer] 読み込み失敗 ({e})。初期化します。")
        self.queue = []
        self.save()

    def add(self, article: ArticleItem, analysis: ArticleAnalysis):
        primary_src = getattr(analysis, "primary_source", "") or "独自記事"
        source_display = primary_src if primary_src not in ("独自記事", article.source_name) else article.source_name
        
        # 必要なテキストデータのみ抽出し、URLは保存しない
        self.queue.append({
            "title": analysis.title_ja,
            "summary": analysis.summary_ja,
            "source": source_display,
            "genre": getattr(analysis, "genre", "general")
        })
        self.save()

    def save(self):
        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(self.queue, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"❌ [ThreadsBuffer] 保存失敗: {e}")
    def get_count(self) -> int:
        return len(self.queue)

    def pop_next(self) -> Optional[Dict]:
        if not self.queue:
            return None
        return self.queue.pop(0)

    def prepend(self, item: Dict):
        self.queue.insert(0, item)

class ThreadsNotifier:
    """Threadsへの自動投稿管理（視認性ヘッダー・文字数制限安全弁付き）"""
    HEADER_MAP = {
        "transfers": "🔄移籍・噂",
        "japanese": "🇯🇵日本人選手",
        "national": "🌐代表ニュース",
        "laliga": "🇪🇸ラ・リーガ",
        "premier": "🏴󠁧󠁢󠁥󠁮󠁧󠁿プレミアリーグ",
        "bundesliga": "🇩🇪ブンデスリーガ",
        "serie_a": "🇮🇹セリエA",
        "ligue_1": "🇫🇷リーグ・アン",
        "general": "⚽ニュース"
    }

    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.user_id = os.getenv("THREADS_USER_ID", "me").strip()
        self.access_token = os.getenv("THREADS_ACCESS_TOKEN", "").strip()
        self.api_url = f"https://graph.threads.net/v1.0/{self.user_id}"

    def build_text(self, item: Dict) -> str:
        genre = str(item.get("genre", "general")).lower().strip()
        if genre == "transfer": genre = "transfers"
        header = self.HEADER_MAP.get(genre, self.HEADER_MAP["general"])

        # ハッシュタグを完全削除し、不要な改行を詰めてスマートなレイアウトに
        # AIの要約（summary）とソース元だけをシンプルに繋ぎます
        text = f"{header}\n{item['title']}\n\n{item['summary']}\n\nソース: {item['source']}"
        
        # 念のため、AIの本文に「【】」や「#」が混入していた場合の強制排除セーフティ
        text = text.replace("【", "").replace("】", "").replace("#", "")
        
        return text

    def post_text(self, text: str) -> bool:
        if not self.user_id or not self.access_token:
            return False
        
        # セーフティ: 500文字制限
        if len(text) > 495:
            text = text[:492] + "..."

        if self.dry_run:
            print(f"\n📱 [Threads Dry-Run]:\n{text}")
            return True

        try:
            # 1. コンテナ作成
            media_url = f"{self.api_url}/threads"
            payload = {"media_type": "TEXT", "text": text, "access_token": self.access_token}
            res = requests.post(media_url, data=payload, timeout=15)
            if res.status_code != 200: 
                print(f"❌ [Threads] コンテナ作成失敗: {res.text}")
                return False
            
            creation_id = res.json().get("id")

            # 2. 公開処理
            publish_url = f"{self.api_url}/threads_publish"
            publish_payload = {"creation_id": creation_id, "access_token": self.access_token}
            res_pub = requests.post(publish_url, data=publish_payload, timeout=15)
            if res_pub.status_code == 200:
                print("✅ Threadsへの配信が完了しました。")
                return True
            else:
                print(f"❌ [Threads] 公開失敗: {res_pub.text}")
                return False
        except Exception as e:
            print(f"💥 [Threads] エラー発生: {e}")
            return False

class RSSFetcher:
    @staticmethod
    def _clean_html(text: str) -> str:
        if not text: return ""
        cleaned = html.unescape(re.sub(r'<[^>]+>', '', html.unescape(str(text)))).strip()
        return normalize_text(cleaned)

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
        
        # 🆕 Threads用のモジュール初期化
        self.threads_buffer = ThreadsBuffer()
        self.threads_notifier = ThreadsNotifier(dry_run=dry_run)
        
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

    def process_all_feeds(self):
        """全RSSフィードを巡回し、新着ニュースをAI解析→Discord送信＆Threadsバッファへ追加"""
        total_processed = 0
        for feed in self.feeds:
            name = feed.get("name", "Unknown")
            url = feed.get("url", "")
            if not url:
                continue

            print(f"🔎 [{name}] フィードを取得中...")
            articles = self.fetcher.fetch_feed(name, url, self.max_per_feed)

            for article in articles:
                # 既に送信済み（URL or タイトル一致）ならスキップ
                if self.sent_history.is_sent(article.link, article.original_title):
                    continue

                # AI解析（翻訳・分類）
                try:
                    analysis = self.ai_processor.process(article)
                except Exception as e:
                    print(f"⚠️ [{name}] AI解析に失敗: {e}")
                    continue

                # サッカー関連でなければ既読扱いにしてスキップ
                if not analysis.is_football:
                    self.sent_history.add(article.link, article.original_title)
                    continue

                # スタメン速報は同日重複を防止
                if analysis.is_lineup and analysis.lineup_team:
                    if self.lineup_history.is_lineup_sent(analysis.lineup_team):
                        print(f"⏭️ [{analysis.lineup_team}] のスタメンは本日送信済みのためスキップします。")
                        self.sent_history.add(article.link, article.original_title)
                        continue
                    self.lineup_history.add(analysis.lineup_team)

                # Discordへ送信
                self.notifier.send(article, analysis)

                # Threads投稿用バッファへ追加
                self.threads_buffer.add(article, analysis)

                # 送信済み履歴に記録
                self.sent_history.add(article.link, article.original_title)
                total_processed += 1

                if not self.dry_run:
                    time.sleep(1.0)  # API/Webhookのレート制限を避けるための小休止

        print(f"✅ 今回の巡回で {total_processed} 件の新しいニュースを処理しました。")

    def run_schedule(self):
        """直近1週間の試合スケジュールを生成してDiscordへ配信"""
        print(f"🚀 [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 週間スケジュールの生成を開始します...")

        schedule = self.ai_processor.generate_schedule()
        if not schedule:
            print("⚠️ スケジュールの生成に失敗、またはGROQ_API_KEY未設定のため処理を中断します。")
            return

        text = format_schedule_message(schedule)
        webhook_url = self.notifier.webhooks.get("general")

        if self.dry_run or not webhook_url:
            print(f"\n📅 [Discord Dry-Run]:\n{text}")
        else:
            ok = send_discord_chunks(webhook_url, text)
            if ok:
                print("✅ 週間スケジュールをDiscordへ配信しました。")
            else:
                print("❌ 週間スケジュールの配信に失敗しました。")

        print(f"🏁 [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] スケジュール処理が完了しました。")

    def run_loop(self, interval_minutes: int = 15):
        """指定間隔で run_once を繰り返し実行し続ける（常駐プロセス用）"""
        print(f"🔁 ループモードで起動しました（{interval_minutes}分間隔）。Ctrl+Cで終了します。")
        while True:
            try:
                self.run_once()
            except Exception as e:
                print(f"💥 巡回処理中に予期しないエラーが発生しました: {e}")
                traceback.print_exc()

            print(f"😴 次回の巡回まで {interval_minutes} 分間待機します...")
            try:
                time.sleep(interval_minutes * 60)
            except KeyboardInterrupt:
                print("🛑 ループモードを終了します。")
                break

    def run_once(self):
        """1回のみ巡回と配信を実行（GitHub Actions用）"""
        print(f"🚀 [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] RSSニュース巡回・処理を開始します...")
        
        # 1. 各RSSフィードから新しいニュースを取得してDiscordへ送信 ＆ Threadsバッファへ追加
        self.process_all_feeds()

        # 2. Threadsバッファ（物置）から最大3件を連続で安全に消化する
        max_process_count = 3
        processed_count = 0

        print(f"📦 現在Threadsバッファ（物置）に溜まっているニュース: {self.threads_buffer.get_count()} 件")

        while processed_count < max_process_count:
            # バッファから先頭の1件を取得（消費）
            next_item = self.threads_buffer.pop_next()
            
            if not next_item:
                if processed_count == 0:
                    print("ℹ️ Threadsに投稿待ちのニュースはありません。")
                else:
                    print(f"✨ バッファが空になったため、計 {processed_count} 件の投稿で処理を終了します。")
                break

            processed_count += 1
            print(f"📱 [{processed_count}/{max_process_count}件目] Threadsへの投稿プロセスを開始します...")

            # 投稿文を組み立てる
            threads_text = self.threads_notifier.build_text(next_item)

            # Bot判定（シャドウバン）を完全に回避するためのランダム待機（90秒〜180秒）
            # ※2件目以降の投稿がある場合のみ待機を挟みます（1件目は即投稿して時間を節約）
            if processed_count > 1:
                wait_time = random.randint(90, 180)
                print(f"⏳ 連投によるBot判定を回避するため、{wait_time} 秒間ランダム待機します...")
                if not self.dry_run:
                    time.sleep(wait_time)

            # Threadsへ投稿
            success = self.threads_notifier.post_text(threads_text)
            
            if not success:
                print("❌ Threadsへの投稿に失敗したため、このアイテムをバッファの先頭に戻して処理を中断します。")
                self.threads_buffer.prepend(next_item)
                break

        # 最後に配信履歴と Threads バッファの状態を保存
        self.sent_history.save()
        self.threads_buffer.save()
        print(f"🏁 [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 1回分の処理が正常に完了しました。")

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