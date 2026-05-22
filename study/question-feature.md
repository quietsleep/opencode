# フォローアップ質問機能（question ツール）

> **質問の要約**: ユーザーの意図が不明確な場合に、OpenCode はフォローアップ質問をするか？  
> その実現方法は？

---

## 結論

**あります。`question` ツールと、複数のプロンプト指示の組み合わせで実現されています。**

主な構成要素は 5 つ:

1. `question` ツール — モデルが呼び出す関数
2. システムプロンプト内の指示文 — 「不明確なら質問せよ」とモデルに伝える
3. `Question.Service` — 質問・回答のライフサイクルを管理する Effect サービス
4. 対話 UI コンポーネント — TUI で選択肢を表示
5. HTTP API + イベントバス — Web/外部クライアント向け

---

## 1. `question` ツールの定義

**ファイル**: `packages/opencode/src/tool/question.ts`

```typescript
export const Parameters = Schema.Struct({
  questions: Schema.mutable(Schema.Array(Question.Prompt))
    .annotate({ description: "Questions to ask" }),
})

// execute()
const answers = yield* question.ask({
  sessionID: ctx.sessionID,
  questions: params.questions,
  tool: ctx.callID ? { messageID: ctx.messageID, callID: ctx.callID } : undefined,
})

return {
  title: `Asked ${params.questions.length} question${params.questions.length > 1 ? "s" : ""}`,
  output: `User has answered your questions: ${formatted}. You can now continue with the user's answers in mind.`,
  metadata: { answers },
}
```

### パラメータの構造

各質問は以下のフィールドを持つ（`packages/opencode/src/question/index.ts`）:

| フィールド | 型 | 説明 |
|----------|---|------|
| `question` | string | 完全な質問文 |
| `header` | string | 短いラベル（最大 30 文字） |
| `options` | array | 選択肢の配列 |
| `options[].label` | string | 表示テキスト（1〜5 語） |
| `options[].description` | string | その選択肢の説明 |
| `multiple` | bool? | 複数選択を許可するか |
| `custom` | bool? | 自由入力を許可するか（デフォルト true） |

### ツールの description（モデルが読む説明）

**ファイル**: `packages/opencode/src/tool/question.txt`

```
Use this tool when you need to ask the user questions during execution.
This allows you to:
1. Gather user preferences or requirements
2. Clarify ambiguous instructions
3. Get decisions on implementation choices as you work
4. Offer choices to the user about what direction to take.

Usage notes:
- When `custom` is enabled (default), a "Type your own answer" option is
  added automatically; don't include "Other" or catch-all options
- Answers are returned as arrays of labels; set `multiple: true` to allow
  selecting more than one
- If you recommend a specific option, make that the first option in the
  list and add "(Recommended)" at the end of the label
```

---

## 2. システムプロンプト内の指示文

モデルに「いつ質問すべきか」を教える指示が複数のプロンプトファイルに散在しています。

### plan エージェント（`session/prompt/plan.txt`）

プランモードでは積極的に質問することが推奨されます。

```
Ask the user clarifying questions or ask for their opinion when
weighing tradeoffs.

**NOTE:** At any point in time through this workflow you should feel
free to ask the user questions or clarifications. Don't make large
assumptions about user intent. The goal is to present a well researched
plan to the user, and tie any loose ends before implementation begins.
```

### trinity モデル用（`session/prompt/trinity.txt:85`）

```
- When the user's request is vague, use the question tool to clarify
  before reading files or making changes.
```

明示的に「曖昧な場合はファイル読み取りや変更の前に `question` ツールを使え」と指示しています。

### GPT モデル用（`session/prompt/gpt.txt:13`）

```
- Do not add backward-compatibility code unless there is a concrete
  need ... if unclear, ask one short question instead of guessing.
```

「不明確なら推測せずに短い質問をせよ」という指示。

### Anthropic プランモード用（`session/prompt/plan-reminder-anthropic.txt`）

```
3. Use AskUserQuestion tool to clarify ambiguities in the user request
   up front.
