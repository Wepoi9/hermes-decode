# 検証記録（日付入りの実機・実測の記録）

現在の自動テストと CI が何を実行するかは `README.md` § 検証 が正です。このファイルは、その契約が実機と実接続でどう確認されたかの日付入りの記録です（環境依存の絶対値は載せません）。

## 実機 E2E（2026-10-16、3 回のプロブで順次確認。ローカル LLM は llama-swap 経由の llama.cpp 系エンジン）

この節は旧契約（`schema_version` 1: 接続先識別子 `conn` とプロセス世代 `gen` をイベントで送り、Desktop 側が (セッション, 接続先) の行を持ち、世代が変われば古い行を退避していた実装）での確認です。現在の契約（`schema_version` 2）では Desktop 側はセッションあたり 1 行だけ持ち、行は次のサンプルで置き換わります。以下の退避の記述は当時のものです。

Desktop の確認は 2 つの稼働形態で実施しました（パッケージ版 Desktop・v0.22.2 基準パッチ適用済みの本体、HEAD チェックアウトの Desktop を vite dev + 実バックエンド + Electron で起動）。v0.22.2 基準は当時の適用基準で、現在の基準は `README.md` § 本体パッチ を見てください。ステータスバー表示と公開イベントは CDP（loopback 専用の開発用デバッグポート）の DOM 読み取りと実マウス（合成ポインターでは泡が開かないため実ホバーを使用）で確認しました。バックエンドは HOME 共有のため、別アプリのプロブ renderer にも同じセッションの live サンプルが観測されました。絶対値の速度・秒数・VRAM 使用量・セッション id・プロセス id は環境依存の実測のため載せません。

### 計測の整合（エンジン ログと突き合わせ）

Desktop の短いチャット 5 件（ストリーミング、`finish_reason = stop`）につき、送信直前にエンジン ログの末尾バイト位置を控えておき、その後に追記された行だけを対応付けて比較しました（エンジン ログの行にはタイムスタンプが無いためバイト位置で切り分け、同一エンジン上の他のリクエスト──別セッションからの長い生成──とはトークン数で区別）。

- 出力トークン数はエンジン側の生成トークン数と完全一致しました。
- `decode_seconds` は常にエンジン生成時間より小さく（96.8%〜99.2%）、窓が終端フレームで閉じていることと一致します。
- 1 件あたりの速度はエンジン側の `tok/s` と最大 3.2% の差（同一リクエスト同士での比較。リクエストをまたいだ平均との比較ではありません）。
- 同じ呼び出しで `ended_at − first_chunk_at` は桁違いに大きく、`decode_seconds` との差が実機で確認できました（プロンプト処理・応答後処理・待ちは窓に入りません）。
- この環境では実行中にエンジンが VRAM 逼迫を繰り返し警告する状態で、生成速度はリクエストごとに大きく揺れました。比較は常に同一リクエストのエンジン行に対して行うため、速度の絶対値は一貫性検証の内部でしか使いません。

### 表示の実機検証済み項目

