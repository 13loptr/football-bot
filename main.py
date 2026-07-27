#!/usr/bin/env python3
"""
海外サッカー RSS 自動収集・Gemini翻訳・分類・Discord自動配信スクリプト
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

# 非必須の警告を非表示
warnings.filterwarnings("ignore")
import requests
import feedparser
from dotenv import load_dotenv
from pydantic import BaseModel, Field

# google-genai SDK
try:
    from google import genai
    from google.genai import types
    HAS_GEMINI_SDK = True
except ImportError:
    HAS_GEMINI_SDK = False

# .envファイルを読み込み
load_dotenv()

# スクリプト設置ディレクトリ
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SENT_HISTORY_FILE = os.path.join(BASE_DIR, "sent_history.json")
FEEDS_CONFIG_FILE = os.path.join(BASE_DIR, "feeds_config.json")
LINEUP_HISTORY_FILE = os.path.join(BASE_DIR, "lineup_history.json")

# Pydantic モデル（Gemini Structured Output用）
class MatchFixture(BaseModel):
    date_jst: str = Field(description="開催日（例: '7月28日(火)'）")
    time_jst: str = Field(description="日本時間のキックオフ時間（例: '20:00', '翌04:00'）")
    league_or_tournament: str = Field(description="大会名・リーグ名・親善試合（例: 'プレミアリーグ', 'プレシーズンマッチ', 'UEFAチャンピオンズリーグ'）")
    home_team: str = Field(description="ホームチーム名（日本語表記）")
    away_team: str = Field(description="アウェイチーム名（日本語表記）")
    is_featured: bool = Field(description="注目試合・ダービー・ビッグクラブ対決・注目プレシーズンマッチであれば True")
    featured_reason: Optional[str] = Field(default=None, description="注目試合である理由（例: '伝統のダービー対決', 'プレシーズン注目のビッグマッチ'）")

class WeeklySchedule(BaseModel):
    period_str: str = Field(description="対象期間（例: '2026年7月27日(月) 〜 8月2日(日)'）")
    featured_matches: List[MatchFixture] = Field(description="注目ピックアップカード（3〜6試合程度）")
    all_matches: List[MatchFixture] = Field(description="直近1週間の全試合リスト（日付順）")

class ArticleAnalysis(BaseModel):
    is_football: bool = Field(
        description=(
            "ニュースがサッカー（フットボール）に関連する話題であれば True、"
            "テニス、F1、ゴルフ、バスケットボール、野球などサッカー以外のスポーツや無関係な話題であれば False"
        )
    )
    title_ja: str = Field(description="自然で分かりやすい日本語に意訳・全翻訳したニュースタイトル")
    summary_ja: str = Field(description="ニュースの概要を日本語で2〜3文に要約した文章")
    genre: str = Field(
        description=(
            "ニュースの内容に基づくジャンル判定。"
            "必ず以下のいずれか一つを返してください: "
            "'transfers', 'japanese', 'national', 'laliga', 'premier', 'bundesliga', 'serie_a', 'ligue_1', 'general'"
        )
    )
    news_type: str = Field(
        default="news",
        description=(
            "ニュースの性質・確実性分類。"
            "必ず以下のいずれか一つを返してください: "
            "'official' (公式発表/確定事項/試合結果), "
            "'rumor' (噂/ゴシップ/メディア推測), "
            "'news' (通常のニュース/レポート/分析)"
        )
    )
    primary_source: str = Field(
        default="独自記事",
        description=(
            "記事内で引用・参照されている大元の情報源・メディア・記者名（例: 'MARCA紙', 'ファブリツィオ・ロマーノ氏'）。"
            "特に他メディアの引用がなければ '独自記事'。"
        )
    )
    is_lineup: bool = Field(
        default=False,
        description="試合のスターティングメンバー（スタメン/Starting XI/Alineaciones/XI inicial/lineup/先発メンバー）の発表に関する記事であれば True、それ以外は False"
    )
    lineup_team: Optional[str] = Field(
        default=None,
        description="スタメンが発表された対象チーム名（日本語表記、例: 'レアル・マドリード', 'アーセナル', '日本代表'）。is_lineupがTrueの場合のみ指定"
    )

class LineupHistory:
    """当日配信済みスタメン（日付+チーム名）の重複防止管理モジュール"""
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
            except Exception as e:
                print(f"⚠️ [LineupHistory] スタメン送信履歴の読み込みに失敗しました ({e})。新規作成します。")
                self.records = set()
        else:
            self.records = set()

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
            print(f"❌ [LineupHistory] スタメン送信履歴の保存に失敗しました: {e}")

def is_japanese_text(text: str) -> bool:
    """テキストに日本語（ひらがな・カタカナ・漢字）が含まれているか検証"""
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

    def __repr__(self):
        return f"<ArticleItem title='{self.original_title}' source='{self.source_name}'>"


class SentHistory:
    """送信済み記事URLの永続化管理モジュール"""
    def __init__(self, filepath: str = SENT_HISTORY_FILE, max_records: int = 5000):
        self.filepath = filepath
        self.max_records = max_records
        self.sent_urls: Set[str] = set()
        self.load()

    def load(self):
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        self.sent_urls = set(data)
            except Exception as e:
                print(f"⚠️ [SentHistory] 送信履歴ファイルの読み込みに失敗しました ({e})。新規作成します。")
                self.sent_urls = set()
        else:
            self.sent_urls = set()

    def is_sent(self, url: str) -> bool:
        return url in self.sent_urls

    def add(self, url: str):
        self.sent_urls.add(url)
        self.save()

    def save(self):
        try:
            # 最新のmax_records件に絞って保存
            urls_list = list(self.sent_urls)
            if len(urls_list) > self.max_records:
                urls_list = urls_list[-self.max_records:]
                self.sent_urls = set(urls_list)

            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(urls_list, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"❌ [SentHistory] 送信履歴ファイルの保存に失敗しました: {e}")


class GeminiProcessor:
    """Gemini APIを使用してニュースの日本語翻訳およびジャンル自動判別を行うモジュール"""
    def __init__(self, api_key: Optional[str] = None):
        key = api_key or os.getenv("GEMINI_API_KEY", "")
        self.api_key = key.strip() if isinstance(key, str) else ""
        self.client = None
        
        # 有効なキーが設定されている場合のみ Client を初期化（空文字列やダミーを回避）
        self.model_name = os.getenv("GEMINI_MODEL", "gemini-flash-latest").strip()
        if self.api_key and not self.api_key.startswith("your_") and HAS_GEMINI_SDK:
            try:
                self.client = genai.Client(api_key=self.api_key)
            except Exception as e:
                print(f"⚠️ [GeminiProcessor] Gemini Client 初期化エラー: {e}")
        else:
            print("ℹ️ [GeminiProcessor] 有効な GEMINI_API_KEY が未設定のため、キーワードベースの簡易処理モードで動きます。")

    def process(self, article: ArticleItem, max_retries: int = 3, retry_delay: float = 2.0) -> ArticleAnalysis:
        """記事のタイトルと要約を翻訳・分類する（リトライ・レートリミット対策・日本語検証付き）"""
        if not self.client:
            return self._fallback_process(article)

        prompt = f"""
