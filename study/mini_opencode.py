"""
mini_opencode.py — OpenCode の動作を示す Python 擬似コード

2 つのループが入れ子になっている:
  [外側] ユーザーとの対話ループ  ← 新しいユーザー入力を待つ
  [内側] エージェントループ      ← ツール呼び出しが終わるまで繰り返す

OpenAI API への入出力は忠実に再現する。
それ以外（DB・権限・UI）はスタブ関数で代替。
"""

from __future__ import annotations
import json
from dataclasses import dataclass
from typing import Any

from openai import OpenAI  # pip install openai

client = OpenAI()  # OPENAI_API_KEY を環境変数から読み込み

# ---------------------------------------------------------------------------
# 型定義
# ---------------------------------------------------------------------------

@dataclass
class Message:
    """DB（SQLite / Drizzle ORM）に保存する 1 メッセージ"""
    role: str             # "system" | "user" | "assistant" | "tool"
    content: str | None
    tool_calls: list[dict] | None = None   # assistant が返したツール呼び出し
    tool_call_id: str    | None = None     # tool ロール用
    name: str            | None = None     # tool ロール用（ツール名）

# ---------------------------------------------------------------------------
# スタブ関数（何をするかだけ示す。中身は実装しない）
# ---------------------------------------------------------------------------

def load_config() -> dict:
    """opencode.json / ~/.config/opencode/config.json を読んで返す"""
    return {"model": "gpt-4o", "agent": "build", "max_steps": 50}

def build_system_prompt(model_id: str, cwd: str) -> list[str]:
    """
    システムプロンプトを複数ブロックのリストで返す。
    packages/opencode/src/session/system.ts が担う。

    返すブロック（順番に system ロールとして送る）:
      [0] ベースプロンプト — packages/opencode/src/session/prompt/gpt.txt
          モデルに合わせてファイルを切り替える（claude→anthropic.txt, gemini→gemini.txt）
          + エージェント固有プロンプト（opencode.json の agent.prompt があれば）
      [1] 実行環境情報
            "You are powered by the model named gpt-4o ..."
            "<env>
               Working directory: /home/user/project
               Is directory a git repo: yes
               Platform: linux
               Today's date: Thu May 14 2026
             </env>"
      [2] スキル一覧（.opencode/skills/ などから読み込んだ名前・説明）
    """
    base   = open(f"packages/opencode/src/session/prompt/gpt.txt").read()
    env    = (
        f"You are powered by the model named {model_id}.\n"
        f"<env>\n"
        f"  Working directory: {cwd}\n"
        f"  Is directory a git repo: yes\n"
        f"  Platform: linux\n"
        f"  Today's date: Thu May 14 2026\n"
        f"</env>"
    )
    skills = ("Skills provide specialized instructions. "
              "Use the skill tool to load one when relevant.")
    return [base, env, skills]

def get_tool_definitions() -> list[dict]:
    """
    有効なツールを JSON Schema 形式で返す。
    agent.permission / session.permission / ユーザー上書きで絞り込まれる。

    実際のツール一覧（packages/opencode/src/tool/registry.ts）:
      shell, read, write, edit, patch, glob, grep, fetch, search,
      task, todo, question, lsp, skill, plan
    """
    return [
        {
            "type": "function",
            "function": {
                "name": "shell",
                "description": "Execute a shell command in the working directory.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                    },
                    "required": ["command"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read",
                "description": "Read the contents of a file.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "write",
                "description": "Write content to a file.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path":    {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "edit",
                "description": "Replace an exact string in a file.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path":       {"type": "string"},
                        "old_string": {"type": "string"},
                        "new_string": {"type": "string"},
                    },
                    "required": ["path", "old_string", "new_string"],
                },
            },
        },
        # glob, grep, fetch, search, task, todo, question, lsp, skill, plan ...
    ]

def execute_tool(tool_name: str, tool_args: dict) -> str:
    """
    ツールを実際に実行して結果文字列を返す。
    packages/opencode/src/tool/ 以下の各ファイルが担う。
    """
    import subprocess
    if tool_name == "shell":
        r = subprocess.run(tool_args["command"], shell=True, capture_output=True, text=True)
        return r.stdout + r.stderr
    if tool_name == "read":
        return open(tool_args["path"]).read()
    if tool_name == "write":
        open(tool_args["path"], "w").write(tool_args["content"])
        return f"Written to {tool_args['path']}"
    if tool_name == "edit":
        c = open(tool_args["path"]).read()
        open(tool_args["path"], "w").write(
            c.replace(tool_args["old_string"], tool_args["new_string"], 1))
        return "Edit applied."
    return f"[Tool '{tool_name}' executed]"

