import os
import json
import requests
from .core_brain import ArticleItem, ArticleAnalysis

HISTORY_FILE = os.path.join(os.path.dirname(__file__), '..', 'data', 'history_discord.json')

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                return set(json.load(f))
        except: pass
    return set()

def save_history(history_set):
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(list(history_set)[-3000:], f, ensure_ascii=False)

def send_to_discord(article: ArticleItem, analysis: ArticleAnalysis):
    history = load_history()
    # 日本語タイトルで重複チェック（RSSのURL変更に惑わされない）
    if analysis.title_ja in history:
        return False
        
    webhook_url = os.getenv("WEBHOOK_GENERAL") # 必要に応じてジャンル別Webhookを拡張可能
    if not webhook_url: return False

    embed = {
        "title": f"【ニュース】 {analysis.title_ja}",
        "url": article.link,
        "description": analysis.summary_ja,
        "color": 0x3498DB,
        "fields": [{"name": "📰 情報源", "value": article.source_name, "inline": True}]
    }
    
    try:
        res = requests.post(webhook_url, json={"embeds": [embed]}, timeout=10)
        if res.status_code in (200, 204):
            history.add(analysis.title_ja)
            save_history(history)
            return True
    except Exception as e:
        print(f"Discord送信エラー: {e}")
    return False