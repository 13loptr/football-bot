import os
import json
import feedparser
import requests
import re
from datetime import datetime
from notion_client import Client

# 環境変数の取得
NOTION_TOKEN = os.environ.get("NOTION_API_KEY")
DATABASE_ID = os.environ.get("NOTION_DATABASE_ID")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

notion = Client(auth=NOTION_TOKEN)

def load_feeds():
    """feeds_config.jsonからメディア名とURLのリストを読み込む"""
    try:
        with open('feeds_config.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"⚠️ JSONファイルの読み込みエラー: {e}")
        return []

def get_existing_urls():
    """すでにNotionに保存済みのURLを取得して重複を防ぐ"""
    results = notion.search(filter={"property": "object", "value": "page"})
    existing_urls = set()
    for page in results.get("results", []):
        props = page.get("properties", {})
        url_prop = props.get("URL", {}).get("url")
        if url_prop:
            existing_urls.add(url_prop)
    return existing_urls

def clean_html(raw_html):
    if not raw_html: return ""
    cleanr = re.compile('<.*?>')
    return re.sub(cleanr, '', raw_html).strip()

def translate_text(text, is_body=False):
    """Groq APIを使ってタイトルまたは本文を翻訳・要約する"""
    if not GROQ_API_KEY or not text: return text

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    
    if is_body:
        # 本文（サマリー）用のプロンプト（XのURL文字数制限を回避するため短めに）
        prompt = (
            f"あなたは欧州サッカー専門の翻訳家です。\n"
            f"以下の海外サッカーニュースの概要を、日本のファンが読みやすい自然な日本語に要約してください。\n"
            f"【絶対条件】\n"
            f"- XのURL制限エラーを防ぐため、最大でも【80文字〜100文字以内】の超短文に要約すること。\n"
            f"- サッカー専門用語は正しく訳すこと。\n"
            f"- 挨拶や「翻訳しました」などの余計な言葉は一切含めないこと。\n\n"
            f"対象テキスト: {text}"
        )
    else:
        # タイトル用のプロンプト
        prompt = (
            f"あなたは欧州サッカー専門の翻訳家です。\n"
            f"次の海外サッカーニュースのタイトルを、キャッチーな装飾や過度な意訳はせず、原文の意味をそのまま正確な日本語に翻訳してください。\n"
            f"サッカー専門用語は正しく訳すこと。\n\n"
            f"【絶対条件】\n"
            f"- 最大でも8