def save_message_to_db(session_id: str, msg: Message) -> None:
    """SQLite（Drizzle ORM）にメッセージを永続化する"""

def load_messages_from_db(session_id: str) -> list[Message]:
    """SQLite からセッションの全メッセージを読み込む"""
    return []

def needs_compaction(messages: list[Message], model_id: str) -> bool:
    """トークン数がモデルのコンテキスト上限に近づいたか判定する"""
    return False

def compact_messages(session_id: str, messages: list[Message]) -> list[Message]:
    """
    compaction エージェントで古いメッセージを要約し置き換える。
    要約済み部分は次回 API 呼び出し時に "What did we do so far?" として渡される。
    """
    return messages

def notify_ui(event: str, data: dict) -> None:
    """TUI / Web UI にイベントをストリーミング配信する（SSE / WebSocket）"""

# ---------------------------------------------------------------------------
# メイン処理
# ---------------------------------------------------------------------------

def run_session(session_id: str) -> None:
    """
    ═══════════════════════════════════════
    【外側のループ】ユーザーとの対話ループ
    ═══════════════════════════════════════
    ユーザーが新しいメッセージを送るたびに agent_loop() を呼ぶ。
    実際は HTTP POST /session/{sessionID}/message で起動する。
    """
    cfg = load_config()
    print("OpenCode ready. Type your message (Ctrl-C to exit).\n")

    while True:
        try:
            user_text = input("You> ").strip()
        except KeyboardInterrupt:
            break
        if not user_text:
            continue

        user_msg = Message(role="user", content=user_text)
        save_message_to_db(session_id, user_msg)
        notify_ui("message.created", {"role": "user", "text": user_text})

        agent_loop(session_id, cfg)