- パッケージ版ではインストール済みコピーの SHA256 がソースと一致し、`runtime door` が当プラグインを desktop-plugins から読み込み登録済みであることを statusbar store と plugin records の読み取りで確認しました。項目はステータスバー右側に 1 件だけ（公式項目と並び、duplicate しない）。
- コア項目と同形式の表示（⚡ アイコン＋`… t/s`、値はフォーカス中セッションの完了済み呼び出しのサンプルに沿ってターン完了ごと・ターン中に変動）、未計測は `⚡ —`。クリック詳細カードは仕様どおり挙動なし（仕様の説明はホバー泡のみ）。ホバー文言は実マウスのホバーで目視確認済み。
- 旧（プロバイダ・モデル）単位設計では、同じモデルで新しいセッションタブを開くと表示が計測値を引き継ぐ動作を実機確認済み（セッション単位への切替後はこの共有はありません。同じ瞬間の公式項目は空（`—`）で対照が確認できました）。
- セッション単位設計: 参照が `focusedStoredSessionId` に追従し、セッション切替で参照が切り替わり、サンプルの無いセッションは `—`（切り替え後も本チャットが裏でデコードを継続していても、項目は新たにフォーカスしたセッションの参照を出し続けました）。表示・非表示の切替は右クリック メニュー「Show in status bar」（チェック解除で bar から項目が消え、戻すと再表示）で、アプリ側の設定に保存されます。アプリ側スイッチをオフにすると項目だけ消え、オンに戻すと再表示。プラグイン初回不在は fresh profile で enablement decision が無い `defaultEnabled: false` の既定動作（バグではなく仕様、localStorage で有効化）。
- モデル変更リセット: 同じセッション内でモデルを切り替えると、新しいモデルのサンプルが着いた時点で旧モデルの計測は窓の履歴（`valid_samples`）に混ざらず、表示は新しい値へ置き換わる（途中でサンプルが着かないモデルへの切替を 2 回重ねても、次に着いたモデルの最初の計測から履歴が始まる）。
- compaction/再開: ローカル モデルのコンテキスト閾値で preflight compression が実走し、要約の主経路が stall したあと deterministic fallback が in place でコミットされた。その前後で `session_id` は不変で、圧縮後のターンは同じセッション id のまま完了し、参照先と表示も継続した（圧縮をまたぐターンの入力サイズは圧縮後の推定と整合）。中断されたターンは未計測どころかサンプル イベント自体を発行しない（fail-closed）。
- バックエンド再起動退避: Desktop を開いたまま計測モジュールの常駐するバックエンド プロセスを停止すると Desktop が直ちに再スポーンし、新プロセス世代のサンプルは世代 id と接続先識別子の両方を作り直し、同じセッションの再起動前の行を退避して表示が新しい値へ復帰した（`—` に stuck しない）。一方 `host.restartGateway()` が再起動する CLI gateway は cron など別面を担う別プロセスで、浅い gateway 再起動では世代 id も接続先も不変のまま計測窓が連続します。
- 未計測の呼び出し（非ストリーミング・中断・計測前の本体プロセス）は `measured = false` となり、表示へ数値を出しません（初回表示は `—`）。
- 表示の作り方は検証後に 1 回変わりました（公式項目と同じ ⚡/`… t/s` 表記・未計測 `⚡ —`・ホバー文言を「フォーカス中のセッションの直近10回のデコード速度」へ簡素化・右クリック メニューで切り替え可能な登録方式）。旧作り（`↑ … tok/s` 表記・括弧付き文言）と新作りのアイコン・表記・文言・メニュー切替の両方を実機目視済みです。

## クラウド LLM の実測（2026-10-22、認証済みの実接続で 1 呼び出しずつ）

計測パッチを適用した本体（main `4e58c721c8…` 基準）で `post_api_request` フックが受け取った payload を直接観測しました。フックに一時的な診断処理を加えて provider・api_mode・usage の出力トークン数・decode_seconds・finish_reason・API 所要時間を記録しました（認証情報・応答本文・接続 URL・セッション ID は記録していません）。

- ローカル LLM（`custom` / `chat_completions`）: 出力トークン 24、decode_seconds 0.19（API 所要時間 9.57 秒）。表示側で計算すると約 126 t/s で、2026-10-10 に記録したローカルの実測速度と一致します。
- Ollama Cloud（`ollama-cloud` / `chat_completions`）: 出力トークン 108、decode_seconds 0.89（API 所要時間 12.78 秒）、終端は `stop`。ストリームの終端フレームで usage が取れ、正の decode_seconds が記録されました。
- Codex サブスクリプション（`openai-codex` / `codex_responses`）: 3 呼び出しとも出力トークンと decode_seconds が取れました（95 / 128 / 5 トークン、0.45 / 1.37 / 0.26 秒、終端は 2 件が `tool_calls`、1 件が `stop`）。実際に使われた経路は `codex_responses` で、`codex_app_server` ではありません（`model.openai_runtime` の指定が無く、Codex の OAuth 認証は app-server 利用の証拠にはなりません）。

3 つとも計測の窓（最初の実データ delta から終端フレームまで）が取れ、usage の出力トークンが正規化されて配信されています。サンプルの配信先接続クライアントは 2 件で、Desktop を含むクライアントが受信側です。表示側はプロバイダで絞り込まないので、この 2 つのクラウド経路にも同じ値が流れます。

## 統合再検証（2026-10-16、監査対応で上流 PR を最新 main へ追従）

