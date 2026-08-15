import os
import json
import requests
from modules.core_brain import ArticleItem, ArticleAnalysis

HISTORY_FILE = os.path.join(os.path.dirname(__file__), '..', 'data', 'history_discord.json')

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

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                return set(json.load(f))
        except: pass
    return set()

def save_history(history_set):
    os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(list(history_set)[-3000:], f, ensure_ascii=False, indent=2)

def send_to_discord(article: ArticleItem, analysis: ArticleAnalysis):
    history = load_history()
    if analysis.title_ja in history:
        return False
        
    safe_genre = analysis.genre.lower().strip() if getattr(analysis, "genre", None) else "general"
    if safe_genre == "transfer": safe_genre = "transfers"
    if safe_genre not in GENRE_CONFIG:
        safe_genre = "general"

    config = GENRE_CONFIG[safe_genre]
    
    webhook_url = os.getenv(config["env_var"])
    if not webhook_url:
        webhook_url = os.getenv("WEBHOOK_GENERAL")
    if not webhook_url: 
        return False

    is_lineup = getattr(analysis, "is_lineup", False)
    news_type_str = NEWS_TYPE_MAP.get(getattr(analysis, "news_type", "news"), "【ニュース】")

    if is_lineup:
        full_title = f"【🚨 スタメン速報】 {analysis.title_ja}"
        color = 0xE74C3C
        category_label = "🚨 スタメン速報"
        category_icon = "🚨"
    else:
        full_title = f"{news_type_str} {analysis.title_ja}"
        color = config["color"]
        category_label = config["label"]
        category_icon = config["icon"]

    primary_src = getattr(analysis, "primary_source", "") or "独自記事"
    source_display = f"{article.source_name} (引用: {primary_src})" if primary_src and primary_src not in ("独自記事", article.source_name) else f"独自記事（{article.source_name}）"

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
        if res.status_code in (200, 204):
            history.add(analysis.title_ja)
            save_history(history)
            return True
    except Exception as e:
        print(f"Discord送信エラー: {e}")
    return False