あなたは海外サッカーに詳しいプロのスポーツジャーナリスト兼翻訳家です。
以下のニュースの「タイトル」と「本文/概要」を分析してください。

【対象ニュース】
配信元メディア: {article.source_name}
タイトル: {article.original_title}
概要: {article.summary}

【指示】
1. サッカー判定 (is_football):
   - ニュースがサッカー（フットボール）に関連する話題（規模の大規模・小規模問わず全般）であれば True を設定してください。
   - テニス、F1/モータースポーツ、ゴルフ、バスケットボール、野球、ラグビー、クリケットなど「サッカー以外のスポーツ」や無関係な話題であれば False を設定してください。
2. タイトル翻訳 (title_ja):
   - タイトルを日本のサッカーファン向けに自然で魅力的な日本語タイトルに全訳してください。
   - 原文が英語・スペイン語・ドイツ語・フランス語・イタリア語などの外国語の場合は、絶対に英語のまま残さず必ず日本語に全訳してください。
3. 概要要約・翻訳 (summary_ja):
   - 概要をわかりやすい日本語で2〜3文に要約・翻訳してください。
4. 確実性・性質ラベル判定 (news_type):
   - ニュースの内容に応じて、必ず以下のいずれか一つを厳密に選んでください。
     - 'official': クラブ・連盟の公式発表、確定した契約更新・移籍決定、怪我の公式リリース、確定した試合結果・スタッツなど【公式/確定】な情報。
     - 'rumor': 移籍の噂、メディアによる推測・報道・オファー打診の噂、関係者の証言など【噂/ゴシップ】に該当する情報。
     - 'news': 上記以外の通常のニュース、マッチレポート、戦術分析コラムなど【ニュース】情報。
5. 大元の情報源抽出 (primary_source):
   - 本文中に別のメディアやジャーナリスト（例: 「MARCA紙によると」「ファブリツィオ・ロマーノ氏によれば」「Sky Sports報道」など）が引用・言及されている場合、その大元の情報源・記者名を抽出してください（例: 'MARCA紙', 'ファブリツィオ・ロマーノ氏'）。
   - 特に他メディアや特定の記者の引用がない場合は '独自記事' と設定してください。
6. ジャンル判定 (genre):
   - ニュースの内容に応じて、以下の優先順位に従って必ずいずれか一つを厳密に選択してください。
     - 'transfers': 選手や監督の移籍、移籍の噂・報道、オファー、契約更新・延長、加入・退団、ローン・バイアウト、ターゲット獲得交渉に関連する話題（※移籍・契約に関連するニュースは最優先で選択してください）。
     - 'japanese': 日本人選手（三笘薫、久保建英、遠藤航など）が所属クラブ等で主役または直接言及されている話題（移籍話題を除く）。
     - 'national': 日本代表（サムライブルー）をはじめ、欧州・南米各国のA代表、W杯予選、代表招集メンバー、代表戦試合結果、国際親善試合などナショナルチーム・代表戦に関連する話題。
     - 'laliga': ラ・リーガ（レアル・マドリード、バルセロナ、アトレティコ等）のクラブ・試合・リーグ話題。
     - 'premier': プレミアリーグ（マンC、アーセナル、リヴァプール、マンU、チェルシー等）のクラブ・試合・リーグ話題。
     - 'bundesliga': ブンデスリーガ（バイエルン、ドルトムント、レバークーゼン等）のクラブ・試合・リーグ話題。
     - 'serie_a': セリエA（インテル、ACミラン、ユヴェントス、ナポリ等）のクラブ・試合・リーグ話題。
     - 'ligue_1': リーグ・アン（PSG、モナコ、マルセイユ等）のクラブ・試合・リーグ話題。
     - 'general': 上記のどの専用ジャンル（移籍、日本人、代表、各主要リーグ）にも該当しない純粋な総合ニュース・全般的なコラム等（最後の受け皿）。
7. スタメン発表判定 (is_lineup & lineup_team):
   - ニュース記事が「試合のスターティングメンバー（スタメン、Starting XI、Alineaciones、XI inicial、先発メンバー）の発表」に関する記事であるかを判定してください (is_lineup: True/False)。
   - スタメン発表記事である場合 (is_lineup: True)、対象となるチーム名（日本語表記、例: 'レアル・マドリード', 'アーセナル', '日本代表'）を lineup_team に設定してください。スタメン記事でない場合は null を設定してください。