fork ブランチの3コミットを上流 main `9c002cb941…` へリベースして PR head を `0e4f0bd2…` → `eeac6734cd25c0e39622806b2388c65be26b2505` に更新し、`patches/decode-hook.patch` を新基準の差分どおりに再生成しました（差分は7ファイル・560追加/7削除）。3コミットとも3-way マージで衝突なしに再生し、旧基準 `cca83848ec…` では上流が `agent/agent_init.py` の `_USAGE_STATE` 辞書隣接のコメント行を削除したことで `git apply` が1ハンク reject していましたが、新基準では適用チェックが通ります。追従後のツリーで両計測テストを `26 passed`・エラー0で確認済み（同時に Codex mock 時計を列が尽きたあとも最後の刻みを返す形へ堅牢化。上流 conftest がテスト後にループを閉じる際、Windows の proactor shutdown が `time.monotonic()` を呼ぶ teardown で StopIteration→RuntimeError になっていたのを防いだ）。リベース後も上流 main は継続して前進しています（執筆時点の先端 `f974134f`、直後の3コミットは当パッチの7ファイル領域への変更なしを確認済み）。

## 統合再検証（2026-10-22、計測パッチの基準追従とパッチ 1 本化）

fork ブランチの 4 コミットを上流 main `4e58c721c8…` へリベースし、PR head を `eeac6734cd…` → `0bbdee3787…` に更新しました。`patches/decode-hook.patch` を新基準の差分どおりに再生成しました。v0.22.4 タグには同じ 4 コミットを 3-way マージで再生すると main 基準とバイト一致になる（計測配線の 7 ファイルが両基準で同じ内容）ため、専用ファイルを分ける代わりに 1 本の差分を両基準へ適用する形にし、専用の v0.22.2・v0.22.4 パッチファイルは retired しました。どちらの基準でも `git apply --check` が通り、適用後の計測テストは両ツリーとも `26 passed`・エラー0 でした（Windows の実チェックアウト `4d04303818…` でも `git apply --check`・適用と 7 ファイルのみ変更であることを確認済み。mock 時計の決定論的計測で、90.5 秒超の窓を含みます）。v0.22.2 で見られていた chat 側の import 時不具合は v0.22.4 では解消しているため、リリースツリーでも chat 側の窓テストまで実行できます。計測内容そのものは変更していません（差分は基準コミットと適用先だけ）。

## 監査指摘への対応（2026-10-22、表示と計測の整合）

リポジトリ監査（2026-10-22 付）で挙げられた P1 2 件と、それに統合した P2（Desktop 側の状態管理の簡素化）に対応しました。イベント契約を変えたので Python 側と Desktop 側を同時に更新し、`schema_version` は `1` から `2` へ上げています（版の違う frame は Desktop 側で無視されるため、旧版と新版のイベントは混在しません）。

- P1「接続先切替後に速度が `—` のままになる」: Desktop 側はセッション 1 行の保持に改めました。集計は Python 側の（セッション, 実接続先）単位の窓のままです。旧実装では同じセッションに 2 つの接続先の行が残ると表示が `null` になっていました（旧実装の `pickVisibleSample([b, a], 's')` は `null`、新実装は最新行 `b`）。
- P1「未計測の最新呼び出しで古い速度が残る」: `measured` の意味を「履歴に有効サンプルがある」から「最新の呼び出しを計測できたか」へ変えました。最新の呼び出しを計測できなかったときは `tok_per_sec` も `null` で、Desktop 側は `—` へ戻します。有効な履歴は Python 側の窓に残るので、次の計測できた呼び出しで集計が再開します。
- P2「Desktop の状態・識別子管理の簡素化」: 接続先の識別子とプロセス世代の ID はイベントから撤廃しました（`schema_version` 2 の payload に `conn` も `gen` もありません）。接続 URL は Python プロセス内の窓の添字としてだけ使い、Desktop へ URL 自身もその派生物も送りません。Desktop 側にあった接続先の混在判定・世代管理・モデル退避のコードがなくなりました。

自動テスト: プラグイン pytest 19 件、Desktop のマッチャー・キャッシュ・payload 解釈の依存なしテスト、`node --check` を通過。新テストが変更前の実装で落ちることを RED として確認しました（Python 4 件が失敗、Desktop 側は契約の版と PARSE ブロックの検査で失敗）。旧実装の挙動を直接測って P1 の再現（接続先 2 つのセッションでは `null`）も確認しました。公開 CI は毎 push で 4 ジョブ（プラグイン pytest・Desktop 構文とマッチャー・上流 main 基準と v0.22.4 タグ基準へパッチを適用しての計測テスト）を実行します。

