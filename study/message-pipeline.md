# LLM へ送るメッセージの構築ロジック

OpenCode がユーザー入力を受け取ってから LLM API に何を送るか、そのパイプライン全体を解説します。

---

## 全体の流れ

```
HTTP POST /session/{id}/message
    ↓
SessionPrompt.prompt()          ← ユーザーメッセージをDBに保存
    ↓
loop() → runLoop()              ← エージェントループ（メイン）
    ↓
MessageV2.toModelMessagesEffect() ← DB上のメッセージをAI SDK形式に変換
    ↓
LLM.stream()                    ← システムプロンプト組み立て・API呼び出し
    ↓
streamText() [Vercel AI SDK]    ← プロバイダSDKへ渡す
    ↓
Anthropic / OpenAI / Google ... ← 実際のLLM API
    ↓
Processor.process()             ← ストリームを受信・ツール実行・DB保存
    ↓ (ツール呼び出しがあれば再度 runLoop)
```

---

## 1. HTTP リクエスト受付

**ファイル**: `packages/opencode/src/server/routes/instance/httpapi/groups/session.ts`

`POST /session/{sessionID}/message` が受け取るペイロード:

```typescript
{
  parts: [
    { type: "text", text: "README.md の内容を読んで" },
    // または
    { type: "file", url: "data:image/png;base64,...", mime: "image/png", filename: "image.png" },
  ],
  agent: "build",                            // 使うエージェント名
  model: { providerID: "anthropic", modelID: "claude-sonnet-4-5" },
  tools: { shell: false },                   // ツールの有効/無効上書き
  variant: "thinking",                       // モデルバリアント
  format: { type: "json_schema", schema: {} } // 出力フォーマット強制
}
```

---

## 2. ユーザーメッセージの保存

**ファイル**: `packages/opencode/src/session/prompt.ts`

`prompt()` 関数がユーザーメッセージを `MessageV2.User` として SQLite に保存します。

```typescript
// MessageV2.User の構造
{
  id: "msg_...",
  sessionID: "sess_...",
  role: "user",
  agent: "build",
  model: { providerID: "anthropic", modelID: "claude-sonnet-4-5" },
  parts: [
    { type: "text", text: "README.md の内容を読んで" }
    // ファイル添付があれば base64 data URI に変換して格納
  ]
}
```

---

## 3. エージェントループ（`runLoop`）

**ファイル**: `packages/opencode/src/session/prompt.ts`（約1631〜1859行）

`runLoop()` はツール呼び出しが終わるまで繰り返されるメインループです。1 回の反復で以下を並列取得します。

```typescript
const [skills, env, instructions, modelMsgs] = yield* Effect.all([
  sys.skills(agent),                        // 利用可能なスキル一覧
  sys.environment(model),                   // 実行環境情報
  instruction.system().pipe(Effect.orDie),  // システム指示文
  MessageV2.toModelMessagesEffect(msgs, model), // 変換済みメッセージ列
])
```

その後 `LLM.stream()` を呼び出し、レスポンスを `Processor.process()` で処理します。ツール呼び出しが返ってきた場合はツールを実行し、その結果を次のループに渡します。

---

## 4. システムプロンプトの組み立て

**ファイル**: `packages/opencode/src/session/system.ts` / `packages/opencode/src/session/llm.ts`

システムプロンプトは複数のブロックを結合して作られます。

### 4-1. プロバイダ固有のベースプロンプト

`packages/opencode/src/session/prompt/` 以下のテキストファイルが使われます:

| プロバイダ | ファイル |
|-----------|---------|
| Anthropic | `anthropic.txt` |
| OpenAI | `gpt.txt` |
| Google | `gemini.txt` |
| その他 | `default.txt` |

### 4-2. 実行環境情報（`sys.environment()`）

```
Model: claude-sonnet-4-5 (anthropic)
Working directory: /home/user/project
Workspace root: /home/user/project
Git: true (branch: main)
Platform: linux
Date: 2026-05-14
```

### 4-3. スキル一覧（`sys.skills()`）

`.opencode/skills/`、`~/.claude/skills/` などに置かれたスキルの名前・説明・場所が列挙されます。

### 4-4. エージェント固有プロンプト

