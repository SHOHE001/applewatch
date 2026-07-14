# ROADMAP

## Phase 1: foundation ✅

- Phase Milestone: `Phase 1`
- Goal: 基礎機能の実装

## Phase 2: interaction

- Phase Milestone: `Phase 2`
- Goal: gen8 の tmux 上で走る対話モード claude セッションを Discord から操作する。方式は tmux send-keys 入力 + セッション JSONL tail 出力 + チャンネル↔セッション紐付け（ADR-002）。`-p` ヘッドレス路線（Phase 1 / #5）は消さず休眠。
  - P2-1: 対話セッション操作の中核（channel↔tmux target 対応表・send-keys 入力・JSONL tail 応答返信）
  - P2-2: 選択肢回答/プラン承認（`AskUserQuestion` / `ExitPlanMode` を Discord から回答）
  - P2-3: push 通知（`Stop` / `Notification` hook で完了・エラー・入力待ちを検知して Discord へ）
  - 依存: P2-1 → P2-2 → P2-3

---

Phase の完了は `gh issue list --milestone "Phase N" --label "type:feature" --state open` が
0 件になったら `🚧 → ✅` に更新する（`loop-phase-close-check.mjs` が自動で行う）。
