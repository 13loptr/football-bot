import os
import json
import time
import random
import requests

# 今後のマルチアカウント化を見据えて「アカウント名(regal)」をファイル名に含める
BUFFER_FILE = os.path.join(os.path.dirname(__file__), '..', 'data', 'buffer_threads_regal.json')
HISTORY_FILE = os.path.join(os.path.dirname(__file__), '..', 'data', 'history_threads_regal.json')

def load_json_list(filepath):
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except: pass
    return []

def save_json_list(filepath, data):
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def add_to_buffer(title_ja, summary_ja, source_name):
    history = set(load_json_list(HISTORY_FILE))
    if title_ja in history:
        return # 既にThreadsで処理済みならスキップ

    buffer = load_json_list(BUFFER_FILE)
    # バッファ内での重複も防ぐ
    if not any(item['title'] == title_ja for item in buffer):
        buffer.append({
            "title": title_ja,
            "summary": summary_ja,
            "source": source_name
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
        
        # 投稿テキストの組み立て（URL排除・安全フォーマット）
        text = f"⚽ニュース\n{item['title']}\n\n{item['summary']}\n\nソース: {item['source']}"
        text = text.replace("【", "").replace("】", "").replace("#", "")[:495]

        # 2件目以降はランダム待機（Bot判定回避）
        if posted_count > 0:
            time.sleep(random.randint(90, 180))

        try:
            api_url = f"https://graph.threads.net/v1.0/{user_id}"
            # 1. コンテナ作成
            res1 = requests.post(f"{api_url}/threads", data={"media_type": "TEXT", "text": text, "access_token": access_token}, timeout=15)
            if res1.status_code == 200:
                creation_id = res1.json().get("id")
                # 2. 公開
                res2 = requests.post(f"{api_url}/threads_publish", data={"creation_id": creation_id, "access_token": access_token}, timeout=15)
                if res2.status_code == 200:
                    history.add(item['title'])
                    posted_count += 1
                    print(f"✅ Threads投稿成功: {item['title'][:20]}...")
                else:
                    buffer.insert(0, item) # 失敗時はバッファに戻す
                    break
        except Exception as e:
            print(f"Threads投稿エラー: {e}")
            buffer.insert(0, item)
            break

    # 状態の保存
    save_json_list(BUFFER_FILE, buffer)
    save_json_list(HISTORY_FILE, list(history)[-3000:])