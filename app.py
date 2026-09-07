import os
import json
import threading
from flask import Flask, jsonify
from modules.core_brain import fetch_rss_feeds, process_with_groq
from modules.sender_discord import send_to_discord
from modules.sender_threads import add_to_buffer, process_threads_buffer

app = Flask(__name__)

def run_news_cycle():
    """巡回・翻訳・送信を一元管理するメインロジック"""
    print("🚀 ニュース巡回サイクルを開始します...")
    
    # 1. フィード設定の読み込み
    try:
        with open('feeds_config.json', 'r', encoding='utf-8') as f:
            feeds = json.load(f)
            print(f"✅ feeds_config.json を読み込みました。対象サイト数: {len(feeds)}")
    except Exception as e:
        print(f"❌ feeds_config.json の読み込みに失敗しました: {e}")
        feeds = []

    # 2. RSS取得
    articles = fetch_rss_feeds(feeds, max_articles=5)
    print(f"📰 取得した記事の総数: {len(articles)} 件")
    
    # 3. 各記事をAI処理して分配
    for article in articles:
        print(f"🧠 AI処理開始: {article.title}")
        analysis = process_with_groq(article)
        
        if analysis.is_football:
            print(f"✅ サッカー記事判定・送信準備: {analysis.title_ja}")
            # Discordへ送信（内部で重複チェック）
            send_to_discord(article, analysis)
            
            # Threadsのバッファへ追加（内部で重複チェック）
            add_to_buffer(analysis.title_ja, analysis.summary_ja, article.source_name, analysis.genre, article.link)
        else:
            print(f"⏭️ スキップ (非サッカー判定 or APIキー未設定): {article.title}")

    # 4. Threadsのバッファから最大3件を安全に投稿
    print("📦 Threadsバッファの消化を開始します...")
    process_threads_buffer(max_posts=3)
    print("🏁 ニュース巡回サイクルが完了しました。")

@app.route('/')
def home():
    return "GOAT Soccer News Bot is active and waiting for triggers."

@app.route('/cron')
def cron_job():
    """UptimeRobotから定期的にアクセスされるエンドポイント"""
   