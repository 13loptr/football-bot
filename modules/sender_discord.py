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
    history = load_hi