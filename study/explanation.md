# OpenCode リポジトリ解説

新規開発者向けに、このリポジトリの構造・技術スタック・開発フローを説明します。

---

## プロジェクト概要

**OpenCode** はオープンソースの AI コーディングエージェントです。ターミナル UI（TUI）、Web ブラウザ、デスクトップアプリの 3 つのインターフェースで動作し、20 以上の LLM プロバイダ（OpenAI、Anthropic、Google など）に対応しています。Claude Code の完全オープンソース代替として設計されており、プロバイダに依存しないアーキテクチャが特徴です。

- **ライセンス**: MIT
- **デフォルトブランチ**: `dev`
- **パッケージマネージャ**: [Bun](https://bun.sh/) 1.3+
- **モノレポ管理**: [Turborepo](https://turbo.build/)

---

## 技術スタック

| 分類 | 技術 |
|------|------|
| 言語 | TypeScript / JavaScript |
| ランタイム | Bun 1.3+（主）、Node.js 24+（補助） |
| UI フレームワーク | [SolidJS](https://www.solidjs.com/) |
| データベース | SQLite（[Drizzle ORM](https://orm.drizzle.team/)） |
| AI 統合 | [Vercel AI SDK](https://sdk.vercel.ai/)、[Model Context Protocol](https://modelcontextprotocol.io/) |
| インフラ | [SST](https://sst.dev/)（Cloudflare Workers ベース） |
| 関数型ライブラリ | [Effect.js](https://effect.website/) |
| ビルド | Turborepo + Vite |
| テスト | Bun test（ユニット）、Playwright（E2E） |

---

## ディレクトリ構造

```
opencode/
├── packages/          # モノレポのすべてのパッケージ（22 個）
├── sdks/              # SDK（JavaScript/TypeScript）
├── infra/             # インフラ・デプロイコード
├── specs/             # API 設計仕様書
├── .github/           # GitHub Actions ワークフロー
├── script/            # ビルド・ユーティリティスクリプト
├── nix/               # Nix 環境設定
├── patches/           # 依存パッケージへのパッチ
├── .opencode/         # OpenCode 自身の設定（エージェント定義など）
├── study/             # 学習・ドキュメント（本ファイルが置かれている場所）
├── README.md          # プロジェクト概要（24 言語対応）
├── CONTRIBUTING.md    # コントリビューションガイド
├── AGENTS.md          # コーディング規約・エージェント仕様
├── package.json       # ワークスペースルート設定
├── turbo.json         # Turborepo タスクパイプライン
├── tsconfig.json      # TypeScript 共通設定
├── bunfig.toml        # Bun 設定
└── sst.config.ts      # インフラ as コード設定
```

---

## 主要パッケージ詳解

### `packages/opencode` — メイン CLI アプリケーション

このリポジトリの中核パッケージです。TUI、HTTP サーバ、データベース、エージェントロジックがすべてここに含まれます。

```
packages/opencode/src/
├── agent/          # エージェントの実装・プロンプト定義
├── cli/cmd/        # CLI コマンド（tui, serve, web, debug など）
├── server/         # HTTP API サーバ（デフォルトポート: 4096）
├── session/        # セッション管理（アプリケーション状態の中心）
├── storage/        # SQLite データベース層（Drizzle ORM）
├── provider/       # LLM プロバイダ管理
├── mcp/            # Model Context Protocol 実装
├── lsp/            # Language Server Protocol 統合
├── git/            # Git 操作
├── permission/     # 権限管理システム
├── config/         # 設定管理
├── skill/          # 組み込みツール（ファイル編集・検索など）
├── plugin/         # プラグインシステム
├── pty/            # 疑似端末（ターミナル実行）
└── auth/           # 認証・認可
```

### `packages/core` — 共有ビジネスロジック

プロバイダ管理、認証、カタログ、ロギングなど、複数パッケージで共有される共通ロジックを提供します。

### `packages/app` — 共有 Web UI コンポーネント

SolidJS ベースの UI コンポーネント群。Vite でビルドされ、Web・デスクトップアプリから利用されます。

### `packages/web` — Web アプリ

ブラウザ向けのフロントエンド実装。

### `packages/desktop` — デスクトップアプリ

Electron ベースのデスクトップアプリ。Web UI をラップしてネイティブ機能を追加します。

### `packages/sdk` — JavaScript SDK

OpenAPI 仕様（`specs/` 以下）から自動生成されるクライアントライブラリです。HTTP API を型安全に呼び出せます。

### `packages/docs` — 公式ドキュメント

MDX 形式のドキュメントソース。クイックスタート、開発ガイドなどが含まれます。

### `packages/llm` — LLM 統合

LLM 関連のプロンプト管理・統合ロジック。詳細は `packages/llm/AGENTS.md` を参照してください。

---

## データベース設計

SQLite を使用し、Drizzle ORM で管理されます。マイグレーションファイルは `packages/opencode/migration/` に格納されています。

| テーブル | 役割 |
|---------|------|
| `Account` / `AccountState` | ユーザーアカウント情報 |
| `Project` | プロジェクト（作業ディレクトリ） |
| `Session` | AI との会話セッション |
| `Message` / `Part` | セッション内のメッセージ |
| `Todo` | タスク管理 |
| `Permission` | 操作権限の記録 |
| `SessionShare` | セッション共有情報 |
| `Workspace` | ワークスペース設定 |

---

## HTTP API

サーバはデフォルトで `http://localhost:4096` で起動します。OpenAPI 仕様は `packages/sdk/openapi.json` にあります。

主要エンドポイント（`specs/project.md` より）:

```
GET  /project                                            # プロジェクト一覧
POST /project/init                                       # プロジェクト初期化
GET  /project/:projectID/session                         # セッション一覧
POST /project/:projectID/session                         # セッション作成
GET  /project/:projectID/session/:sessionID/message      # メッセージ一覧
POST /project/:projectID/session/:sessionID/message      # メッセージ送信
```

---

## 組み込みエージェント

`.opencode/` ディレクトリでエージェントを定義できます。デフォルトで以下の 3 つが用意されています。

| エージェント | 説明 |
|-------------|------|
| `build` | フルアクセス権を持つデフォルトエージェント |
| `plan` | 読み取り専用の分析エージェント |
| `general` | `@general` で呼び出せる汎用サブエージェント |

---

## 開発環境のセットアップ

### 必要なツール

- **Bun** 1.3 以上（`curl -fsSL https://bun.sh/install | bash`）
- **Node.js** 24 以上（一部ツールで必要）
- **Git**

### インストールと起動

```bash
# リポジトリのクローン
git clone https://github.com/anomalyco/opencode
cd opencode

# 依存関係のインストール
bun install

# TUI で起動（packages/opencode ディレクトリで実行）
bun dev

# 特定のディレクトリで起動
bun dev /path/to/your/project

# ヘッドレス API サーバとして起動
bun dev serve

# Web インターフェースで起動
bun dev web
```

### デスクトップアプリの開発

```bash
bun run --cwd packages/desktop dev      # 開発サーバ起動
bun run --cwd packages/desktop build    # ビルド
bun run --cwd packages/desktop package  # パッケージング
```

---

## テストの実行

```bash
# 全テスト（JUnit XML 出力）
bun turbo test:ci

# E2E テスト
bun --cwd packages/app test:e2e:local

# HTTP API テスト
bun --cwd packages/opencode test:httpapi

# 型チェック
bun typecheck

# リント
bun lint
```

---

## CI/CD

`.github/workflows/` に以下のワークフローが定義されています。

| ファイル | 役割 |
|---------|------|
| `test.yml` | ユニット・E2E テスト（Linux / Windows） |
| `publish.yml` | ビルドと成果物の公開 |
| `typecheck.yml` | TypeScript 型検証 |
| `review.yml` | コードレビュー自動化 |
| `deploy.yml` | インフラデプロイ |

---

## コーディング規約（`AGENTS.md` より抜粋）

- ロジックはなるべく単一関数に集約する（再利用しない限り分割しない）
- `try`/`catch` を避ける → `.catch()` を使う
- `const` を優先する（`let` は最小限に）
- `else` を避ける → 早期リターン（early return）を使う
- 不要な分割代入を避ける
- Bun の API を積極的に使う（Node.js API より優先）

---

## コントリビューション

詳細は `CONTRIBUTING.md` を参照してください。PR を作成する際の要点:

1. 既存の Issue と紐付ける（`Fixes #123`）
2. コミットメッセージはコンベンショナルコミット形式（`feat:`, `fix:`, `docs:` など）
3. PR は小さくフォーカスを絞る
4. ロジック変更にはテストを追加する
5. UI 変更にはスクリーンショット・動画を添付する

---

## 参考ファイル

| ファイル | 内容 |
|---------|------|
| `README.md` | プロジェクト概要・インストール方法 |
| `CONTRIBUTING.md` | 開発参加ガイド |
| `AGENTS.md` | コーディング規約・エージェント仕様 |
| `packages/docs/` | 公式ドキュメントのソース |
| `specs/project.md` | HTTP API 仕様 |
| `packages/opencode/src/index.ts` | CLI エントリーポイント |
| `packages/opencode/AGENTS.md` | CLI エージェント詳細 |
| `packages/llm/AGENTS.md` | LLM 統合の詳細 |
