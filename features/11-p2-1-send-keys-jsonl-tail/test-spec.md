# test-spec: #11 P2-1 send-keys 入力 + JSONL tail 応答返信

実装差分（`git diff 4d10945..HEAD`）と plan.md「テスト計画」を突き合わせた検証仕様。
テスト実体は `tests/test_p2_1_session.py`（T01–T26 + T06b）。実行系は pytest（`.venv`、`asyncio_mode=auto`）。

## カバレッジ対応（plan T-ID → 実装関数 → テスト）

| T-ID | 対象 | 検証内容 | 状態 |
|---|---|---|---|
| T01 | `project_dir_for_cwd` | 日本語 cwd → `-home-shohei--------applewatch`（実 projects dir 一致） | ✅ pass |
| T02 | `project_dir_for_cwd` | 非英数を全て `-`（`/a/b`→`-a-b`、境界） | ✅ pass |
| T03 | `latest_session_jsonl` | mtime 最新の jsonl を選ぶ | ✅ pass |
| T04 | `latest_session_jsonl` | dir 不在 / 空 → None（境界、例外なし） | ✅ pass |
| T05 | `send_prompt` | argv=`send-keys -l -- <text>` + `send-keys Enter` の 2 call、日本語/引用符リテラル | ✅ pass |
| T06 | `tmux_target_exists` | display-message rc=0+非空→True / rc!=0→False | ✅ pass |
| T06b | `tmux_target_exists` | **rc=0 かつ空 stdout→False**（no-tty で不在 target が rc=0 空を返す実機挙動への防御。plan PoC 追加検証） | ✅ pass |
| T07 | `wait_for_reply` | offset 後の end_turn(text) を返す | ✅ pass |
| T08 | `wait_for_reply` | tool_use→tool_result→end_turn を貫通、preamble+最終 text を `\n\n` 連結（意味論固定） | ✅ pass |
| T09 | `wait_for_reply` | end_turn 来ず timeout → TimeoutError（境界） | ✅ pass |
| T10 | `wait_for_reply` | offset 前の既存 end_turn は返さず timeout（stale 防止、境界） | ✅ pass |
| T11 | `SessionDriver.drive` | 正常系 → DriveResult(True, text, "")、send 呼ばれる | ✅ pass |
| T12 | `drive` | target 不在 → (False,"",err)、send 未呼び出し（境界） | ✅ pass |
| T13 | `drive` | jsonl None → (False,"",err)、send 未呼び出し（境界） | ✅ pass |
| T14 | `drive` | wait timeout → (False,"",err)（境界） | ✅ pass |
| T15 | `drive` | pane cwd 不一致 → (False,"",err)、send 未呼び出し（境界、別セッション誤返信防止） | ✅ pass |
| T16 | `bot.on_message` | map 済みチャンネル → drive が tmux_target/cwd で呼ばれ text を reply | ✅ pass |
| T17 | `bot.on_message` | 未 map / bot 発言 / 空文字 → drive 未呼び出し・reply なし（境界） | ✅ pass |
| T18 | `bot._respond` | drive 失敗 → `⚠️ {err}` 1 行 reply（silent drop しない） | ✅ pass |
| T19 | `bot._respond` | text>1900 → reply + channel.send 分割（`_split_message`） | ✅ pass |
| T20 | `load_channel_map` | 新スキーマ TOML → `{cid: SessionTarget}` | ✅ pass |
| T21 | `load_channel_map` | 旧 dir のみ（tmux_target 欠落）→ ValueError（移行例つき、境界） | ✅ pass |
| T22 | `load_channel_map` | `dir` エイリアス + tmux_target → dir を cwd 採用 | ✅ pass |
| T23 | `load_channel_map` | channel_id 重複 → ValueError（境界） | ✅ pass |
| T24 | env fallback | tmux_target 欠落 → ValueError（境界） | ✅ pass |
| T25 | env fallback | 完全な env → SessionTarget | ✅ pass |
| T26 | env fallback | TOML 存在時は TOML 優先（境界） | ✅ pass |

## 期待値乖離チェック

- plan の全 T-ID が実装テストに 1:1 対応。欠落なし。
- 実装差分で plan に無かった分岐 = `tmux_target_exists` の「rc=0 かつ空 stdout」ケース（no-tty 実機挙動）。T06b として追加済み。plan.md 設計方針にも実装ノートを反映。
- retain 対象（run_claude / webhook / `_split_message`）は不変で継続 pass。

## 実機確認が必要な項目（自動テスト対象外 → 完了条件（人間））

- gen8 tmux に実対話 claude を立て、Discord 経由の追加プロンプト → 応答返信の end-to-end。
- Apple Watch 経由（Discord 音声返信）の実操作。

（send-keys リテラル送信・JSONL の stop_reason=end_turn・no-tty 下の display-message 挙動は
実機で PoC 済みだが、「稼働中の対話セッションに割り込んで応答が返る」統合動作は人間の実機確認に委ねる。）
