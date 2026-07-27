# 海外サッカー RSS Gemini 自動翻訳・分類・Discord配信 Bot

海外サッカーの複数のRSSフィード（BBC, Sky Sports, SOCCER KING, FOOTBALL ZONE, フットボールチャンネル等）を巡回し、Google Gemini API (`google-genai` SDK) を使って自然な日本語への自動翻訳・自動要約・ジャンル自動判定を行い、対応するDiscordチャンネル（Webhook）へ配信するPythonスクリプトです。

---

## 🔥 主な機能

1. **複数RSSフィード巡回**:
   - 海外メディア（英語）および国内主要サッカーメディアの最新RSS記事を自動取得。
2. **送信済み記事の重複排除**:
   - `sent_history.json` で過去に配信した記事URLを保存・照合し、二重送信を完全防止。
3. **Gemini API（`google-genai` SDK）による翻訳 & 分類**:
   - 最新モデル `gemini-2.5-flash` の Structured Output を利用。
   - タイトルを日本のファンに分かりやすい表現へ意訳し、概要を2〜3文で要約。
   - 記事内容に応じて5つのジャンルに自動分類。
4. **Discord Webhook 自動振り分け配信**:
   - 5つのカテゴリに対応したDiscord Webhookへカラー埋め込みカード（Embed）として送信。
     - 🔄 **移籍情報・噂**: 移籍、契約、噂、監督人事
     - 🇯🇵 **日本人選手**: 海外日本人選手に関する報道・話題
     - 🇪🇸 **ラ・リーガ**: レアル・マドリード、バルセロナ等スペイン関連
     - 🏴󠁧󠁢󠁥󠁮󠁧󠁿 **プレミアリーグ**: マンC、アーセナル、リヴァプール等イングランド関連
     - ⚽ **総合ニュース**: CL/EL、代表戦、セリエA、ブンデス等全般
5. **柔軟な実行モード**:
   - 1回のみ実行 (`--once`)
   - 定期巡回ループ実行 (`--loop`)
   - 送信テスト用ドライラン (`--dry-run`)

---

## 📁 ディレクトリ構造

```
KICK/
├── .env                  # APIキーおよびWebhook URLの設定ファイル
├── .env.example          # 設定用テンプレート
├── requirements.txt      # 依存モジュール一覧
├── feeds_config.json     # 巡回対象のRSSフィード設定
├── sent_history.json     # 送信済み記事URLの保持ファイル（自動更新）
├── main.py               # メインプログラム
└── README.md             # マニュアル（本ファイル）
```

---

## 🛠️ セットアップ・使い方

### 1. 依存ライブラリのインストール

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 環境変数（`.env`）の設定

`.env.example` をコピーして `.env` を作成し、ご自身の **Gemini API Key** および **Discord Webhook URL** を設定してください。

```bash
cp .env.example .env
```

`.env` の記述例:
```env
# Google Gemini API Key
GEMINI_API_KEY=AIzaSy...

# Discord Webhook URLs (各チャンネルのWebhook URLを設定)
WEBHOOK_TRANSFERS=https://discord.com/api/webhooks/123.../abc...
WEBHOOK_JAPANESE=https://discord.com/api/webhooks/123.../def...
WEBHOOK_LALIGA=https://discord.com/api/webhooks/123.../ghi...
WEBHOOK_PREMIER=https://discord.com/api/webhooks/123.../jkl...
WEBHOOK_GENERAL=https://discord.com/api/webhooks/123.../mno...

# 巡回設定
FETCH_INTERVAL_MINUTES=15
MAX_ARTICLES_PER_FEED=5
```

---

## 🏃 実行コマンド

### 動作確認（ドライランモード）
Discordへの送信や送信履歴の更新を行わずに動作を確認します。
```bash
python3 main.py --dry-run
```

### 単発実行（1回のみ巡回・送信）
```bash
python3 main.py --once
```

### 常駐・定期巡回実行（デフォルト15分間隔）
```bash
python3 main.py --loop
```

※巡回インターバルを10分に変更する場合:
```bash
python3 main.py --loop --interval 10
```

---

## ⚙️ カスタマイズ

- **巡回フィードの追加・変更**:
  `feeds_config.json` を編集して、新しいRSS URLを追加できます。
- **取得件数の変更**:
  `main.py --limit 10` のように `--limit` オプションを指定するか、`.env` 内の `MAX_ARTICLES_PER_FEED` を調整してください。