実機: 計測パッチを適用した本体で実際に 1 呼び出しを実行し、配信される payload が `schema_version` 2 で `conn` も `gen` も持たないことを確認しました（Ollama Cloud の 1 呼び出し、`tok_per_sec` 111、`measured` true、`valid_samples` 1）。接続先切替と未計測呼び出しの表示そのものは Desktop 実機 E2E が未実施です（2026-10-16 の E2E は旧契約の確認です）。Desktop 側のプラグイン半分の更新は再スキャンまたは再起動で反映されます。更新のあいだ版が食い違うと表示は `—` のままになります（値を誤って出さないための仕様です）。
## 契約 v2 の Desktop 実機 E2E（2026-10-22、パッケージ版 Desktop・Hermes v0.22.4+70）

イベント契約 v2 を実機の Desktop で確認しました。パッケージ版 Desktop（`apps/desktop/release/win-unpacked`）を loopback 専用のデバッグポート付きで起動し、Chromium DevTools プロトコルで renderer の DOM を読んで表示値の実物を、gateway 側の一時的な診断出力（作業後に撤去）でフック payload の実物を、それぞれ観測しました。観測対象は Plugin のステータスバー項目（⚡ アイコン付き）のテキストです。

| 条件 | 手順 | 結果 |
| --- | --- | --- |
| 計測済みの表示 | Codex サブスクリプション（`gpt-6.1-sol`）で実際に応答させる | 項目が `61 t/s`、次の呼び出しで `111 t/s` と更新。同画面の公式項目（同時は `15 t/s`）とは別の値で、decode 秒数による値になっている |
| 同一セッション内の接続先切替 | 同じセッションのモデルを Codex サブスクリプション（`chatgpt.com`）からローカル Qwen へ切り替えて応答させる | 切替後も項目は新しい接続先の速度を表示（`106 t/s`）。旧契約なら接続先 2 つの行が競合して `—` になる箇所 |
| 計測済み → 未計測への遷移 | 同じセッションで、終端フレームはあるが usage を返さない応答を 1 回起こす | payload は `tok_per_sec: null` / `measured: false` / `valid_samples: 1`（窓の履歴は保持）、表示は `7 t/s` → `—` へ戻った。履歴は次の計測できた呼び出しで再利用される |
| セッション切替 | サイドバーの別セッションへフォーカスを移す | 計測値のあるセッションではその値を表示し、値のないセッションでは `—`（他セッションの値が漏れない） |
| バックエンド再起動 | gateway（Desktop のバックエンド）を再起動し、同じセッションで応答させる | 再起動後の最初の呼び出しは `valid_samples: 1`（新プロセスの窓）となり、表示は `—` → `8 t/s` と復帰する（stuck しない） |

未計測イベントについて: 実測は、終端フレームはあるが usage を持たない応答を返す OpenAI 互換の loopback 試験エンドポイント（作業後に停止・削除）で起こしました。Hermes 本体も Desktop も実物で、確認しているのは「最新の呼び出しが未計測なら表示は `—` に戻り、窓の履歴は保持される」という点だけです。応答が終端フレーム無しで途切れる場合は、本体が再試行して応答が返らないため `post_api_request` 自体は発行されず、表示は前の値を保ちます（README の動作条件と同じ仕様です）。

## 計測パッチの現行 main への移植（2026-10-28、実機の Windows インストール）

Desktop の速度表示が `—` になった報告の調査です。本体の計測配線が実機のツリーから失われていたことが原因でした。

原因の実機確認（実機の Hermes ユーザー領域にあるインストール、`main` の先端 `0431192d69…`、`git describe` は `v0.22.5+canary.20261028T064834Z-45-g0431192d69`）:

- `agent` 配下を `decode_seconds` と `_decode_first_delta_at` で走査して 0 件を確認しました。現行本体のフックは `agent/turn_response_intake.py` で `first_chunk_at` を渡しますが、`decode_seconds` は渡しません。
- 計測配線は作業ツリーの未コミット変更として保持されていました。`hermes update` は autostash 付きの fast-forward で、2026-10-25 14:46 JST の更新ときに `stash@{0}`（`hermes-update-autostash-20261025-054619`）へ退避され、そのまま復元されていませんでした。stash の中身には `_last_api_decode_seconds` と `decode_seconds` の配線が含まれます（`git stash show -p` で確認）。
- 旧パッチ（基準 `4e58c721c8…`）は現行 main へ `git apply --check` が通りません。reject は `agent/codex_runtime.py:862` の 1 箇所だけで、残り 6 ファイルは通ります。v0.22.5 タグ `bf0203a794…` には 7 ファイルすべてで reject します（リリースツリーの context は main と違う）。

