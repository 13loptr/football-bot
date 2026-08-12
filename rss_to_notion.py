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

def load_rss_urls():
    """feeds_config.jsonからRSSのURLリストを読み込む"""
    try:
        with open('feeds_config.json', 'r', encoding='utf-8') as f:
            feeds = json.load(f)
            return [feed['url'] for feed in feeds]
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
    """RSSのサマリーに含まれる邪魔なHTMLタグ（<p>や<a>など）を削除してプレーンテキストにする"""
    if not raw_html:
        return ""
    cleanr = re.compile('<.*?>')
    return re.sub(cleanr, '', raw_html).strip()

def translate_text(text, is_body=False):
    """Groq APIを使ってタイトルまたは本文を翻訳する"""
    if not GROQ_API_KEY or not text:
        return text

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    
    if is_body:
        # 本文（サマリー）用のプロンプト
        prompt = (
            f"あなたは欧州サッカー専門の翻訳家です。\n"
            f"以下の海外サッカーニュースの概要(サマリー)を、日本のファンが読みやすい自然な日本語に翻訳してください。\n"
            f"【絶対条件】\n"
            f"- サッカー専門用語は正しく訳すこと。\n"
            f"- 挨拶や「翻訳しました」などの余計な言葉は一切含めず、翻訳された本文のみを出力すること。\n\n"
            f"対象テキスト: {text}"
        )
    else:
        # タイトル用のプロンプト（文字数制限重視）
        prompt = (
            f"あなたは欧州サッカー専門の翻訳家です。\n"
            f"次の海外サッカーニュースのタイトルを、キャッチーな装飾や過度な意訳はせず、原文の意味をそのまま正確な日本語に翻訳してください。\n"
            f"サッカー専門用語（例: クリーンシート→無失点など）は正しく訳してください。\n\n"
            f"【絶対条件】\n"
            f"- X（旧Twitter）でURLと一緒に投稿しても文字数制限に引っかからない長さ（最大でも80文字程度）に収めること。\n"
            f"- 解説や挨拶、前置き、「翻訳結果：」などの余計な文字は一切出力せず、日本語のタイトル【本文のみ】を出力すること。\n\n"
            f"対象タイトル: {text}"
        )

    data = {
        "model": "llama3-8b-8192",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3
    }

    try:
        response = requests.post(url, headers=headers, json=data, timeout=10)
        response.raise_for_status()
        result = response.json()
        translated = result["choices"][0]["message"]["content"].strip()
        
        # タイトルの場合は不要な記号を削除
        if not is_body:
            return translated.replace('"', '').replace('「', '').replace('」', '')
        return translated
    except Exception as e:
        print(f"⚠️ Groq翻訳エラー: {e}")
        return text

def add_to_notion(title, url, pub_date, body_text):
    """Notionデータベースに新規レコードとページ内コンテンツを追加"""
    # Notionの1ブロックあたりの文字数上限エラーを防ぐための安全策（2000文字でカット）
    safe_body_text = body_text[:1999] if body_text else "（本文の要約が提供されていません）"

    notion.pages.create(
        parent={"database_id": DATABASE_ID},
        properties={
            "Title": {
                "title": [{"text": {"content": title}}]
            },
            "URL": {
                "url": url
            },
            "Date": {
                "date": {"start": pub_date}
            },
            "Status": {
                "status": {"name": "未投稿"}
            }
        },
        # ページを開いたときの中身（コンテンツ）に翻訳された本文を追加
        children=[
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [
                        {
                            "type": "text",
                            "text": {"content": safe_body_text}
                        }
                    ]
                }
            }
        ]
    )

def main():
    print("🚀 処理を開始します...")
    
    rss_urls = load_rss_urls()
    if not rss_urls:
        print("🛑 読み込むRSS URLがありませんでした。処理を終了します。")
        return

    existing_urls = get_existing_urls()
    print(f"📊 Notion側の既存データ: {len(existing_urls)}件")
    
    for rss_url in rss_urls:
        print(f"\n📡 フィード取得中: {rss_url}")
        feed = feedparser.parse(rss_url)
        print(f"✅ 取得できた記事数: {len(feed.entries)}件")
        
        for entry in feed.entries[:5]:  # 各フィードから最新5件を取得
            title = entry.title
            link = entry.link
            
            if link not in existing_urls:
                pub_date = datetime.now().isoformat()
                
                # RSSから本文（サマリー）を取得し、HTMLタグを綺麗に取り除く
                raw_summary = getattr(entry, 'summary', getattr(entry, 'description', ''))
                clean_summary = clean_html(raw_summary)
                
                # 翻訳の実行（タイトルと本文を別々に翻訳）
                print(f"🤖 タイトル翻訳中...")
                japanese_title = translate_text(title, is_body=False)
                
                japanese_body = ""
                if clean_summary:
                    print(f"🤖 本文(サマリー)翻訳中...")
                    # 長すぎる場合はAPIの負荷を下げるために最初の800文字程度に制限
                    japanese_body = translate_text(clean_summary[:800], is_body=True)
                
                add_to_notion(japanese_title, link, pub_date, japanese_body)
                print(f"🟢 追加しました: {japanese_title}")
            else:
                print(f"⚪ スキップ（保存済み）: {title[:30]}...")

if __name__ == "__main__":
    main()