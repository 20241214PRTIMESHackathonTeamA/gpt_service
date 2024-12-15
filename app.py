from functools import wraps
import os
from dotenv import load_dotenv
from flask_socketio import SocketIO, emit
from datetime import datetime, timezone, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from supabase import create_client, Client
import requests
from openai import OpenAI


# OpenAI APIキーを設定
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY)

# Notion APIトークン
NOTION_API_TOKEN = os.getenv("NOTION_API_TOKEN")
# 環境変数をロード
load_dotenv()

app = Flask(__name__)
socketio = SocketIO(app)


# Notion APIのヘッダー
NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_API_TOKEN}",
    "Notion-Version": "2022-06-28"
}

# 子要素のテキストを抽出する関数


# Notion APIヘッダー設定
HEADERS = {
    "Authorization": f"Bearer {NOTION_API_TOKEN}",
    "Notion-Version": "2022-06-28"
}

# Notionページと子要素を再帰的に取得


def fetch_notion_page_and_children(page_id, depth=0, max_depth=3):
    if not page_id or depth > max_depth:
        return None

    try:
        page_url = f"https://api.notion.com/v1/pages/{page_id}"
        children_url = f"https://api.notion.com/v1/blocks/{page_id}/children"

        page_response = requests.get(page_url, headers=HEADERS)
        page_data = page_response.json()

        children_response = requests.get(children_url, headers=HEADERS)
        children_data = children_response.json()

        # ページ情報
        page_info = {
            "id": page_data.get("id"),
            "title": page_data.get("properties", {}).get("title", {}).get("title", [{}])[0].get("plain_text", "No Title"),
            "url": page_data.get("url")
        }

        # 子要素を再帰的に取得
        children = []
        for child in children_data.get("results", []):
            if child["type"] == "child_page":
                children.append(fetch_notion_page_and_children(
                    child["id"], depth + 1, max_depth))
            elif child["type"] == "paragraph":
                children.append({
                    "id": child["id"],
                    "type": "paragraph",
                    "text": "".join(rt["plain_text"] for rt in child["paragraph"]["rich_text"])
                })

        return {"page": page_info, "children": children}

    except Exception as e:
        print(f"Error fetching Notion page: {e}")
        return None

# GPT-4 APIを呼び出す


def call_openai_gpt(prompt):
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "gpt-4",
        "messages": [
            {
                "role": "system",
                "content": "あなたはニュース編集者です。以下のデータを読み取り、ニュースバリューのあるトピックを複数見つけ、それぞれ短くタイトルを生成してください。ないことを付け加えないでください。"
            },
            {"role": "user", "content": prompt}
        ],
        "max_tokens": 500,
        "temperature": 0.7
    }

    try:
        response = requests.post(url, headers=headers, json=payload)
        response_data = response.json()
        print(response_data)
        if "choices" in response_data and response_data["choices"]:
            return response_data["choices"][0]["message"]["content"]
        return "ニューストピック生成に失敗しました。"
    except Exception as e:
        print(f"Error calling OpenAI API: {e}")
        return "APIエラーが発生しました。"

# テキストをすべて連結


def call_openai_gpt_judge(prompt, system):
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "gpt-4",
        "messages": [
            {
                "role": "system",
                "content": system
            },
            {"role": "user", "content": prompt}
        ],
        "max_tokens": 500,
        "temperature": 0.7
    }

    try:
        response = requests.post(url, headers=headers, json=payload)
        response_data = response.json()
        print(response_data)
        if "choices" in response_data and response_data["choices"]:
            return response_data["choices"][0]["message"]["content"]
        return "ニューストピック生成に失敗しました。"
    except Exception as e:
        print(f"Error calling OpenAI API: {e}")
        return "APIエラーが発生しました。"


def collect_all_text_content(notion_data):
    texts = []

    def traverse(data):
        if "page" in data and "title" in data["page"]:
            texts.append(data["page"]["title"])
        if "children" in data:
            for child in data["children"]:
                if isinstance(child, dict) and "text" in child:
                    texts.append(child["text"])
                elif isinstance(child, dict) and "children" in child:
                    traverse(child)

    traverse(notion_data)
    return "\n".join(texts)

# Flaskのエンドポイント


@app.route("/fetch-topics", methods=["GET"])
def fetch_topics():
    page_id = request.args.get(
        "pageId", "15c4358dfdad8070bf92c4dc2842ce3e")  # デフォルトのページID

    # Notionデータの取得
    notion_data = fetch_notion_page_and_children(page_id)
    if not notion_data:
        return jsonify({"error": "Notionデータの取得に失敗しました。"}), 500

    # 全てのテキストを連結
    all_text_content = collect_all_text_content(notion_data)

    # GPTを呼び出してニューストピックを生成
    news_topics = call_openai_gpt(all_text_content)

    # 結果をJSONで返却
    return jsonify({
        "newsTopics": [line for line in news_topics.split("\n") if line.strip()],
        "notionData": notion_data
    })


@app.route('/evaluate-title', methods=['POST'])
def evaluate_title():
    data = request.json
    title = data.get("title")

    if not title:
        return jsonify({"error": "タイトルが指定されていません。"}), 400

    prompt = f"タイトル「{title}」を評価してください。ニュースバリュー、公序良俗、法規制の観点で、それぞれの適切性を次のJSON形式で評価し、理由を説明してください: {{\"newsValue\": {{\"valid\": true/false, \"reason\": \"理由\"}}, \"publicDecency\": {{\"valid\": true/false, \"reason\": \"理由\"}}, \"legalCompliance\": {{\"valid\": true/false, \"reason\": \"理由\"}}}}"
    system_message = "あなたはニュース編集者です。以下のタイトルを評価してください。"
    gpt_response = call_openai_gpt_judge(prompt, system_message)

    if isinstance(gpt_response, dict) and "error" in gpt_response:
        return jsonify(gpt_response), 500

    return jsonify({"evaluation": gpt_response})


@app.route('/chat', methods=['POST'])
def chat():
    try:
        # リクエストボディを取得
        data = request.get_json()

        # 会話履歴の取得
        messages = data.get("messages", [])

        # 会話履歴がない場合のエラー処理
        if not messages:
            return jsonify({"error": "No messages provided"}), 400

        # OpenAI API を呼び出し
        response = client.chat.completions.create(model="gpt-4",
                                                  messages=messages)

        # アシスタントの応答を取得
        assistant_message = response.choices[0].message.content

        # 応答を会話履歴に追加
        messages.append({"role": "assistant", "content": assistant_message})

        # 応答として更新された会話履歴を返す
        return jsonify({"messages": messages})

    except Exception as e:
        return jsonify({"error": str(e)}), 500