移植の内容: `agent/agent_init.py`・`agent/chat_completion_helpers.py`・`agent/turn_api_request.py`・`agent/turn_response_intake.py` と計測テスト 2 ファイルは 3-way マージでそのまま再生できました。衝突は `agent/codex_runtime.py` の 1 箇所だけで、上流が `_CodexResponseAssembler` に追加した `saw_answer_item_added` の宣言と計測刻みの宣言が同じ位置を向いたため、両方を並べて維持しました。計測の意味と配線は旧パッチのままです（差分は 7 ファイル・560 追加 / 7 削除で旧パッチと同形、`git diff --check` は clean）。

テスト結果（実機の Windows。Hermes が管理する test environment の Python 3.14.7、pytest 9.1.1、pytest-asyncio 1.3.0、anthropic 0.87.0）:

| 段階 | 実行 | 結果 |
| --- | --- | --- |
| RED | 計測テスト 2 ファイルだけを適用して実行 | `23 failed, 3 passed`（`decode_seconds` 属性と `_last_api_decode_seconds` の欠落が原因） |
| GREEN | 本体側 5 ファイルを移植して実行 | `26 passed` |
| 既存の関連スイート | ストリーミング・Codex Responses・intake relay・Codex live events・retry backoff・plugin stream hooks・usage anchor・stream single writer・relay native stream | `150 passed, 4 skipped` |
| CI と同じ条件 | 基準コミット `0431192d69…` のまっさらな worktree で `git apply --check` → 適用 → 計測テスト | `--check` 成功、`26 passed` |

プラグイン側は無変更です（`python -m pytest tests -q` 20 passed、`node tests/desktop-match.test.mjs` 通過、`node --check` 通過）。実機にインストール済みの Python 半分（`plugins/hermes-decode/__init__.py`・`plugin.yaml`）と Desktop 半分（`desktop-plugins/hermes-decode/plugin.js`）はリポジトリのファイルと SHA-256 が一致しており、イベント名と契約の版は現行本体と食い違っていないため、プラグイン側の修正は不要でした。

CI の適用基準は 1 本化しました。v0.22.4 タグ基準のジョブを撤去し、上流 main `0431192d69d4afd2443a7da15bb9e129a424414c` の 1 基準にしています（v0.22.5 タグでは `agent/codex_runtime.py` の context が違い、同じ差分は適用できません）。

この段階で未確認だった項目: gateway 再起動後の実機表示です。次節に記す通り、分離インスタンスの試験用 SSE endpoint では計測値と未計測表示を確認しました。一方、実プロバイダを用いた Chat Completions・Codex Responses の表示は未確認のままです。

## gateway 再起動後の Desktop 実機確認（2026-10-28、同じインストール）

Desktop までの伝達を、既存の環境へ触れない分離インスタンスで確認しました。本体は実機のもの、Desktop も実物（`apps/desktop/release/win-unpacked/Hermes.exe`）です。

分離の作り方: 隔離した一時 `HERMES_HOME` へ専用の `config.yaml`（試験用 provider 1 本と `plugins.enabled: [hermes-decode]` だけ）を置き、`hermes-agent`・`installs`・`tools`・`bin` だけを junction で実機インストールへつないで、既定の user-data-dir とは別の `--user-data-dir` で Desktop を起動しました。DevTools は loopback 限定のリモートデバッグポートだけで開いています。実機の `config.yaml`・認証情報・セッションは変更していません。

応答は試験用の OpenAI 互換 loopback SSE endpoint が作ります。content delta 10 かけを 0.14 秒間隔で流し、終端フレームのあとに usage（`completion_tokens: 24`）を載せる mode と、終端フレームだけ返して usage を載せない mode の 2 通りを `mode.txt` で切り替えます。

