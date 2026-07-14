# Rejection log for #11

## round 1 (design) 裁定
- 採用: architect#1（pane cwd 検証を drive に追加）, architect#2/migration#2（DriveResult→NamedTuple, "Runner同型"記述削除）, architect#3（wait_for_reply 返信=ターン内全 text 連結に確定, T08 具体化）, architect#4/migration#5（load_channel_map/build_client before/after, 旧test対応表, error reply 新仕様固定）, migration#3（env fallback テスト T24-T26 追加）, migration#6（T19 欠番→連番化）
- 部分棄却: migration#1（旧 dir のみ→ValueError で起動不能）。**fail-fast 自体は保持**。理由: ADR-002 で発火が send-keys に変わり、tmux_target 無しではセッション操作が原理的に不可能。silent 推測（例: 適当な pane に送る）はより危険。構成的部分（actionable なエラー文言 + README migration snippet）は採用済み。単一ユーザー（gen8）プロジェクトで toml.example も同時更新されるため移行コストは限定的。

## STEP7 最終レビュー裁定（final, 3persona verdict=fail blocking=6）
Codex は advisory。findings を精読し以下を裁定:
- 採用（実バグ→ implementer 再 dispatch 1 回で修正、debug-spec.md FIX-1〜5）:
  - FIX-1 timeout 不正値 silent failure（全3persona high）
  - FIX-2 latest_session_jsonl stat 競合で handler クラッシュ（architect/contrarian）
  - FIX-3 同一 pane 並行 drive の入力交錯/誤帰属（architect high）→ per-target Lock
  - FIX-4 cwd 比較 symlink 未解決（contrarian/migration）→ realpath 比較
  - FIX-5 相対 cwd 破綻（migration high）→ 絶対パス必須検証
- 却下/Non-Goal 化（plan.md 追記済み）:
  - B: 1 ターン中の JSONL 切替 inode 追従 → 最小コア外。file 消失は FIX-2 で緩和
  - D: start_offset 不完全行途中 → idle セッション前提でカバー
修正後 76 テスト pass。STEP7 の再レビューは方針通り実施しない。