エージェント設定（`opencode.json` 等）に `prompt` が定義されていれば追加されます。

### 4-5. プラグインによる追加

プラグインの `chat.params` フックでシステムプロンプトをさらに変換・追加できます。

**最終的なシステム配列のイメージ:**

```
system: [
  "[ベースプロンプト（anthropic.txt の内容）]",
  "[環境情報]",
  "[スキル一覧]",
  "[エージェント固有プロンプト]",   // あれば
  "[ユーザー指定の追加プロンプト]", // あれば
]
```

> **キャッシング**: 最初の 2 ブロックと末尾 2 メッセージにプロンプトキャッシュが適用されます。

---

## 5. メッセージ変換（DB → AI SDK 形式）

**ファイル**: `packages/opencode/src/session/message-v2.ts`（`toModelMessagesEffect`、約 630〜912 行）

SQLite に保存されたメッセージを Vercel AI SDK の `ModelMessage` 形式に変換します。

### ユーザーメッセージのマッピング

| DBのpart type | AI SDK 形式 |
|--------------|------------|
| `text` | `{ type: "text", text: "..." }` |
| `file` | `{ type: "file", url: "data:...", mediaType: "..." }` |
| `compaction` | `{ type: "text", text: "What did we do so far?" }` |
| `subtask` | `{ type: "text", text: "The following tool was executed" }` |

### アシスタントメッセージのマッピング

| DBのpart type | AI SDK 形式 |
|--------------|------------|
| `text` | `{ type: "text", text: "..." }` |
| `tool`（完了） | `{ type: "tool-call", toolName: "...", input: {...}, output: {...} }` |
| `tool`（エラー） | `{ type: "tool-call", state: "output-error", errorText: "..." }` |
| `tool`（中断） | `{ type: "tool-call", state: "output-error", errorText: "[interrupted]" }` |
| `reasoning` | `{ type: "reasoning", text: "..." }` |

変換後に AI SDK の `convertToModelMessages()` を呼んでプロバイダが解釈できる最終形式にします。

---

## 6. ツールの定義と絞り込み

**ファイル**: `packages/opencode/src/tool/registry.ts` / `packages/opencode/src/session/llm.ts`

### 組み込みツール一覧

| ツール名 | 機能 |
|---------|------|
| `shell` | シェルコマンドの実行 |
| `read` | ファイルの読み取り |
| `write` | ファイルの書き込み |
| `edit` | ファイルの編集（差分ベース） |
| `patch` | パッチの適用 |
| `glob` | ファイルの検索（パターン） |
| `grep` | テキスト検索 |
| `fetch` | Web ページの取得 |
| `search` | Web 検索 |
| `task` | サブエージェントの起動 |
| `todo` | Todo 操作 |
| `question` | ユーザーへの質問 |
| `lsp` | Language Server Protocol 操作 |
| `skill` | スキルの読み込み |
| `plan` | プランモードの切り替え |

### 権限による絞り込み

ツールは 3 層の権限設定で絞り込まれます:

1. `agent.permission`（エージェント設定）
2. `session.permission`（セッション設定）
3. ユーザーが送った `tools: { shell: false }` などの上書き

`Permission.disabled()` で無効化されたツールは `streamText()` に渡されません。

---

## 7. LLM への最終送信（`LLM.stream()`）

**ファイル**: `packages/opencode/src/session/llm.ts`

`streamText()` に渡される引数の全体像:

```typescript
streamText({
  // モデルインスタンス（キャッシュミドルウェア付き）
  model: wrapLanguageModel({
    model: language,       // プロバイダSDKのモデルインスタンス
    middleware: [cachingMiddleware, ...],
  }),

  // システムプロンプト（または messages に先頭ブロックとして挿入）
  // ※ OpenAI OAuth の場合は options.instructions に入る
  system: systemBlock,

  // 会話履歴（全ターン）
  messages: [
    { role: "user",      content: [{ type: "text", text: "README.md を読んで" }] },
    { role: "assistant", content: [
        { type: "text",      text: "読みます。" },
        { type: "tool-call", toolName: "read", input: { path: "README.md" }, id: "tc_1" }
    ]},
    { role: "user",      content: [
        { type: "tool-result", toolUseId: "tc_1", content: "# OpenCode\n..." }
    ]},
    // ... 以降のターンが続く
  ],

  // 有効なツール定義
  tools: {
    read:  { description: "...", parameters: zodSchema },
    shell: { description: "...", parameters: zodSchema },
    // ...
  },
  activeTools: ["read", "shell"],  // 実際に渡すツール名リスト
  toolChoice: "auto",              // json_schema モード時は "required"

  // 生成パラメータ
  temperature: 0.7,
  topP: 0.9,
  maxOutputTokens: 8192,
  providerOptions: { ... },  // プロバイダ固有オプション（thinking など）

  // その他
  headers: {
    "X-OpenCode-Session-ID": "...",
    "X-OpenCode-Model": "...",
  },
  maxRetries: 2,
  abortSignal: ...,
  experimental_telemetry: { ... },
})
```

---

## 8. プロバイダ固有の変換

**ファイル**: `packages/opencode/src/provider/transform.ts`

`streamText()` に渡す直前に、プロバイダごとに `normalizeMessages()` が適用されます。

| プロバイダ | 変換内容 |
|-----------|---------|
| Anthropic / Bedrock | 空のテキスト・reasoning パーツを除去 |
| Anthropic / Claude | ツール ID を英数字・`_`・`-` のみに正規化 |
| Mistral | ツール ID を 9 文字英数字に切り詰め |
| Anthropic / Google-Vertex | ツール呼び出しをテキストより前に並べ替え |
| Deepseek | 全アシスタントメッセージに空の reasoning パーツを追加 |
| 全プロバイダ | 不正な UTF-16 サロゲートを除去 |

---

## 9. ツール実行と再ループ

**ファイル**: `packages/opencode/src/session/prompt.ts`（`Processor.process()`）

ストリームから `tool-call` イベントが来たら:

1. 対応するツール（例: `read`）の `execute()` を呼び出す
2. 結果を `MessageV2.Part.Tool`（state: `completed`）としてDBに保存
3. `runLoop()` を再度呼び出してモデルに結果を渡す

ループは以下のいずれかで終了します:
- モデルがツールを呼ばずにテキストだけ返す
- `agent.steps`（最大ステップ数）に達する
- ユーザーが中断する

---

## 10. メッセージコンパクション

長いセッションでコンテキスト上限に近づくと、`compaction` エージェントが古いメッセージを要約します。要約済みメッセージは `compaction` パーツに置き換えられ、変換時に `"What did we do so far?"` という合成テキストとしてモデルに渡されます。

---

## まとめ：LLM API に実際に渡されるもの

```
┌─────────────────────────────────────────────────┐
│ System                                          │
│  [1] ベースプロンプト（anthropic.txt など）        │
│  [2] 実行環境情報（ディレクトリ・日付・Gitなど）    │
│  [3] スキル一覧                                  │
│  [4] エージェント固有プロンプト（あれば）           │
├─────────────────────────────────────────────────┤
│ Messages                                        │
│  user:      テキスト or ファイル(data URI)        │
│  assistant: テキスト + tool-call                │
│  user:      tool-result（ツール実行結果）         │
│  ... ← ループが続く限り繰り返す                   │
├─────────────────────────────────────────────────┤
│ Tools                                           │
│  read / write / edit / shell / grep / ...       │
│  （権限設定によって絞り込まれる）                  │
└─────────────────────────────────────────────────┘
```

---

## 参照ファイル

| ファイル | 役割 |
|---------|------|
| `packages/opencode/src/session/prompt.ts` | メインループ・メッセージ保存（約2000行） |
| `packages/opencode/src/session/llm.ts` | `streamText()` 呼び出し（約470行） |
| `packages/opencode/src/session/message-v2.ts` | DB→AI SDK変換（約950行） |
| `packages/opencode/src/session/system.ts` | システムプロンプト生成（約85行） |
| `packages/opencode/src/agent/agent.ts` | エージェント定義（約464行） |
| `packages/opencode/src/tool/registry.ts` | ツール登録（約270行） |
| `packages/opencode/src/provider/transform.ts` | プロバイダ固有変換（約1000行） |
| `packages/opencode/src/session/prompt/anthropic.txt` | Anthropic用ベースプロンプト |