| 条件 | 実機で読んだ表示 | 応答の形 |
| --- | --- | --- |
| usage を返す応答 | `17 t/s`（CDP で status bar の DOM を読取） | 24 tokens / 約 1.4 秒のデコード窓。プロンプト込みの API 所要時間ではなく最初の data delta から終端までの窓に対応します |
| 同じセッションで usage を返さない応答 | `—` | 終端フレームあり・usage なし。`measured: false` のフレームでそのセッションの行が消え、直前表示の `17 t/s` が上書きされます |

伝達経路は `decode_seconds`（移植した本体側の計測）→ `post_api_request` → plugin の `broadcast_plugin_event` → Desktop の `plugin.hermes-decode.sample` の順で、試験 endpoint のリクエストログ、gateway の稼働、Desktop 側 DOM の 3 点を確認しています。Desktop 半分は opt-in（`defaultEnabled: false`）なので、分離インスタンス側で有効化してから読んでいます。

後片付けと本体側の再起動: 分離 Desktop とその gateway・試験 endpoint は停止し、一時 home（junction 含む）を削除しました。残存 listener が無いことを `netstat` で確認しています。実機の host gateway は `hermes gateway restart` で再起動し（作業ツリーは本体側の未コミット計測パッチを含む状態）、`hermes plugins list` の `hermes-decode` は `enabled` です。Desktop もデバッグポートなしで通常起動し直し、gateway の起動完了と既定 profile の接続まで readback しています。別 profile に残っている既存の gateway ticket 初期化警告は今回の操作とは無関係です。

上流テストスイート全体をまとめて流す実行は、移植由来ではない失敗を大量に出します。実機のインストールツリーで `tests/agent` と `tests/hermes_cli/test_cli_status_bar.py` を走らせると `2046 failed, 9500 passed, 108 skipped, 415 errors`（53 分）ですが、同じ test environment のまっさらな worktree で `tests/agent/lsp` を単独実行すると、パッチ無しの基準コミットとパッチ適用済みの worktree がともに `15 failed, 95 passed, 2 skipped` で、実機のインストールツリーだけが `30 failed, 80 passed, 2 skipped` です（各 2 回実行して同じ）。差分 15 件はすべて LSP 側のテストで、実機ツリーでは `agent.lsp.manager.LSPService` が実在する言語サーバーを spawn して `_broken` へ記録します（例: `lsp[pyright] spawn/initialize failed for a user-profile path: RuntimeError`、`assert len(svc._broken) == 0` が `1 == 0`）。まとめて流す場合の主因は、実 `HERMES_HOME` へ file I/O するテストを弾く guard（`TEST BUG: file I/O against the REAL hermes home`）と言語サーバーの spawn 成否で、同じファイルでも単独実行すれば通ります（`tests/agent/test_verification_continuation_budget.py` と `tests/agent/test_tool_batch_segmentation.py` は単独で `38 passed, 1 skipped`）。パッチの有無は結果を変えないので、この本数の失敗は移植由来ではなく、実機のインストールツリーと言語サーバーを伴う環境側の条件由来です。

実接続での表示（Codex サブスクリプション・Ollama Cloud・ローカル LLM）は今回の確認では実施していません。Codex Responses 経路の計測は `tests/agent/test_codex_first_event_timing.py` の決定論テストで覆っていますが、実機 Desktop での目視は未実施のままです。

## 上流テストスイート全体の失敗数についての切り分け（2026-10-29、同じ test environment）

移植後の実機ツリーで `tests/agent` と `tests/hermes_cli/test_cli_status_bar.py` をまとめて流すと `2046 failed, 9500 passed, 108 skipped, 415 errors`（53 分）が出ました。本体の作業ツリーが dirty なので、この本数が移植由来かどうかを基準コミット `0431192d69…` の worktree で切り分けました（同じ test environment の Python、`--tb=no` の `tests/agent/lsp` 単体実行）。

| ツリー | `tests/agent/lsp` の結果 |
| --- | --- |
| 基準コミットのまっさらな worktree（パッチ無し） | `15 failed, 95 passed, 2 skipped`（2 回実行して同じ） |
| 同じ worktree へ `patches/decode-hook.patch` を適用（`git apply --check` は exit 0） | `15 failed, 95 passed, 2 skipped` |
| 実機のインストールツリー（パッチ適用済み） | `30 failed, 80 passed, 2 skipped`（2 回実行して同じ） |