...
3. Use AskUserQuestion to ask the users questions about trade offs.
```

Anthropic 系では `AskUserQuestion` という名称も使われる（同等の機能）。

---

## 3. ツールの登録と有効化条件

**ファイル**: `packages/opencode/src/tool/registry.ts`

```typescript
const questionEnabled =
  ["app", "cli", "desktop"].includes(flags.client) || flags.enableQuestionTool

// ...

builtin: [
  tool.invalid,
  ...(questionEnabled ? [tool.question] : []),
  // ... 他のツール
]
```

| 起動形態 | `question` ツールが使えるか |
|---------|--------------------------|
| `cli`（ターミナル） | ✅ 自動有効 |
| `app`（Web） | ✅ 自動有効 |
| `desktop`（Electron） | ✅ 自動有効 |
| `serve`（ヘッドレス API） | ❌ デフォルト無効 |
| 任意のクライアント | 環境変数 `OPENCODE_ENABLE_QUESTION_TOOL=1` で強制有効化 |

ヘッドレス API モードでは、ユーザー対話 UI が無いため、デフォルトでは無効化されています。

---

## 4. `Question.Service` — ライフサイクル管理

**ファイル**: `packages/opencode/src/question/index.ts`

サービスのインターフェース:

```typescript
export interface Interface {
  readonly ask:    (input) => Effect.Effect<ReadonlyArray<Answer>, RejectedError>
  readonly reply:  (input) => Effect.Effect<void>
  readonly reject: (requestID) => Effect.Effect<void>
  readonly list:   () => Effect.Effect<ReadonlyArray<Request>>
}
```

### 動作の仕組み（Deferred パターン）

```typescript
const ask = Effect.fn("Question.ask")(function* (input) {
  const id = QuestionID.ascending()
  // 解決待ちの Deferred（Promise 相当）を作る
  const deferred = yield* Deferred.make<ReadonlyArray<Answer>, RejectedError>()

  pending.set(id, { info, deferred })
  yield* bus.publish(Event.Asked, info)  // ★ UI へ通知

  // ユーザーが回答するまで待機（ブロッキング）
  return yield* Effect.ensuring(
    Deferred.await(deferred),
    Effect.sync(() => { pending.delete(id) }),
  )
})
```

**ポイント**:

- `ask()` は `Deferred` を作り、回答が返るまで **ブロック** する
- `bus.publish(Event.Asked, info)` で UI 側にイベントが流れる
- ユーザーが TUI で選択肢を選ぶと `reply()` が呼ばれ、`Deferred` が解決される
- ユーザーが質問を破棄すると `reject()` が呼ばれ、`RejectedError` が投げられる

これにより、モデル側の `execute()` は **何事もなかったかのように `answers` を受け取れる**。

### 公開イベント

| イベント名 | 発火タイミング |
|----------|-------------|
| `question.asked` | モデルが質問を投げかけたとき |
| `question.replied` | ユーザーが回答したとき |
| `question.rejected` | ユーザーが質問を破棄したとき |

---

## 5. 対話 UI コンポーネント

### TUI（ターミナル）

**ファイル**: `packages/opencode/src/cli/cmd/tui/routes/session/question.tsx`

機能:
- 複数質問はタブ式で 1 問ずつ表示
- 矢印キーで選択肢を移動
- 数字キー（1〜9）でショートカット選択
- 自由入力欄（`custom: true` のとき自動追加）
- 最後に「Confirm」タブで全回答をレビュー
- `Esc` で破棄、`Enter` で送信

### CLI 直接モード（`opencode run` 等）

**ファイル**: `packages/opencode/src/cli/cmd/run/footer.question.tsx`

非対話セッション向けの簡易版フッター UI。同じキー操作。

---

## 6. HTTP API（外部クライアント向け）

| メソッド・パス | 役割 |
|--------------|------|
| `GET /question` | 現在の保留中の質問一覧を取得 |
| `POST /question/{requestID}/reply` | 回答を送信 |
| `POST /question/{requestID}/reject` | 質問を破棄 |

Web UI やサードパーティ統合は、`question.asked` イベントを購読し、  
UI で回答を集めてから `reply` エンドポイントを叩く流れになります。

---

## 7. 実例：プランモード終了時の確認

**ファイル**: `packages/opencode/src/tool/plan.ts`

プランモードの終了時に `question` ツールが呼ばれます。

```typescript
const answers = yield* question.ask({
  sessionID: ctx.sessionID,
  questions: [{
    question: `Plan at ${plan} is complete. Would you like to switch to the build agent?`,
    header: "Build Agent",
    custom: false,
    options: [
      { label: "Yes", description: "Switch to build agent and start implementing" },
      { label: "No",  description: "Stay with plan agent to continue refining" },
    ],
  }],
  tool: ctx.callID ? { messageID: ctx.messageID, callID: ctx.callID } : undefined,
})
```

---

## エージェントループから見た流れ

```
┌─────────────────────────────────────────────────────────┐
│ 1. ユーザー：「ファイルを整理して」（曖昧）              │
└─────────────────────────────────────────────────────────┘
                       ↓