必ず指定されたスキーマに従ってJSONを出力してください。
"""

        candidate_models = [self.model_name, "gemini-flash-latest", "gemini-2.0-flash"]
        # 重複除去
        seen = set()
        models_to_try = [m for m in candidate_models if not (m in seen or seen.add(m))]

        for model_target in models_to_try:
            for attempt in range(1, max_retries + 1):
                try:
                    response = self.client.models.generate_content(
                        model=model_target,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                            response_schema=ArticleAnalysis,
                            temperature=0.2,
                        ),
                    )

                    # レスポンスのパース
                    if response and response.text:
                        text_content = response.text.strip()
                        # マークダウンコードブロック(```json ... ```)の除去
                        if text_content.startswith("```"):
                            text_content = re.sub(r"^```(?:json)?\s*", "", text_content)
                            text_content = re.sub(r"\s*```$", "", text_content)
                        
                        data = json.loads(text_content)
                        analysis = ArticleAnalysis(**data)

                        # 日本語判定バリデーション: 原文タイトルに英字があり、返却タイトルに日本語が含まれていない場合は翻訳再試行
                        if not is_japanese_text(analysis.title_ja):
                            print(f"⚠️ [GeminiProcessor] 翻訳未完了を検出（日本語文字なし: '{analysis.title_ja}'）。再試行します (試行 {attempt}/{max_retries})...")
                            if attempt < max_retries:
                                time.sleep(1.5)
                                continue

                        return analysis
                except Exception as e:
                    err_str = str(e)
                    print(f"⚠️ [GeminiProcessor] API呼び出しエラー (モデル: {model_target}, 試行 {attempt}/{max_retries}): {err_str}")
                    
                    # 404 NOT_FOUND の場合はモデル切り替え
                    if "404" in err_str or "NOT_FOUND" in err_str:
                        print(f"⚠️ [GeminiProcessor] モデル '{model_target}' が利用不能のため、次のモデル候補に切り替えます。")
                        break

                    if attempt < max_retries:
                        wait_sec = retry_delay * (2 ** (attempt - 1))
                        if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str or "Quota" in err_str:
                            # エラーレスポンスからAPI推奨の待機秒数(retryIn/retryDelay)を動的抽出
                            delay_match = re.search(r'retry in ([0-9]+(?:\.[0-9]+)?)s', err_str, re.IGNORECASE)
                            if not delay_match:
                                delay_match = re.search(r"'retryDelay':\s*['\"]([0-9]+)s['\"]", err_str, re.IGNORECASE)
                            
                            if delay_match:
                                wait_sec = float(delay_match.group(1)) + 1.5
                            else:
                                wait_sec = max(wait_sec, 15.0 * attempt)

                        print(f"⏳ [GeminiProcessor] {wait_sec:.1f}秒待機して再試行します...")
                        time.sleep(wait_sec)

        print(f"❌ [GeminiProcessor] 全てのリトライおよびモデル候補での実行が失敗しました。フォールバック処理を適用します。")
        return self._fallback_process(article)

    def generate_schedule(self, max_retries: int = 3, retry_delay: float = 2.0) -> Optional[WeeklySchedule]:
        """直近1週間のサッカー試合スケジュールを生成する"""
        if not self.client:
            print("⚠️ [GeminiProcessor] 有効な Gemini Client が未初期化のためスケジュール作成をスキップします。")
            return None

        # 日本時間の本日日付を取得
        jst_now = datetime.now(timezone(timedelta(hours=9)))
        current_date_str = jst_now.strftime("%Y年%m月%d日(%a)")

        prompt = f"""
あなたはプロの海外サッカーアナリスト兼スケジュール編成専門家です。
本日 ({current_date_str}) から直近1週間（7日間）に開催予定の欧州サッカーおよび注目の国内外サッカー試合スケジュールを作成してください。

【対象範囲】
1. 各国代表戦（日本代表/サムライブルー、欧州・南米等の国際Aマッチ、W杯予選、ネーションズリーグ、国際親善試合など）★必須網羅
2. 欧州5大リーグ（プレミアリーグ、ラ・リーガ、セリエA、ブンデスリーガ、リーグ・アン）
3. 主要カップ戦（UEFAチャンピオンズリーグ、ヨーロッパリーグ、各国内カップ戦等）
4. プレシーズンマッチ、ジャパンツアー、クラブ親善試合全般

【指示】
1. キックオフ日時は必ず「日本時間（JST）」で正確に計算して記述してください。
2. 今週開催される全対戦カードから、特に注目すべき代表戦の大一番（日本代表の公式戦・強豪国同士の親善試合/公式戦）、伝統のダービー対決、ビッグクラブ対決、注目のプレシーズンマッチを「注目ピックアップカード (featured_matches)」として3〜6試合選択し、注目理由(featured_reason)を明記してください。
3. 全対戦カードを日付順・キックオフ時間順に網羅的に整理して全試合リスト (all_matches) に格納してください。

