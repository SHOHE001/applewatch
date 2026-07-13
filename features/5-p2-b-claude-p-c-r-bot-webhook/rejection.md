# Rejection log for #5

## design round 1 (2026-07-14)

- **[migration high] ClaudeWatchClient コンストラクタ互換 shim を残せ** → 棄却。内部利用のみ（`build_client()` / `server.py` 経由）の MVP で、Issue #5 が単一チャンネル → channel_map 方式への変更を明示要求している。public に外部利用者がいないため 1 リリース分の `target_channel_id` shim は YAGNI。運用上の移行パスは env fallback（`DISCORD_CHANNEL_ID`）で確保済み。既存 `test_bot.py` は channel_map ベースに書き換える。
- **[architect medium] load_channel_map を config モジュールに分離** → 部分採用/棄却。純粋関数化（Discord 非依存で env/file を読み dict を返す）は採用。config モジュール新設は YAGNI で棄却（設定はまだ小さい）。

## final round (STEP 7, 2026-07-14)

採用: TOML スキーマ検証不足（migration high/blocking + 型検証 medium ×3）→ `_parse_toml_channel_map` を fail-fast 強化、T19/T20/T21 追加。以下は棄却:

- **[architect medium] Runner 型エイリアスが呼び出し契約とズレ** → 棄却。実装は既に `Runner = Callable[..., Awaitable[tuple[int, str, str]]]`（`...` で任意 kwargs 許容）であり、指摘の前提（`Callable[[str], ...]`）が不正確。Protocol 化は YAGNI。
- **[contrarian low] debug 応答が構築済み argv を反映していない** → 棄却。debug 応答は mode/cwd/prompt で診断十分。argv 全文を含めると `-r <session_id>` の session_id がログに漏れる懸念があり、実装（mode/cwd/prompt）を維持。plan の文言も既に mode/cwd/prompt 期待に一致。
- **[architect low] README ツリーが新規ファイルとズレる可能性 / squash 前に git status 確認** → 運用注意として STEP 8 で対応（新規ファイルが commit に含まれることを確認）。
