import os
import json
import time
import random
import requests

BUFFER_FILE = os.path.join(os.path.dirname(__file__), '..', 'data', 'buffer_threads_regal.json')
HISTORY_FILE = os.path.join(os.path.dirname(__file__), '..', 'data', 'history_threads_regal.json')

# 💡 ジャンルに応じたハッシュタグの辞書を追加
GENRE_HASHTAGS = {
    "transfers": "🔁",
    "japanese": "🇯🇵",
    "national": "🌏",
    "laliga": "🇪🇸",
    "premier": "🏴󠁧󠁢󠁥󠁮󠁧󠁿",
    "bundesliga": "🇩🇪",
    "serie_a": "🇮🇹",
    "ligue_1": "🇫🇷",
    "general": "⚽️"
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

# 💡 genreを受け取れるように引数を追加
def add_to_buffer(title_ja, summary_ja, source_name, genre):
    history = set(load_json_list(HISTORY_FILE))
    if title_ja in history:
        return 

    buffer = load_json_list(BUFFER_FILE)
    if not any(item['title'] == title_ja for item in buffer):
        buffer.append({
            "title": title_ja,
            "summary": summary_ja,
            "source": source_name,
            "genre": genre # 💡 バッファにジャンルを保存
        })
        save_json_list(BUFFER_FILE, buffer)

def process_threads_buffer(max_posts=3):
    user_id = os.getenv("THREADS_USER_ID")
    access_token = os.getenv("THREADS_ACCESS_TOKEN")
    if not user_id or not access_token: return

    buffer = load_json_list(BUFFER_FILE)
    history = set(load_json_list(HISTORY_FILE))
    
    posted_count = 0
    while buffer and posted_count < max_posts:
        item = buffer.pop(0)
        
        # 💡 バッファからジャンルを取り出し、ハッシュタグに変換
        genre_key = item.get('genre', 'general')
        hashtag = GENRE_HASHTAGS.get(genre_key, "#海外サッカー")
        
        # 💡 タイトルの上にハッシュタグを配置
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
                    history.add(item['title'])
                    posted_count += 1
                    print(f"✅ Threads投稿成功: {item['title'][:20]}...")
                else:
                    print(f"❌ Threads公開エラー: {res2.text}")
                    buffer.insert(0, item) 
                    break
            else:
                print(f"❌ Threadsコンテナ作成エラー: {res1.text}")
                buffer.insert(0, item)
                break
        except Exception as e:
            print(f"Threads投稿エラー: {e}")
            buffer.insert(0, item)
            break

    save_json_list(BUFFER_FILE, buffer)
    save_json_list(HISTORY_FILE, list(history)[-3000:])