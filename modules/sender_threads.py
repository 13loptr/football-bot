import os
import json
import time
import random
import requests

BUFFER_FILE = os.path.join(os.path.dirname(__file__), '..', 'data', 'buffer_threads_regal.json')
HISTORY_FILE = os.path.join(os.path.dirname(__file__), '..', 'data', 'history_threads_regal.json')

GENRE_HASHTAGS = {
    "transfers": "#移籍情報 #サッカー移籍",
    "japanese": "#日本人選手 #サッカー日本代表",
    "national": "#サッカー各国代表",
    "laliga": "#ラ・リーガ #レアルマドリード",
    "premier": "#プレミアリーグ",
    "bundesliga": "#ブンデスリーガ",
    "serie_a": "#セリエA",
    "ligue_1": "#リーグアン",
    "general": "#海外サッカー #サッカーニュース"
}

def load_json_list(filepath):
    dirname = os.path.dirname(filepath)
    if not os.path.exists(dirname):
        os.makedirs(dirname, exist_ok=True)
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except: pass
    return []

def save_json_list(filepath, data):
    dirname = os.path.dirname(filepath)
    if not os.path.exists(dirname):
        os.makedirs(dirname, exist_ok=True)
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# 💡 修正: url引数を追加
def add_to_buffer(title_ja, summary_ja, source_name, genre, url):
    history = load_json_list(HISTORY_FILE)
    
    # 💡 修正: URLとタイトルの両方でブロック（移行期の安全策）
    if url in history or title_ja in history:
        return 

    buffer = load_json_list(BUFFER_FILE)
    # 💡 修正: バッファ内もURLで重複チェック
    if not any(item.get('url') == url or item['title'] == title_ja for item in buffer):
        buffer.append({
            "title": title_ja,
            "summary": summary_ja,
            "source": source_name,
            "genre": genre,
            "url": url # 💡 修正: バッファにもURLを保持
        })
        save_json_list(BUFFER_FILE, buffer)

def process_threads_buffer(max_posts=3):
    user_id = os.getenv("THREADS_USER_ID")
    access_token = os.getenv("THREADS_ACCESS_TOKEN")
    if not user_id or not access_token: return

    buffer = load_json_list(BUFFER_FILE)
    history = load_json_list(HISTORY_FILE)
    
    posted_count = 0
    while buffer and posted_count < max_posts:
        item = buffer.pop(0)
        
        genre_key = item.get('genre', 'general')
        hashtag = GENRE_HASHTAGS.get(genre_key, "#海外サッカー")
        
        text = f"{hashtag}\n{item['title']}\n\n{item['summary']}\n\nソース: {item['source']}"
        text = text.replace("【", "").replace("】", "")[:495]

        if posted_count > 0:
            time.sleep(random.randint(90, 180))

        try:
            api_url = f"https://graph.threads.net/v1.0/{user_id}"
            res1 = requests.post(f"{api_url}/threads", data={"media_type": "TEXT", "text": text, "access_token": access_token}, timeout=15)
            if res1.status_code == 200:
                creation_id = res1.json().get("id")
                res2 = requests.post(f"{api_url}/threads_publish", data={"creation_id": creation_id, "access_token": access_token}, timeout=15)
                if res2.status_code == 200:
                    
                    # 💡 修正: 日本語タイトルではなくURLを優先して保存する
                    saved_key = item.get('url', item['title'])
                    if saved_key n