パッチ適用の有無では結果は変わらず、実機のインストールツリーだけ失敗数が多くなります。失敗の理由は本体の計測とは無関係で、`agent.lsp.manager.LSPService` が実環境の実在する言語サーバーを spawn して `_broken` へ記録するものです（例: `lsp[pyright] spawn/initialize failed for a user-profile path: RuntimeError`、`assert len(svc._broken) == 0` が `1 == 0`）。まとめて流す場合の失敗は、実 `HERMES_HOME` へ file I/O するテストを弾く guard（`TEST BUG: file I/O against the REAL hermes home`）と言語サーバーの spawn 成否が主因で、同じファイルでも単独実行すれば通ります（`tests/agent/test_verification_continuation_budget.py` と `tests/agent/test_tool_batch_segmentation.py` は単独で `38 passed, 1 skipped`）。したがって本数の大きな失敗は移植由来ではなく、実機のインストールツリーと言語サーバーを伴う環境側の条件由来です。

## 計測パッチの基準追従（2026-10-29、上流 main `145f26b5a5…`）

前の基準 `0431192d69…` から上流 main が前進していたため、新しい先端へ基準を追従させて CI・README・`THIRD_PARTY_NOTICES.md` の pin を揃えました。実機ではなく、上流を clone したまっさらなツリーでの実測です。

作業ツリー: 上流を blob 遅延 clone して main の先端（作業開始時点）`145f26b5a5198f5a733641ef6b6c3d74f087269d` を detached checkout した状態（`git describe` は `v0.22.5+canary.20261028T064834Z-471-g145f26b5a5`、前の基準から 426 コミット進、`git status` は clean）。

| 確認 | 結果 |
| --- | --- |
| パッチが触る 7 ファイルの差分（前の基準→新基準） | `agent/chat_completion_helpers.py` だけが変更で、内容は `_bedrock_converse_call` の client 生成にアクティブなモデルを渡す 1 行。残り 6 ファイルは前の基準と同じ blob |
| 旧パッチの適用可否（新基準） | `git apply --check` は exit 0。reject なし（計測配線の移植は不要だった） |
| パッチ再生成 | `git diff` で再生成。7 ファイル・560 追加 / 7 削除は旧パッチと同じ形、ハンクヘッダ 32 本も一致、差分の本文の増減は 0 件。変わったのは `agent/chat_completion_helpers.py` の `index` の前置イメージだけ（`d8a4b815ae` → `c682fbd4d7`、適用後は `a23f0c48c9` → `3f73092489`） |
| 往復の確認 | まっさらなツリーで `git apply --check`（exit 0）→ `git apply --3way --check`（exit 0）→ 適用（変更は上記 7 ファイルのみ）→ `git apply --reverse` で `git status` clean に戻る |
| パッチ適用前の既存テスト（計測テスト 2 ファイル、パッチ無し） | `10 passed`（上流側の既存テストだけが存在し、このツリーに `decode_seconds` の配線はない。この節では失敗を測っていないので RED とは呼ばない。RED として失敗を実測したのは 2026-10-28 節の `23 failed, 3 passed` の方です） |
| GREEN（パッチ適用後の計測テスト 2 ファイル） | `26 passed` |
| 既存の関連スイート（パッチ適用後） | `209 passed, 4 skipped`（失敗 0。`tests/agent` の streaming・Codex Responses adapter・run_agent codex_responses・turn response intake relay・Codex runtime live events・stream retry backoff・plugin stream hooks・usage anchor・stream single writer とその guard、`tests/e2e` の relay native OpenAI・Anthropic stream の計 12 ファイル） |
| 上流 main がまだ計測を受け付けていないこと | 新基準のまっさらなツリーで `agent/*.py` を `decode_seconds` / `_last_api_decode_seconds` で走査して 0 件、`agent/turn_response_intake.py` は `first_chunk_at` だけを渡す |

実行環境: Windows の上流 clone。test environment は上流の `uv.lock` から `uv sync --frozen --group dev --group test` で作り、Python 3.14.7・pytest 9.1.1・pytest-asyncio 1.4.0・anthropic 0.125.0 です。実行は `.venv\Scripts\python.exe -m pytest <対象ファイル> -q`（`OPENAI_API_KEY`・`OPENROUTER_API_KEY` は空、`PYTHONHASHSEED=0`）。上流の `scripts/run_tests.sh` は bash 必須で、この Windows 環境では WSL の bash が起動しないため直接 pytest を呼んでいます（上流ランナーのファイル単位隔離や環境の固定は入らない）。上流テストスイート全体をまとめて流す実行は今回行っていません（前節のとおり環境側の条件で大量の失敗が出るため、対象を絞って失敗 0 を確認しています）。

