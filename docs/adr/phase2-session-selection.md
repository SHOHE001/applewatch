# ADR-001: Phase 2 セッション選択方式

- Status: Superseded by ADR-002 (2026-07-14)
- Date: 2026-07-13
- Related: #4 (parent: #2, blocks: #5)

> **Superseded (2026-07-14)**: 本 ADR は `claude -p -c` によるヘッドレス発火を前提としていたが、
> Phase 2 の方針が「tmux 上で走る対話モード claude セッションを直接操作する」に転換したため、
> 発火手段（`-p` → tmux send-keys + JSONL tail）を [ADR-002](./phase2-interactive-session-control.md) で置き換える。
> ただし本 ADR の中核である「Discord チャンネル ↔ 対象の紐付けでセッションを特定する」という選択方式は
> ADR-002 に継承される（紐付け先が `project_dir`（-p の cwd）から `tmux target`（send-keys 宛先）に変わるのみ）。

## Context

Phase 2 で「Apple Watch → Discord bot / Webhook から Claude Code の既存セッションに介入する」機能を実装するにあたり、`~/.claude/projects/<project-hash>/*.jsonl` のどのセッションを継続対象にするかの決定ロジックを、実装 (P2-b, #5) の前に確定する必要がある。

Phase 1 (#1) は `claude -p <prompt>` で毎回フレッシュな新セッションを開くだけで、既存セッションの文脈は引き継がない。gen8 では複数プロジェクトを並行して触るため、Watch 側から「どのプロジェクトのどのセッション」を特定する導線が要る。

前提となる Claude Code CLI のフラグ:

- `-c`: 直近セッション継続
- `-r <session-id>`: 特定セッション再開

## Decision

**方式 1: Discord チャンネル ↔ プロジェクト紐付け方式** を採用する。

- プロジェクトごとに専用 Discord チャンネルを作る（例: `#claude-applewatch`, `#claude-foo`）
- bot は `channel_id → project_dir` の対応表を設定ファイル（`config.toml` 等）で持つ
- bot は着弾チャンネルを見て `cd <project_dir> && claude -p -c <prompt>` で発火

ハイブリッド（チャンネル指定があればそれ、なければ mtime 最新に fallback）は採らない。理由は下記「Rationale」参照。

## Considered Options

### 方式 1: Discord チャンネル ↔ プロジェクト紐付け

チャンネル ID → 作業ディレクトリを対応表として持ち、bot は着弾チャンネルに応じて `cd <project_dir> && claude -p -c` で発火。

### 方式 2: prompt prefix

共通チャンネル 1 つに投げ、`@applewatch <質問>` のような prefix でプロジェクトを指定。bot は先頭トークンをパースして対象プロジェクトを決定。

### 方式 3: 直近アクティブセッション追随

`~/.claude/projects/*/` 配下の JSONL を mtime 最新で自動選択して追記継続する。プロジェクト指定なし。

## Comparison

| 軸 | 方式 1（チャンネル） | 方式 2（prefix） | 方式 3（mtime 追随） |
|---|---|---|---|
| 設定・運用コスト | 中（プロジェクト追加でチャンネル + 対応表追記） | 中（対応表追記、チャンネルは増えない） | ゼロ |
| 誤爆リスク | 低（チャンネル境界で物理分離） | 中（prefix つけ忘れで別プロジェクトに刺さる） | 高（並行作業中の別プロジェクト mtime に引っ張られる） |
| Watch UX | 中（ショートカット複数、選択 1 タップ） | 悪（音声で prefix、Siri 誤認識に弱い） | 良（何も指定しない） |
| 実装難易度 | 低（map lookup のみ） | 中（パーサ + エラー処理） | 低（mtime 取得 + tie-breaker） |

## Rationale

- Watch から Claude を触るシーンは「移動中に、いま抱えてる案件をチクっと訊く」がほとんどで、対象プロジェクトは事前に決まっている。固定紐付けが最も自然
- iOS ショートカットを「applewatch プロジェクトへ質問」「foo プロジェクトへ質問」のように別々に用意すれば、Watch UX の負担はショートカット選択 1 タップに収まる。音声で prefix を毎回言うより確実
- 方式 3 の誤爆リスクが致命的。gen8 では並行作業で常に mtime が動くため、Watch から投げた質問が意図しないプロジェクトのセッションに追記される事故が起きやすい
- 方式 1 + 方式 3 のハイブリッド案（チャンネル無指定時に mtime へ fallback）も検討したが、Discord チャンネル追加漏れ時に暗黙で方式 3 が発動して事故りやすいため、まず方式 1 単独で始める

## Consequences

### Positive

- 誤爆リスクが最小
- 実装がシンプル（bot 内で dict lookup、環境変数化しやすい）
- 対応表を repo に置けば履歴管理でき、gen8 の並行プロジェクト事情に強い

### Negative

- 新プロジェクトを Watch 対応させるたびに Discord チャンネル作成 + 対応表エントリ追加の作業が必要
- Discord サーバー内のチャンネル数が増える（Apple Watch から触りたいプロジェクト数だけ）
- 対応表未登録のチャンネルにメッセージが来たときの handling を決める必要がある（無視 / エラー返信）

### Follow-ups

- P2-b (#5) で `channel_id → project_dir` 対応表の設定形式を決めて実装
- 対応表未登録チャンネルの handling をエラーメッセージ返信で実装（silent drop はデバッグしづらい）
- 将来的にプロジェクト数が Discord チャンネル管理の限界を超えたら方式 2（prefix）併用を再検討

## References

- Issue #4: 本 ADR の起票元
- Issue #2: Phase 2 親 Issue
- Issue #5: P2-b 実装（本 ADR に依存）
