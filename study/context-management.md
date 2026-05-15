# コンテキスト管理：大量ツール呼び出し時の動作

> **質問の要約**: 100 ファイルを `read` ツールで読んだセッションでは、  
> コンパクションがない場合、毎ターン全メッセージを API に送っているのか？

---

## 結論から言うと

**基本的には YES — 毎ターン全履歴を送っている。ただし 3 つの仕組みで徐々に削られる。**

```
ターンごとの API 送信内容
┌─────────────────────────────────────────────────────┐
│ system × N     ベースプロンプト・環境情報・スキル     │ 毎回フル
├─────────────────────────────────────────────────────┤
│ user           "100 ファイルを読んで説明して"         │
│ assistant      [tool_call: read("file_001.py")]      │
│ tool           "# file_001.py の内容..."             │ ← 古くなると削られる
│ assistant      [tool_call: read("file_002.py")]      │
│ tool           "# file_002.py の内容..."             │
│   ...（100 ファイル分）...                           │
│ assistant      "解析完了。以下がレポジトリの..."      │
└─────────────────────────────────────────────────────┘
```

100 ファイル分のツール結果がそのまま蓄積されると、  
コンテキスト上限に達した時点で **自動コンパクション** が発動する。

---

## トークン計算の方法

**ファイル**: `packages/opencode/src/util/token.ts`

```typescript
const CHARS_PER_TOKEN = 4
export function estimate(input: string) {
  return Math.round((input || "").length / CHARS_PER_TOKEN)
}
```

JSON 文字列化したメッセージ全体を「4 文字 = 1 トークン」で割るだけの  
**粗い推定値**。各ターン終了後に API から返ってくる実際の使用トークン数も  
アシスタントメッセージに保存される。

---

## 3 つのコンテキスト削減メカニズム

### メカニズム 1：古いツール出力の段階的消去（プルーニング）

**ファイル**: `packages/opencode/src/session/compaction.ts`

コンパクションが走る際、まず古いツール結果から中身を消す。

```
定数:
  PRUNE_MINIMUM    = 20,000 トークン  ← この閾値を超えたら削り始める
  PRUNE_PROTECT    = 40,000 トークン  ← 最近のツール出力はこの量まで保護
```

**動作イメージ**:

```
[古い tool: "# file_001.py の内容..." (3000 chars)]
         ↓ プルーニング後
[古い tool: "[Old tool result content cleared]"]   ← 15 文字に圧縮

[最近の tool: "# file_099.py の内容..." (3000 chars)]  ← 保護されて残る
```

処理順序:
1. 履歴を新しい順にスキャン
2. 直近 2 ターン分のツール出力は保護
3. 40,000 トークン分を「保護ゾーン」として確保
4. それより古いツール出力の `.time.compacted` に現在時刻を記録
5. 次回の `toModelMessagesEffect()` でそのツール出力が `[Old tool result content cleared]` に置き換わる

---

### メカニズム 2：オーバーフロー検知とコンパクション発動

**ファイル**: `packages/opencode/src/session/overflow.ts`

各ターン終了後に `isOverflow()` でトークン数を確認する。

```typescript
// 使用可能なトークン数 = モデルの入力上限 - 安全マージン(20,000)
function usable(model, cfg) {
  const reserved = cfg.compaction?.reserved ?? Math.min(20_000, maxOutputTokens(model))
  return model.limit.input
    ? model.limit.input - reserved
    : model.limit.context - maxOutputTokens(model)
}

// 実際の使用トークン数が usable() を超えたらコンパクション発動
function isOverflow({ tokens, model, cfg }) {
  if (cfg.compaction?.auto === false) return false  // 手動無効化可能
  const count = tokens.input + tokens.output + tokens.cache.read + tokens.cache.write
  return count >= usable({ cfg, model })
}
```

発動条件のイメージ（gpt-4o の場合）:
```
モデルのコンテキスト上限: 128,000 トークン
最大出力トークン:          16,384 トークン
安全マージン:              20,000 トークン
────────────────────────────────
使用可能上限:         128,000 - 20,000 = 108,000 トークン

→ 実際の使用量が 108,000 を超えたらコンパクション発動
```

---

### メカニズム 3：コンパクション（履歴の要約置き換え）

**ファイル**: `packages/opencode/src/session/compaction.ts`

コンパクションが発動するとどうなるか：

**ステップ 1**: `compaction` エージェントへの入力を組み立てる

