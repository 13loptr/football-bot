import os
import json
import feedparser
from datetime import datetime
from notion_client import Client

# 環境変数の取得
NOTION_TOKEN = os.environ.get("NOTION_API_KEY")
DATABASE_ID = os.environ.get("NOTION_DATABASE_ID")

notion = Client(auth=NOTION_TOKEN)

def load_rss_urls():
    """feeds_config.jsonからRSSのURLリストを読み込む"""
    try:
        with open('feeds_config.json', 'r', encoding='utf-8') as f:
            feeds = json.load(f)
            # JSONの中から 'url' の部分だけを抽出してリストにする
            return [feed['url'] for feed in feeds]
    except Exception as e:
        print(f"⚠️ JSONファイルの読み込みエラー: {e}")
        return []

def get_existing_urls():
    """すでにNotionに保存済みのURLを取得して重複を防ぐ"""
    results = notion.databases.query(database_id=DATABASE_ID)
    existing_urls = set()
    for page in results.get("results", []):
        props = page.get("properties", {})
        url_prop = props.get("URL", {}).get("url")
        if url_prop:
            existing_urls.add(url_prop)
    return existing_urls

def add_to_notion(title, url, pub_date):
    """Notionデータベースに新規レコードを追加"""
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
        }
    )

def main():
    print("🚀 処理を開始します...")
    
    # JSONファイルからURLリストを取得
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
                add_to_notion(title, link, pub_date)
                print(f"🟢 追加しました: {title}")
            else:
                print(f"⚪ スキップ（保存済み）: {title}")

if __name__ == "__main__":
    main()