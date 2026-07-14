# ADR-002: Phase 2 対話セッション操作方式

- Status: Accepted
- Date: 2026-07-14
- Supersedes: [ADR-001](./phase2-session-selection.md)
- Related: #2 (parent), P2-1 / P2-2 / P2-3

## Context

Phase 1 (#1) と ADR-001 は `claude -p`（ヘッドレス単発）を前提にしていた。P2-b (#5) で `-c` / `-r` によるセッション継続も実装したが、いずれも「毎回プロセスを起動して 1 ターン回す」モデルであり、gen8 の tmux 上で人間が実際に使っている**対話モードの claude セッションそのもの**には介入できない。

Phase 2 の本来のゴールは「バイト中など gen8 の前に居ないときに、走っている対話セッションを Apple Watch → Discord から操作する」こと。ユーザー方針として **`-p` ヘッドレス路線はここでは使わない**と確定した（2026-07-14）。したがって発火手段を、対話セッションへの外部入力に切り替える。

この環境には既に先例がある:

- gloop の watcher (`loop-tmux-watcher.mjs`) が `tmux send-keys` で worker pane に `/clear` → `/gloop` を無人投入している（send-keys で対話 claude を操作できることは実証済み）
- gloop-watch (`loop-progress.mjs`) がセッション JSONL（`~/.claude/projects/<cwd由来hash>/*.jsonl`）を tail して整形表示している（構造化データとして応答を取り出す先例）

## Decision

**tmux send-keys 入力 + セッション JSONL tail 出力** を採用する。宛先の特定は ADR-001 の「Discord チャンネル ↔ 対象の紐付け」を継承する。

- **宛先**: `channel_id → { tmux_target, cwd }` の対応表（`claude-watch.toml` を拡張）。`tmux_target` は `session:window.pane`、`cwd` はそのセッションの作業ディレクトリ（JSONL パス特定用）。
- **入力**: チャンネル着弾メッセージを、対応する pane へ `tmux send-keys -t <target> -l <text>` + Enter で送る（`-l` はリテラル送信）。
- **出力**: `cwd` から `~/.claude/projects/<hash>/*.jsonl` を特定し tail。新しい assistant メッセージを同チャンネルへ投稿する（TUI 整形に依存しない）。
- **選択肢/プラン承認 (P2-2)**: JSONL で `AskUserQuestion` / `ExitPlanMode` を検知 → Discord に選択肢を投稿 → 返信（番号等）を send-keys で該当セッションへ送る。
- **push 通知 (P2-3)**: Claude Code の `Stop` / `Notification` hook で「応答完了・エラー・入力待ち」を検知し、Discord webhook へ通知（gloop の notify と同型）。

## Considered Options

### 方式 A: tmux send-keys + JSONL tail（採用）

入力は send-keys、出力はセッション JSONL の tail。実装が軽く、既存の gloop 資産（send-keys 投入・JSONL follow）を流用でき、応答が構造化データで取れる。

### 方式 B: tmux send-keys + capture-pane

出力を `tmux capture-pane`（画面バッファのキャプチャ）で取る。実装は最小だが、枠線・スピナー・プロンプト等の TUI 装飾が混ざり、Discord に返すテキストが読みにくい。差分検出も脆い。

### 方式 C: Agent SDK / Managed Agents 経由

Claude Code の SDK やマネージド経路で対話を制御し、承認 API を介入させる。最も健全だが設計変更が大きく「最短経路」に反する。将来の選択肢として保留。

## Rationale

- 入力の send-keys は gloop で実運用実績があり、リスクが低い
- 出力は JSONL tail が明確に優位。assistant メッセージが構造化データで確定するため、TUI 整形の除去や画面差分検出という脆い処理を避けられる。gloop-watch に先例があり流用できる
- 宛先のチャンネル紐付けは ADR-001 の誤爆最小・実装単純という利点をそのまま引き継げる（対応先の値が変わるだけ）

## Consequences

### Positive

- 走っている対話セッションに割り込める（Phase 2 の本来のゴールを満たす）
- 既存 bot（`bot.py` の Discord 接続・`channel_map`・応答チャンク分割）を骨格として流用でき、`run_claude` 呼び出し部分を send-keys + JSONL tail に差し替えるだけで中核が組める
- `-p` 資産（Phase 1・#5・webhook `/ask`）は消さず休眠。将来 webhook 経路を send-keys 送信に転用する余地も残る

### Negative

- 操作対象の対話セッションは人間が事前に tmux に立てておく前提（bot からのセッション新規起動は本 ADR のスコープ外）
- send-keys は「入力を投げる」だけで応答完了を直接は知れない。完了検知は JSONL / hook 側に依存する
- pane / session が閉じられた・再作成された場合の宛先の追従（`tmux_target` の陳腐化）を扱う必要がある

## PoC で潰す技術リスク

各 Issue の冒頭に検証タスクとして置く。「できる」と決めつけず、実挙動で確認してから実装に進む。

- `send-keys` で任意テキスト（改行・引用符・日本語）を安全に送れるか（複数行・特殊文字の扱い、`-l` の挙動）
- 選択肢回答: `AskUserQuestion` の TUI に番号 + Enter を送って正しく選択されるか
- 応答完了の判定: JSONL の assistant メッセージ確定 = 1 ターン完了、の検知と tail の取りこぼし防止
- 入力待ち通知: `Stop` hook が対話モードで期待どおり発火するか

## References

- ADR-001: 本 ADR が supersede する（チャンネル紐付けの選択方式は継承）
- Issue #2: Phase 2 親 Issue
- 先例: `~/.claude/skills/gloop/scripts/loop-tmux-watcher.mjs`（send-keys 投入）, `loop-progress.mjs`（JSONL tail）