┌─────────────────────────────────────────────────────────┐
│ 2. モデル（プロンプト指示で）「曖昧 → question ツール」 │
│    tool_call: question(questions=[                      │
│      { question: "どのフォルダを整理しますか？",          │
│        options: [{label: "src/"}, {label: "tests/"}] }  │
│    ])                                                   │
└─────────────────────────────────────────────────────────┘
                       ↓
┌─────────────────────────────────────────────────────────┐
│ 3. tool.execute() → Question.Service.ask()              │
│    → Deferred 作成・bus.publish(question.asked)         │
│    → モデル側はここで待機（ブロック）                   │
└─────────────────────────────────────────────────────────┘
                       ↓
┌─────────────────────────────────────────────────────────┐
│ 4. TUI が選択肢を表示 → ユーザーが選択                  │
│    → POST /question/{id}/reply                          │
│    → Question.Service.reply() → Deferred 解決           │
└─────────────────────────────────────────────────────────┘
                       ↓
┌─────────────────────────────────────────────────────────┐
│ 5. tool 結果が返る:                                     │
│    "User has answered your questions:                   │
│     'どのフォルダを整理しますか？'='src/'"               │
│    → 通常どおりエージェントループ次ターンへ             │
└─────────────────────────────────────────────────────────┘
```

ツール結果は通常のツールと同じく `{"role": "tool", "content": "..."}` で  
次の `chat.completions.create()` 呼び出しに渡されます。  
**モデル側からはブロッキング API の戻り値として自然に扱えます。**

---

## まとめ

| 構成要素 | 役割 |
|---------|------|
| `question` ツール | モデルが呼ぶ関数。質問・選択肢・複数選択フラグなどを渡す |
| `question.txt`（ツール description） | モデルに「いつ・どう使うか」を説明 |
| プロンプト内の指示文 | 「曖昧なら推測せず質問せよ」とモデルに方針を与える |
| `Question.Service` | Deferred で「質問→回答」を非同期にブリッジ |
| TUI / CLI コンポーネント | 矢印キー・数字キー・自由入力の選択 UI |
| HTTP API + イベントバス | Web/外部クライアントの統合 |

OpenCode の質問機能は **「ツール（モデル側）」と「UI（ユーザー側）」を  
イベントバスと Deferred で疎結合に繋いだもの** であり、  
モデルからは普通のツール呼び出しとして使えるシンプルな設計になっています。

---

## 参照ファイル

| ファイル | 内容 |
|---------|------|
| `packages/opencode/src/tool/question.ts` | ツール定義（`Tool.define`） |
| `packages/opencode/src/tool/question.txt` | ツール description（モデル向け） |
| `packages/opencode/src/tool/registry.ts` | ツール登録・有効化条件 |
| `packages/opencode/src/question/index.ts` | `Question.Service`（Deferred 管理） |
| `packages/opencode/src/cli/cmd/tui/routes/session/question.tsx` | TUI コンポーネント |
| `packages/opencode/src/cli/cmd/run/footer.question.tsx` | CLI 直接モード UI |
| `packages/opencode/src/session/prompt/plan.txt` | プランモード指示文 |
| `packages/opencode/src/session/prompt/trinity.txt` | trinity モデル指示文 |
| `packages/opencode/src/session/prompt/gpt.txt` | GPT モデル指示文 |
| `packages/opencode/src/session/prompt/plan-reminder-anthropic.txt` | Anthropic プランモード指示文 |
| `packages/opencode/src/tool/plan.ts` | 質問機能の実使用例 |
