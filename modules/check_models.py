import requests

# ⚠️ 先ほど発行した新しいAPIキーをここに貼り付けてください
GROQ_API_KEY = "gsk_..."

url = "https://api.groq.com/openai/v1/models"
headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}

print("📡 利用可能なモデルをGroqに問い合わせ中...")
res = requests.get(url, headers=headers)

if res.status_code == 200:
    models = res.json().get("data", [])
    print("🟢 現在使えるモデル一覧:")
    for m in models:
        print(f"- {m['id']}")
else:
    print(f"❌ エラー: {res.text}")