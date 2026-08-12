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
            f"- 最大でも80文字程度に収めること。\n"
            f"- 解説や挨拶などの余計な文字は一切出力せず、日本語のタイトル【本文のみ】を出力すること。\n\n"
            f"対象タイトル: {text}"
        )

    data = {
        "model": "llama3-8b-8192",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2
    }

    try:
        response = requests.post(url, headers=headers, json=data, timeout=10)
        response.raise_for_status()
        result = response.json()
        translated = result["choices"][0]["message"]["content"].strip()
        if not is_body:
            return translated.replace('"', '').replace('「', '').replace('」', '')
        return translated
    except Exception as e:
        print(f"⚠️ Groq翻訳エラー: {e}")
        return text

def add_to_notion(title, url, pub_date, summary, source_name):
    """Notionデータベースに新規レコード（タイトル、URL、要約、ソース）を追加"""
    notion.pages.create(
        parent={"database_id": DATABASE_ID},
        properties={
            "Title": {"title": [{"text": {"content": title}}]},
            "URL": {"url": url},
            "Date": {"date": {"start": pub_date}},
            "Status": {"status": {"name": "未投稿"}},
            "Summary": {"rich_text": [{"text": {"content": summary}}]}, # 追加した列
            "Source": {"rich_text": [{"text": {"content": source_name}}]} # 追加した列
        }
    )

def main():
    print("🚀 処理を開始します...")
    
    feeds = load_feeds()
    if not feeds:
        print("🛑 読み込むRSS URLがありませんでした。処理を終了します。")
        return

    existing_urls = get_existing_urls()
    print(f"📊 Notion側の既存データ: {len(existing_urls)}件")
    
    for feed_info in feeds:
        source_name = feed_info.get("name", "Unknown")
        rss_url = feed_info.get("url", "")
        
        print(f"\n📡 フィード取得中: [{source_name}] {rss_url}")
        feed = feedparser.parse(rss_url)
        print(f"✅ 取得できた記事数: {len(feed.entries)}件")
        
        for entry in feed.entries[:5]:
            title = entry.title
            link = entry.link
            
            if link not in existing_urls:
                pub_date = datetime.now().isoformat()
                
                raw_summary = getattr(entry, 'summary', getattr(entry, 'description', ''))
                clean_summary = clean_html(raw_summary)
                
                print(f"🤖 翻訳・要約中: {title[:30]}...")
                japanese_title = translate_text(title, is_body=False)
                japanese_body = translate_text(clean_summary[:800], is_body=True) if clean_summary else ""
                
                # 新しい列（Summary, Source）を含めてNotionへ送信
                add_to_notion(japanese_title, link, pub_date, japanese_body, source_name)
                print(f"🟢 追加しました: {japanese_title}")
            else:
                print(f"⚪ スキップ（保存済み）: {title[:30]}...")

if __name__ == "__main__":
    main()