ツール出力を 2,000 文字に切り詰めて要約用プロンプトに渡す。
```typescript
const TOOL_OUTPUT_MAX_CHARS = 2_000  // コンパクション入力のツール出力上限
```

```
要約エージェントへの入力:
  前回の要約（あれば）
  + 要約対象のメッセージ履歴（ツール出力は 2,000 文字に切り詰め）
  + "以下を Markdown 形式で要約せよ" 指示
```

**ステップ 2**: 要約エージェントが Markdown 要約を生成する

```markdown
## 実行済みの作業
- file_001.py ～ file_050.py を読み取り済み
- MyClass は db/models.py で定義されている
- エントリポイントは main.py の run() 関数
...
```

**ステップ 3**: 要約を `CompactionPart` としてメッセージに記録する

```typescript
// DB に保存される CompactionPart
{
  type: "compaction",
  summary: "## 実行済みの作業\n...",  // 要約テキスト
  tail_start_id: "msg_0099",          // ここ以降は「生のまま」保持
}
```

`tail_start_id` = コンパクション後も生のまま保持するメッセージの先頭 ID。  
直近 2 ターン（デフォルト）がそのまま残る。

---

## filterCompacted：毎ターンの前処理

**ファイル**: `packages/opencode/src/session/message-v2.ts`

各エージェントループの先頭で `filterCompactedEffect()` が走る。

```typescript
// prompt.ts — ループ先頭
let msgs = yield* MessageV2.filterCompactedEffect(sessionID)
```

コンパクション済みセッションでの並び替え:

```
DB に保存されている全メッセージ:
  [user_01][assistant_01][tool_01]...[user_50][compaction_marker][summary]
  [user_51][assistant_51]...[user_100]

filterCompacted() 後（API に渡す順序）:
  [compaction_marker + "What did we do so far?"]  ← 先頭に
  [summary（アシスタントの要約テキスト）]
  [user_99][assistant_99]...[user_100]             ← 直近 2 ターン
  （user_01 ～ user_98 は除外）                    ← 送らない
```

`toModelMessagesEffect()` での変換:

| DB のパーツ | API に渡されるテキスト |
|------------|----------------------|
| `compaction` パーツ | `"What did we do so far?"` |
| 通常の `tool` パーツ（プルーニング済み） | `"[Old tool result content cleared]"` |
| 通常の `tool` パーツ（生） | 実際のツール出力テキスト |
| `text` パーツ | そのままのテキスト |

---

## 100 ファイル読み取りの実際の流れ

```
ファイル 1～30 を read
  → 通常どおり全履歴を送信
  → トークン使用: ~30,000

ファイル 31～60 を read
  → プルーニング開始（古いツール出力を "[cleared]" に）
  → トークン使用: ~50,000（プルーニングなしなら ~60,000）

ファイル 61～90 を read
  → プルーニングがさらに進む
  → トークン使用: ~70,000

ファイル 91～100 を read
  → コンパクション発動（108,000 トークン閾値に近づく）
  → compaction エージェントが「ファイル 1～90 の解析結果」を Markdown 要約
  → 次ターンから API には [要約 + 直近 2 ターン] だけ送る
  → トークン使用: ~15,000（リセット）

続きの作業（説明 Markdown 生成など）
  → コンパクション後の小さなコンテキストで実行
```

---

## まとめ

| 状況 | API に送るもの |
|------|--------------|
| コンパクション前 | 全メッセージ（ただし古いツール出力は段階的に `[cleared]`） |
| コンパクション後 | コンパクションマーカー + 要約テキスト + 直近 2 ターン |
| 要約後のさらなる蓄積 | 上記 + 新しいメッセージが追加されていく |

OpenCode の設計思想は **「要約で圧縮する」** であり、  
「古いメッセージを切り捨てる」ではない。  
要約には過去の作業内容が Markdown で保持されるため、  
モデルはコンテキストを失わずに長い作業を継続できる。

---

## 参照ファイル

| ファイル | 内容 |
|---------|------|
| `packages/opencode/src/session/overflow.ts` | オーバーフロー検知ロジック |
| `packages/opencode/src/session/compaction.ts` | コンパクション・プルーニング実装 |
| `packages/opencode/src/session/message-v2.ts` | `filterCompacted` / `toModelMessagesEffect` |
| `packages/opencode/src/session/prompt.ts` | エージェントループ（`runLoop`） |
| `packages/opencode/src/util/token.ts` | トークン推定（4 文字 = 1 トークン） |