必ず指定されたスキーマに従ってJSONを出力してください。
"""

        candidate_models = [self.model_name, "gemini-flash-latest", "gemini-2.0-flash"]
        seen = set()
        models_to_try = [m for m in candidate_models if not (m in seen or seen.add(m))]

        for model_target in models_to_try:
            for attempt in range(1, max_retries + 1):
                try:
                    response = self.client.models.generate_content(
                        model=model_target,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                            response_schema=WeeklySchedule,
                            temperature=0.2,
                        ),
                    )

                    if response and response.text:
                        text_content = response.text.strip()
                        if text_content.startswith("```"):
                            text_content = re.sub(r"^```(?:json)?\s*", "", text_content)
                            text_content = re.sub(r"\s*```$", "", text_content)
                        
                        data = json.loads(text_content)
                        return WeeklySchedule(**data)
                except Exception as e:
                    err_str = str(e)
                    print(f"⚠️ [GeminiProcessor] スケジュール生成エラー (モデル: {model_target}, 試行 {attempt}/{max_retries}): {err_str}")
                    
                    if "404" in err_str or "NOT_FOUND" in err_str:
                        break

                    if attempt < max_retries:
                        wait_sec = retry_delay * (2 ** (attempt - 1))
                        if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str or "Quota" in err_str:
                            delay_match = re.search(r'retry in ([0-9]+(?:\.[0-9]+)?)s', err_str, re.IGNORECASE)
                            if not delay_match:
                                delay_match = re.search(r"'retryDelay':\s*['\"]([0-9]+)s['\"]", err_str, re.IGNORECASE)
                            wait_sec = float(delay_match.group(1)) + 1.5 if delay_match else max(wait_sec, 15.0 * attempt)

                        print(f"⏳ [GeminiProcessor] {wait_sec:.1f}秒待機して再試行します...")
                        time.sleep(wait_sec)

        return None

    def _fallback_process(self, article: ArticleItem) -> ArticleAnalysis:
        """APIキーが未設定またはエラー時のフォールバック処理（簡易キーワード判定）"""
        text = (article.original_title + " " + article.summary).lower()

        # サッカー以外のスポーツ判定
        non_football_keywords = ["tennis", "f1", "formula 1", "golf", "nba", "basketball", "baseball", "cricket", "rugby", "motogp", "wimbledon", "us open", "テニス", "ゴルフ"]
        is_football = not any(k in text for k in non_football_keywords)

        # 簡易ジャンル判定
        japanese_keywords = ["mitoma", "kubo", "endo", "tomiyasu", "furuhashi", "minamino", "doan", "sugawara", "kamada", "ito", "japan", "samurai blue", "ゲキサカ", "フットボールチャンネル"]
        transfer_keywords = ["transfer", "sign", "deal", "contract", "target", "bid", "loan", "move", "joined", "fee", "agreed", "rumour", "rumor", "移籍", "獲得", "加入", "退団", "契約"]
        laliga_keywords = ["real madrid", "barcelona", "barca", "atletico", "la liga", "spain", "real sociedad", "girona", "sevilla", "betis", "バルセロナ", "レアル"]
        premier_keywords = ["manchester city", "man city", "arsenal", "liverpool", "manchester united", "man utd", "chelsea", "tottenham", "spurs", "premier league", "england", "プレミア"]
        bundesliga_keywords = ["bundesliga", "bayern", "dortmund", "leverkusen", "stuttgart", "leipzig", "germany", "バイエルン", "ドルトムント"]
        serie_a_keywords = ["serie a", "inter", "milan", "juventus", "napoli", "roma", "lazio", "italy", "セリエ"]
        ligue_1_keywords = ["ligue 1", "psg", "paris saint-germain", "monaco", "marseille", "lyon", "france", "パリ・サンジェルマン"]

        genre = "general" # 最後の受け皿
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

        # 性質ラベル判定
        news_type = "news"
        if any(k in text for k in ["official", "confirmed", "announced", "statement", "公式", "発表"]):
            news_type = "official"
        elif any(k in text for k in ["rumor", "rumour", "reportedly", "target", "interest", "噂", "報道"]):
            news_type = "rumor"

        # スタメン簡易判定
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
    """WeeklyScheduleオブジェクトをDiscord配信用の読みやすいマークダウン文字列に整形"""
    lines = []
    lines.append("📅 **【欧州サッカー＆注目マッチ 直近1週間試合スケジュール】**")
    lines.append(f"🗓️ **対象期間: {schedule.period_str} (JST)**\n")
    
    # 1. 今週の注目ピックアップカード
    lines.append("【🔥 今週の注目ピックアップカード】")
    if schedule.featured_matches:
        for m in schedule.featured_matches:
            reason_str = f" (*{m.featured_reason}*)" if m.featured_reason else ""
            lines.append(f"🌟 **[{m.date_jst} {m.time_jst}]** [{m.league_or_tournament}] **{m.home_team} vs {m.away_team}**{reason_str}")
    else:
        lines.append("※今週の主要ピックアップカードはありません。")
    
    lines.append("\n──────────────────────────────────────────────────\n")

    # 2. 全試合スケジュール一覧
    lines.append("⚽ **【直近7日間の対戦カード一覧 (JST)】**")
    
    current_date = None
    for m in schedule.all_matches:
        if m.date_jst != current_date:
            current_date = m.date_jst
            lines.append(f"\n🗓️ **{current_date}**")
        lines.append(f"  • `{m.time_jst}` [{m.league_or_tournament}] {m.home_team} vs {m.away_team}")
        
    return "\n".join(lines)


def send_discord_chunks(webhook_url: str, text: str, max_length: int = 1900) -> bool:
    """2000文字を超える長いテキストを改行単位で複数メッセージに分割してDiscord Webhookへ順次送信"""
    if not text:
        return False

    chunks = []
    current_chunk = []
    current_length = 0

    for line in text.split("\n"):
        line_len = len(line) + 1  # 改行コード分+1
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
        payload = {"content": chunk}
        if total_parts > 1:
            print(f"📦 [DiscordNotifier] メッセージ送信 (Part {idx}/{total_parts})...")
        try:
            res = requests.post(webhook_url, json=payload, timeout=10)
            if res.status_code in (200, 204):
                print(f"✅ [DiscordNotifier] 送信成功 (Part {idx}/{total_parts})")
            elif res.status_code == 429:
                retry_after = res.json().get("retry_after", 5)
                print(f"⏳ [DiscordNotifier] Discord Rate Limit検知。{retry_after}秒待機して再送します...")
                time.sleep(retry_after)
                res_retry = requests.post(webhook_url, json=payload, timeout=10)
                if res_retry.status_code not in (200, 204):
                    success = False
            else:
                print(f"❌ [DiscordNotifier] 送信失敗 (HTTP {res.status_code}): {res.text}")
                success = False
        except Exception as e:
            print(f"❌ [DiscordNotifier] 送信通信エラー: {e}")
            success = False

        time.sleep(1)

    return success


class DiscordNotifier:
    """ジャンルに応じたDiscord Webhookへのメッセージ配信モジュール"""

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

    NEWS_TYPE_MAP = {
        "official": "【公式/確定】",
        "rumor": "【噂/ゴシップ】",
        "news": "【ニュース】"
    }

    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.webhooks = {}
        for genre, config in self.GENRE_CONFIG.items():
            self.webhooks[genre] = os.getenv(config["env_var"], "").strip()

    @staticmethod
    def send_system_log(message: str, error: Optional[Exception] = None) -> bool:
        """重大なシステムエラーやスタックトレースを WEBHOOK_SYSTEM 宛に即座に通知する"""
        webhook_url = os.getenv("WEBHOOK_SYSTEM", "").strip()
        if not webhook_url:
            print("⚠️ [SystemLog] WEBHOOK_SYSTEM が未設定のためエラー通知をスキップしました。")
            return False

        error_details = ""
        if error:
            tb_lines = traceback.format_exception(type(error), error, error.__traceback__)
            error_details = "".join(tb_lines)[-1400:]  # Discord文字数制限対策

        jst_time = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M:%S")
        content = f"🚨 **【KICK システムエラー発生アラート】**\n⏰ **発生時刻 (JST):** `{jst_time}`\n📝 **概要:** {message}"
        if error_details:
            content += f"\n\n🔍 **スタックトレース:**\n```python\n{error_details}\n```"

        payload = {"content": content[:1950]}
        try:
            res = requests.post(webhook_url, json=payload, timeout=10)
            if res.status_code in (200, 204):
                print(f"🚨 [SystemLog] WEBHOOK_SYSTEM へのエラー通知送信に成功しました。")
                return True
            else:
                print(f"❌ [SystemLog] エラー通知送信失敗 (HTTP {res.status_code}): {res.text}")
                return False
        except Exception as sys_err:
            print(f"❌ [SystemLog] エラー通知送信通信例外: {sys_err}")
            return False

    def send(self, article: ArticleItem, analysis: ArticleAnalysis) -> bool:
        # 1. 最優先: スタメン速報 (is_lineup == True)
        is_lineup = getattr(analysis, "is_lineup", False)
        if is_lineup:
            label_prefix = "【🚨 スタメン速報】"
            full_title = f"{label_prefix} {analysis.title_ja}"
            lineup_webhook = os.getenv("WEBHOOK_LINEUP", "").strip()
            webhook_url = lineup_webhook if lineup_webhook else self.webhooks.get("general")
            color = 0xE74C3C  # 赤色
            category_label = f"🚨 スタメン速報 ({analysis.lineup_team})" if getattr(analysis, "lineup_team", None) else "🚨 スタメン速報"
            category_icon = "🚨"

        # 2. 移籍情報・噂 (transfers: 確実・最優先ルーティング)
        elif getattr(analysis, "genre", "") == "transfers":
            label_prefix = self.NEWS_TYPE_MAP.get(getattr(analysis, "news_type", "news"), "【ニュース】")
            full_title = f"{label_prefix} {analysis.title_ja}"
            webhook_url = self.webhooks.get("transfers") or self.webhooks.get("general")
            genre_info = self.GENRE_CONFIG["transfers"]
            color = genre_info["color"]
            category_label = genre_info["label"]
            category_icon = genre_info["icon"]

        # 3. 日本人選手ニュース (japanese)
        elif getattr(analysis, "genre", "") == "japanese":
            label_prefix = self.NEWS_TYPE_MAP.get(getattr(analysis, "news_type", "news"), "【ニュース】")
            full_title = f"{label_prefix} {analysis.title_ja}"
            webhook_url = self.webhooks.get("japanese") or self.webhooks.get("general")
            genre_info = self.GENRE_CONFIG["japanese"]
            color = genre_info["color"]
            category_label = genre_info["label"]
            category_icon = genre_info["icon"]

        # 4. 代表ニュース (national)
        elif getattr(analysis, "genre", "") == "national":
            label_prefix = self.NEWS_TYPE_MAP.get(getattr(analysis, "news_type", "news"), "【ニュース】")
            full_title = f"{label_prefix} {analysis.title_ja}"
            webhook_url = self.webhooks.get("national") or self.webhooks.get("general")
            genre_info = self.GENRE_CONFIG["national"]
            color = genre_info["color"]
            category_label = genre_info["label"]
            category_icon = genre_info["icon"]

        # 5. 5大リーグニュース (laliga, premier, bundesliga, serie_a, ligue_1)
        elif getattr(analysis, "genre", "") in ("laliga", "premier", "bundesliga", "serie_a", "ligue_1"):
            label_prefix = self.NEWS_TYPE_MAP.get(getattr(analysis, "news_type", "news"), "【ニュース】")
            full_title = f"{label_prefix} {analysis.title_ja}"
            genre_info = self.GENRE_CONFIG.get(analysis.genre, self.GENRE_CONFIG["general"])
            webhook_url = self.webhooks.get(analysis.genre) or self.webhooks.get("general")
            color = genre_info["color"]
            category_label = genre_info["label"]
            category_icon = genre_info["icon"]

        # 6. 最後の受け皿: 総合・その他ニュース (general)
        else:
            label_prefix = self.NEWS_TYPE_MAP.get(getattr(analysis, "news_type", "news"), "【ニュース】")
            full_title = f"{label_prefix} {analysis.title_ja}"
            genre_info = self.GENRE_CONFIG["general"]
            webhook_url = self.webhooks.get("general")
            color = genre_info["color"]
            category_label = genre_info["label"]
            category_icon = genre_info["icon"]

        # 情報源表示テキストの作成
        primary_src = getattr(analysis, "primary_source", "") or "独自記事"
        if primary_src and primary_src != "独自記事" and primary_src != article.source_name:
            source_display = f"{article.source_name} (引用: {primary_src})"
        else:
            source_display = f"独自記事（{article.source_name}）"

        print(f"\n──────────────────────────────────────────────────")
        print(f"📰 [{category_icon} {category_label}] {full_title}")
        print(f"🔗 原文: {article.original_title} ({article.source_name})")
        print(f"📝 概要: {analysis.summary_ja}")
        print(f"📰 情報源: {source_display}")
        print(f"🌐 URL: {article.link}")

        if self.dry_run:
            print("💡 [DRY-RUN] Discord送信をスキップしました。")
            return True

        if not webhook_url:
            print(f"⚠️ [DiscordNotifier] Webhook URLが設定されていません。スキップします。")
            return False

        # Embedメッセージの作成
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
            "footer": {
                "text": "海外サッカー 自動ニュース配信 Bot | Gemini API Powered"
            },
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

        if article.image_url:
            embed["image"] = {"url": article.image_url}

        payload = {
            "embeds": [embed]
        }

        try:
            res = requests.post(webhook_url, json=payload, timeout=10)
            if res.status_code in (200, 204):
                print(f"✅ [DiscordNotifier] Discordへの送信成功 ({genre_info['label']})")
                return True
            elif res.status_code == 429:
                # レートリミット対応
                retry_after = res.json().get("retry_after", 5)
                print(f"⏳ [DiscordNotifier] Discord Rate limit検知。{retry_after}秒待機して再送します...")
                time.sleep(retry_after)
                res_retry = requests.post(webhook_url, json=payload, timeout=10)
                return res_retry.status_code in (200, 204)
            else:
                print(f"❌ [DiscordNotifier] 送信失敗 (HTTP {res.status_code}): {res.text}")
                return False
        except Exception as e:
            print(f"❌ [DiscordNotifier] 通信エラー: {e}")
            return False


class RSSFetcher:
    """RSSフィードの巡回・パースを行うモジュール"""

    @staticmethod
    def _clean_html(text: str) -> str:
        """HTMLタグおよび文字参照(HTML entity)の除去と整形"""
        if not text:
            return ""
        # 1. HTML実体参照（&amp;, &nbsp;, &#39; 等）をデコード
        text = html.unescape(str(text))
        # 2. HTMLタグ除去
        clean_text = re.sub(r'<[^>]+>', '', text)
        # 3. 再度デコード（エスケープされたタグ文字等に対応）
        clean_text = html.unescape(clean_text)
        # 4. 連続する空白・改行の整形
        clean_text = re.sub(r'\s+', ' ', clean_text).strip()
        return clean_text

    @staticmethod
    def _extract_summary_raw(entry) -> str:
        """フィードエントリーの様々なデータ構造（summary, description, content等）から本文/概要テキストを抽出"""
        # 1. summary
        summary = entry.get("summary", "") if isinstance(entry, dict) else getattr(entry, "summary", "")
        if summary:
            return str(summary)

        # 2. description
        desc = entry.get("description", "") if isinstance(entry, dict) else getattr(entry, "description", "")
        if desc:
            return str(desc)

        # 3. content (Atom/RSS 2.0 encoded content)
        contents = entry.get("content", []) if isinstance(entry, dict) else getattr(entry, "content", [])
        if contents and isinstance(contents, list):
            for c in contents:
                if isinstance(c, dict) and c.get("value"):
                    return str(c["value"])
                elif hasattr(c, "value") and c.value:
                    return str(c.value)

        # 4. summary_detail
        detail = entry.get("summary_detail", {}) if isinstance(entry, dict) else getattr(entry, "summary_detail", {})
        if detail and isinstance(detail, dict) and detail.get("value"):
            return str(detail["value"])
        elif hasattr(detail, "value") and detail.value:
            return str(detail.value)

        return ""

    @staticmethod
    def _extract_image(entry) -> Optional[str]:
        """フィードエントリーから画像URLを抽出"""
        # 1. media_content
        media_content = getattr(entry, "media_content", None) or (entry.get("media_content", None) if isinstance(entry, dict) else None)
        if media_content:
            for media in media_content:
                if isinstance(media, dict) and media.get("url"):
                    return media["url"]
        # 2. media_thumbnail
        media_thumb = getattr(entry, "media_thumbnail", None) or (entry.get("media_thumbnail", None) if isinstance(entry, dict) else None)
        if media_thumb:
            for media in media_thumb:
                if isinstance(media, dict) and media.get("url"):
                    return media["url"]
        # 3. enclosures
        enclosures = getattr(entry, "enclosures", None) or (entry.get("enclosures", None) if isinstance(entry, dict) else None)
        if enclosures:
            for enc in enclosures:
                enc_type = getattr(enc, "type", "") or (enc.get("type", "") if isinstance(enc, dict) else "")
                enc_href = getattr(enc, "href", "") or (enc.get("href", "") if isinstance(enc, dict) else "")
                if "image" in enc_type and enc_href:
                    return enc_href
        # 4. raw summary / content 内の <img> タグ
        raw_text = RSSFetcher._extract_summary_raw(entry)
        img_match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', raw_text, re.IGNORECASE)
        if img_match:
            return img_match.group(1)

        return None

    def fetch_feed(self, source_name: str, feed_url: str, max_articles: int = 5) -> List[ArticleItem]:
        articles = []
        try:
            # ブロックされやすい非推奨URLの自動正規化
            if "kicker.de/bundesliga.rss" in feed_url:
                feed_url = "https://newsfeed.kicker.de/news/bundesliga"
            elif "gazzetta.it/rss/Calcio.xml" in feed_url:
                feed_url = "https://www.gazzetta.it/rss/calcio.xml"
            elif "xml.lequipe.fr" in feed_url:
                feed_url = "https://www.footmercato.net/flux-rss"

            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9,ja;q=0.8,de;q=0.7,it;q=0.7,fr;q=0.7",
                "Upgrade-Insecure-Requests": "1"
            }
            parsed = None
            try:
                res = requests.get(feed_url, headers=headers, timeout=12, allow_redirects=True)
                res.raise_for_status()
                parsed = feedparser.parse(res.content)
            except Exception as req_err:
                # requestsでHTTPエラーや失敗が生じた場合、feedparser直接取得を試行
                parsed = feedparser.parse(feed_url, agent=headers["User-Agent"])

            if not parsed or not parsed.entries:
                print(f"⚠️ [RSSFetcher] エントリーが0件または取得失敗: {source_name} ({feed_url})")
                return articles

            for entry in parsed.entries[:max_articles]:
                raw_title = entry.get("title", "") if isinstance(entry, dict) else getattr(entry, "title", "")
                title = self._clean_html(raw_title)
                link = entry.get("link", "") if isinstance(entry, dict) else getattr(entry, "link", "")
                
                raw_summary = self._extract_summary_raw(entry)
                summary = self._clean_html(raw_summary)

                # 本文/概要が空の場合はタイトルをフォールバックとして使用
                if not summary:
                    summary = title

                published = entry.get("published", "") or entry.get("updated", "") if isinstance(entry, dict) else getattr(entry, "published", getattr(entry, "updated", ""))
                image_url = self._extract_image(entry)

                if title and link:
                    articles.append(ArticleItem(
                        title=title,
                        link=link,
                        summary=summary,
                        source_name=source_name,
                        published=str(published),
                        image_url=image_url
                    ))
        except Exception as e:
            print(f"❌ [RSSFetcher] フィード処理中にエラー発生 ({source_name}): {e}")

        return articles


class SoccerNewsBot:
    """メインコントローラークラス"""
    def __init__(self, dry_run: bool = False, max_per_feed: int = 5):
        self.dry_run = dry_run
        self.max_per_feed = max_per_feed
        self.sent_history = SentHistory()
        self.lineup_history = LineupHistory()
        self.gemini = GeminiProcessor()
        self.notifier = DiscordNotifier(dry_run=dry_run)
        self.fetcher = RSSFetcher()
        self.feeds = self._load_feeds_config()

    def _load_feeds_config(self) -> List[Dict[str, str]]:
        if os.path.exists(FEEDS_CONFIG_FILE):
            try:
                with open(FEEDS_CONFIG_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"⚠️ [SoccerNewsBot] 設定ファイル {FEEDS_CONFIG_FILE} の読み込み失敗 ({e})")
        
        # デフォルトフィード設定
        return [
            {"name": "BBC Football", "url": "https://feeds.bbci.co.uk/sport/football/rss.xml"},
            {"name": "Sky Sports Football", "url": "https://www.skysports.com/rss/12040"},
            {"name": "MARCA (La Liga)", "url": "https://e00-marca.uecdn.es/rss/futbol/primera-division.xml"},
            {"name": "The Guardian (Premier)", "url": "https://www.theguardian.com/football/premierleague/rss"}
        ]

    def run_once(self):
        print(f"\n🚀 [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] RSSニュース巡回・処理を開始します...")
        try:
            total_new = 0
            total_sent = 0

            for feed_info in self.feeds:
                name = feed_info.get("name", "Unknown Feed")
                url = feed_info.get("url", "")
                if not url:
                    continue

                print(f"🔍 巡回中: {name} ({url})")
                try:
                    articles = self.fetcher.fetch_feed(name, url, max_articles=self.max_per_feed)
                except Exception as feed_err:
                    print(f"⚠️ [SoccerNewsBot] フィード取得失敗 ({name}): {feed_err}")
                    continue

                for article in articles:
                    try:
                        # 重複判定 (URLベース)
                        if self.sent_history.is_sent(article.link):
                            continue

                        # Gemini API負荷軽減のための意図的ウェイト (呼び出し前に必ず3秒待機)
                        request_delay = float(os.getenv("GEMINI_REQUEST_DELAY_SECONDS", "3.0"))
                        print(f"⏳ [Pacing] Gemini API負荷制御のため {request_delay:.1f} 秒待機中...")
                        time.sleep(request_delay)

                        # Geminiによる翻訳とジャンル自動判定
                        analysis = self.gemini.process(article)

                        # 翻訳未完了（Gemini APIエラーや返却値が非日本語）の確実な検知とスキップ処理
                        if not analysis or not analysis.title_ja or not is_japanese_text(analysis.title_ja):
                            print(f"⚠️ [SoccerNewsBot] 翻訳未完了（日本語タイトルなし）のため英文のまま配信するのを回避してスキップしました: {article.original_title}")
                            if not self.dry_run:
                                self.sent_history.add(article.link)
                            continue

                        # サッカー以外のスポーツ・ニュースの除外処理
                        if not analysis.is_football:
                            print(f"🚫 [Non-Football Skipped] サッカー以外のニュースのため送信をスキップしました: {article.original_title}")
                            if not self.dry_run:
                                self.sent_history.add(article.link)
                            continue

                        # スタメン速報の当日重複排除チェック
                        if getattr(analysis, "is_lineup", False) and getattr(analysis, "lineup_team", None):
                            if self.lineup_history.is_lineup_sent(analysis.lineup_team):
                                print(f"🚫 [Lineup Duplicate Skipped] 本日すでに '{analysis.lineup_team}' のスタメン速報を配信済みのためスキップしました: {article.original_title}")
                                if not self.dry_run:
                                    self.sent_history.add(article.link)
                                continue

                        total_new += 1

                        # Discord送信
                        success = self.notifier.send(article, analysis)

                        if success:
                            total_sent += 1
                            if not self.dry_run:
                                self.sent_history.add(article.link)
                                if getattr(analysis, "is_lineup", False) and getattr(analysis, "lineup_team", None):
                                    self.lineup_history.add(analysis.lineup_team)

                        # Webhook送信インターバル (API負荷軽減)
                        time.sleep(1)
                    except Exception as article_err:
                        print(f"⚠️ [SoccerNewsBot] 記事個別の処理中にエラー発生 ({article.original_title}): {article_err}")
                        continue

            print(f"\n✨ 処理完了: 新着記事 {total_new} 件中 {total_sent} 件を配信しました。")
        except Exception as fatal_err:
            print(f"💥 [SoccerNewsBot] ニュース巡回中に致命的エラー発生: {fatal_err}")
            DiscordNotifier.send_system_log("ニュース巡回・配信処理中に致命的エラーが発生しました。", error=fatal_err)
            raise fatal_err

    def run_schedule(self):
        """直近1週間のサッカー試合スケジュールを作成してDiscordに配信する"""
        print(f"\n🗓️ [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 直近1週間のサッカー試合スケジュールを作成・配信します...")
        try:
            # 3秒待機（Gemini APIペーシング）
            request_delay = float(os.getenv("GEMINI_REQUEST_DELAY_SECONDS", "3.0"))
            print(f"⏳ [Pacing] Gemini API負荷制御のため {request_delay:.1f} 秒待機中...")
            time.sleep(request_delay)

            schedule_data = self.gemini.generate_schedule()
            if not schedule_data:
                print("❌ [SoccerNewsBot] スケジュール情報の生成に失敗しました。")
                DiscordNotifier.send_system_log("試合スケジュールの生成に失敗しました（Gemini API応答なし）。")
                return

            formatted_msg = format_schedule_message(schedule_data)
            
            print(f"\n──────────────────────────────────────────────────")
            preview = formatted_msg[:600] + ("\n... (省略) ..." if len(formatted_msg) > 600 else "")
            print(preview)

            if self.dry_run:
                print("💡 [DRY-RUN] Discordスケジュール配信をスキップしました。")
                return

            webhook_url = os.getenv("WEBHOOK_SCHEDULE", "").strip()
            if not webhook_url:
                print("⚠️ [SoccerNewsBot] WEBHOOK_SCHEDULE が設定されていません。.env を確認してください。")
                return

            send_discord_chunks(webhook_url, formatted_msg)
        except Exception as schedule_err:
            print(f"💥 [SoccerNewsBot] スケジュール配信中に致命的エラー発生: {schedule_err}")
            DiscordNotifier.send_system_log("試合スケジュール配信中に致命的エラーが発生しました。", error=schedule_err)
            raise schedule_err

    def run_loop(self, interval_minutes: int = 15):
        print(f"🔄 定期実行モードで動作を開始します (巡回インターバル: {interval_minutes}分)")
        try:
            while True:
                try:
                    self.run_once()
                except Exception as loop_err:
                    print(f"⚠️ [SoccerNewsBot] 巡回回次で例外が発生しましたが、次回定期巡回まで待機を継続します: {loop_err}")
                print(f"💤 {interval_minutes}分間待機します...")
                time.sleep(interval_minutes * 60)
        except KeyboardInterrupt:
            print("\n👋 ユーザー操作により定期実行を終了しました。")


def main():
    try:
        parser = argparse.ArgumentParser(description="海外サッカー RSS Gemini 翻訳・自動Discord配信 Bot")
        parser.add_argument("--once", action="store_true", help="1回だけ実行して終了する")
        parser.add_argument("--loop", action="store_true", help="指定されたインターバルで定期ループ実行する")
        parser.add_argument("--schedule", action="store_true", help="直近1週間のサッカー試合スケジュールを作成・配信する")
        parser.add_argument("--dry-run", action="store_true", help="Discordへ送信せず、履歴も更新しない動作確認モード")
        parser.add_argument("--limit", type=int, default=5, help="1フィードあたりに取得する最大記事数")
        parser.add_argument("--interval", type=int, default=None, help="定期実行時の分単位の待機時間")

        args = parser.parse_args()

        # 環境変数からのデフォルトインターバル取得
        interval = args.interval or int(os.getenv("FETCH_INTERVAL_MINUTES", "15"))

        bot = SoccerNewsBot(dry_run=args.dry_run, max_per_feed=args.limit)

        if args.schedule:
            bot.run_schedule()
        elif args.loop:
            bot.run_loop(interval_minutes=interval)
        else:
            # デフォルトはonce実行
            bot.run_once()
    except Exception as main_err:
        print(f"💥 [main] メインプログラムで致命的例外が発生しました: {main_err}")
        DiscordNotifier.send_system_log("メインプログラムで致命的例外が発生しました。", error=main_err)
        sys.exit(1)

if __name__ == "__main__":
    main()