def agent_loop(session_id: str, cfg: dict) -> None:
    """
    ═══════════════════════════════════════════════════
    【内側のループ】エージェントループ（trial-and-error）
    ═══════════════════════════════════════════════════
    1 回の反復 = "API を 1 回呼ぶ → ツールを実行 → 結果をDBへ保存"
    ツール呼び出しがなくなるか max_steps に達したら終了。

    実際は packages/opencode/src/session/prompt.ts の runLoop() が担う。
    """
    model_id  = cfg["model"]      # 例: "gpt-4o"
    max_steps = cfg["max_steps"]  # 例: 50
    tools     = get_tool_definitions()
    step      = 0

    while True:
        step += 1
        if step > max_steps:
            notify_ui("error", {"message": "Max steps reached"})
            break

        # ── コンテキスト上限チェック ──────────────────────────────────
        messages = load_messages_from_db(session_id)
        if needs_compaction(messages, model_id):
            messages = compact_messages(session_id, messages)

        # ── ★ OpenAI API に渡す messages を組み立てる ★ ───────────────
        #
        # system ブロック（各ブロックを別の {"role":"system"} として送る）:
        #   [0] ベースプロンプト（gpt.txt）+ エージェント固有プロンプト
        #   [1] 実行環境情報（cwd / git / platform / date）
        #   [2] スキル一覧
        #
        system_blocks = build_system_prompt(model_id, cwd="/home/user/project")
        api_messages: list[dict] = [
            {"role": "system", "content": b} for b in system_blocks
        ]

        # DB の履歴を OpenAI 形式に変換して追加
        #   user      → {"role":"user",      "content":"..."}
        #   assistant → {"role":"assistant", "content":"...", "tool_calls":[...]}
        #   tool      → {"role":"tool",      "tool_call_id":"...", "content":"..."}
        for msg in messages:
            entry: dict[str, Any] = {"role": msg.role}
            if msg.content      is not None: entry["content"]      = msg.content
            if msg.tool_calls   is not None: entry["tool_calls"]   = msg.tool_calls
            if msg.tool_call_id is not None: entry["tool_call_id"] = msg.tool_call_id
            if msg.name         is not None: entry["name"]         = msg.name
            api_messages.append(entry)

        # ── ★ OpenAI API 呼び出し ★ ──────────────────────────────────
        #
        # 【入力パラメータ】
        #   model         : "gpt-4o"
        #   messages      : 上で組み立てたリスト（system×N + user/assistant/tool の履歴）
        #   tools         : ツール定義（JSON Schema）のリスト
        #   tool_choice   : "auto"（通常）/ "required"（json_schema 強制出力モード）
        #   temperature   : 0.7（モデル・エージェント設定値。capabilities.temperature=false なら省略）
        #   max_tokens    : 8192（モデルの最大出力トークン数）
        #   stream        : True（チャンクで受け取る）
        #
        response = client.chat.completions.create(
            model       = model_id,
            messages    = api_messages,
            tools       = tools,
            tool_choice = "auto",
            temperature = 0.7,
            max_tokens  = 8192,
            stream      = True,
        )

        # ── ★ OpenAI API 出力の受信（ストリーミング） ★ ──────────────
        #
        # 【受信するイベント】
        #   delta.content         : テキストチャンク "I'll read the file..."
        #   delta.tool_calls[]
        #     .id                 : "call_abc123"
        #     .function.name      : "read"
        #     .function.arguments : '{"path": "README.md"}'  ← 複数チャンクで届く
        #   finish_reason         : "stop" | "tool_calls" | "length" | "content_filter"
        #
        accumulated_text = ""
        accumulated_tool_calls: dict[int, dict] = {}  # チャンクを index でマージ
        finish_reason = None

        for chunk in response:
            delta = chunk.choices[0].delta
            finish_reason = chunk.choices[0].finish_reason or finish_reason

            if delta.content:
                accumulated_text += delta.content
                notify_ui("text.delta", {"text": delta.content})

            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in accumulated_tool_calls:
                        accumulated_tool_calls[idx] = {
                            "id":       tc.id or "",
                            "type":     "function",
                            "function": {"name": tc.function.name or "", "arguments": ""},
                        }
                    if tc.id:                      accumulated_tool_calls[idx]["id"] = tc.id
                    if tc.function.name:           accumulated_tool_calls[idx]["function"]["name"] = tc.function.name
                    if tc.function.arguments:      accumulated_tool_calls[idx]["function"]["arguments"] += tc.function.arguments

        tool_calls_list = [accumulated_tool_calls[i] for i in sorted(accumulated_tool_calls)]

        # ── アシスタントメッセージを DB に保存 ────────────────────────
        assistant_msg = Message(
            role       = "assistant",
            content    = accumulated_text or None,
            tool_calls = tool_calls_list or None,
        )
        save_message_to_db(session_id, assistant_msg)
        notify_ui("message.created", {"role": "assistant", "text": accumulated_text})

        # ── ループ終了判定 ─────────────────────────────────────────────
        if finish_reason == "stop" and not tool_calls_list:
            # ツール呼び出しなし → モデルが回答完了と判断 → ループ終了
            print(f"\nAssistant> {accumulated_text}\n")
            break

        # ── ★ ツール実行 → 結果を DB に追加 → 次の反復へ ★ ──────────
        #
        # モデルが 1 ターンで複数ツールを並列呼び出すことがある。
        # 各結果を {"role":"tool"} として DB に追加し、次の反復で API に渡す。
        #
        for tc in tool_calls_list:
            tool_name    = tc["function"]["name"]
            tool_args    = json.loads(tc["function"]["arguments"])
            tool_call_id = tc["id"]

            notify_ui("tool.start", {"name": tool_name, "args": tool_args})

            # 権限チェック（permission.ask で都度ユーザー確認する場合もある）
            try:
                result = execute_tool(tool_name, tool_args)
            except Exception as e:
                result = f"[Error] {e}"

            notify_ui("tool.end", {"name": tool_name, "result": result[:200]})

            # ツール結果を DB に保存（次のループで api_messages に追加される）
            save_message_to_db(session_id, Message(
                role         = "tool",
                content      = result,
                tool_call_id = tool_call_id,
                name         = tool_name,
            ))

        # → while True の先頭に戻り、更新された履歴で API を再呼び出し


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uuid
    run_session(str(uuid.uuid4()))