プラグイン側（集計・表示・CI の他の 2 ジョブ）は無変更です。基準追従のコミットは同日に `Wepoi9/hermes-decode` の `main` へ push しました（`310b973`、および記録を整えた `ebfcca1`・`9953e19`）。公開 CI（run `40828122116`、head `9953e19…`）は 3 ジョブすべて成功です（Plugin aggregation tests・Desktop half syntax and matcher・Core patch applies to its base commit and its tests run）。Core patch のジョブログは `git -C hermes switch --detach 145f26b5a5198f5a733641ef6b6c3d74f087269d` から上流ランナーで `Summary: 2 files, 26 tests passed, 0 failed` を出しています（Windows での直接実行より強い条件です。上流の `scripts/run_tests.sh` のファイル単位の隔離と 4 workers が効いています）。

実機側は前の基準のまま残っています。実機のインストール（`main` を追う更新経路）は `0431192d69…`（`v0.22.5+canary.20261028T064834Z-45-g0431192d69`）にあり、計測配線は未コミットの作業ツリー変更として 7 ファイルに入っています（`git status` は上記 7 ファイルが dirty、`decode_seconds` の走査は 16 件）。本体が新基準以降へ更新されたあとは、そのツリーへ再生成した差分を再適用する必要があります。実プロバイダ（Codex サブスクリプション・Ollama Cloud・ローカル LLM）での Desktop 表示は今回の作業でも実施していません（前節どおり未確認のままです）。

## 上流 PR #139204 の基準追従（2026-10-29、head `41ea0303ff…`）

上流 PR のブランチ `Wepoi9/hermes-agent:feat/decode-measure-hook` を基準 `145f26b5a5…` 之上へ更新しました（`--force-with-lease`、`0bbdee3787…` → `41ea0303ff…`）。fork ブランチのみで、上流 `main` へは書き込んでいません（push 後の上流 main は `ef75bc7088…`）。

- 旧ブランチの 4 コミットを新基準へ 3-way replay すると `agent/chat_completion_helpers.py` と `agent/codex_runtime.py` が衝突しました（上流が `_CodexResponseAssembler` へ `saw_answer_item_added` を追加、`_bedrock_converse_call` の client 生成にモデルを渡す行を変更）。中間コミットの途中状態を保つ意味はないため、新旧の差分内容をそのまま 1 コミットへ squash しています。
- 追加 560・削除 7 とハンク 32 本は旧ブランチの差分と一致し（追加・削除行 567 行はバイト一致、相違は基準漂移による context 行のみ）、新 head のツリーは `patches/decode-hook.patch` の適用後 blob と 7 ファイルすべてで一致しました。GitHub が読む PR 差分も hunk 本文・context 行・追加/削除行が `patches/decode-hook.patch` と一致します（GitHub 側は index の省略長が 12 文字、ハンクの関数名表示が異なるだけです）。
- 更新後の head ツリーを fork clone 専用の test environment（同じ `uv.lock`、Python 3.14.7）で実行し、計測テスト `26 passed`、関連スイート 12 ファイル `209 passed, 4 skipped`（失敗 0）を確認しました。実行方法は前節と同じ（pytest 直接実行）。
- PR 状態（更新直後の読み取り）: `OPEN`・非 Draft・`mergeable: MERGEABLE`・レビュー 0 件・コメント 0 件・1 コミット。上流のワークフローは push 後に `action_required` になり、メンテナの承認を待つ状態です（CI・Docker Build・Nix flake check・Label rerun・Plugin Catalog CI、`Docs Search Monitor` の 1 本だけが success）。つまり上流 CI は緑ではなく、承認ゲートで止まっています。原因が最初のコントリビューション承認なのか他の条件なのかは、GitHub の読み取りでは確定できません。
- PR 本文は現行の事実へ更新しました。古い基準の記述と v0.22.2 / v0.22.4 ツリーへの適用に関する記述は削除し、Windows での直接実行と GitHub Actions の検証を区別し、当リポジトリの `patches/decode-hook.patch` が push 済みのコミットと一致し公開 CI が新基準で 3 ジョブ成功している旨を明記しています（上流のメンテナ承認を待つ状態であることも併記しました）。
