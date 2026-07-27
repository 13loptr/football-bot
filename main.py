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
2. 今週開催される全対戦カードから、特に注目すべき代表戦の大一番（日本代表の公式戦・強