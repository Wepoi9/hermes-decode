# Hermes Decode — セッション累積デコード速度

Hermes Desktop のステータスバーに、フォーカス中セッションの累積デコード速度を表示するプラグインです。

```text
累積 tok/s = Σ(計測できた出力トークン数) ÷ Σ(対応するデコード秒数)
```

- **直近10回の窓は廃止**。完了した計測可能な呼び出しを、セッション単位で最初から累積します。速度の単純平均ではありません。
- 同じセッション内では、モデル・provider・接続先を切り替えても累積を引き継ぎます。別セッションの値は混ぜません。
- コンパクションでセッション ID が切り替わった場合は、`post_compaction` が報告した前の ID から新しい ID へ累積を移します。同じ ID に留まる in-place compaction は集計を変えません。前の ID を報告しない経路では、新しいセッションの集計は 0 から始まります。
- 未計測の呼び出しは分子・分母・有効件数を変えず、既存の累積値を表示し続けます。まだ一度も計測できていないセッションは `—` です。
- **集計はメモリ内だけ**。gateway 再起動・プラグイン再読込で初期化されます。セッションログからの復元・永続化は実装していません。DSH の永続セッション投影と異なる点です。
- 接続 URL、認証情報、プロンプト本文、応答本文は Desktop イベントへ含めません。バックグラウンド処理や外部サーバーは追加しません。

## ファイル構成

- `__init__.py` — セッションごとの累積トークン数・デコード時間の集計と通知
- `desktop/plugin.js` — フォーカス中セッションの速度表示
- `plugin.yaml` — `post_api_request` と `post_compaction` フック宣言
- `patches/decode-hook.patch` — 上流が `decode_seconds` を提供するまでの一時パッチ
- `tests/` — 集計・契約・Desktop表示のテスト
- `docs/VERIFICATION.md` — 過去の検証履歴（旧仕様の直近10回の記録も含みます）

## インストール・表示設定

1. リポジトリを `~/.hermes/plugins/hermes-decode` に配置します。
2. `hermes plugins enable hermes-decode` で本体側を有効にします。
3. Hermes Desktop の Capabilities → Plugins で `hermes-decode` をオンにします。Desktop 側は初期状態ではオフです。
4. ステータスバー右クリックの「Show in status bar」で `デコード速度` を表示します。Hermes 標準の `Tokens per second` とは別項目です。

本体が `post_api_request` に `decode_seconds` を渡す必要があります。上流 PR [NousResearch/hermes-agent#139204](https://github.com/NousResearch/hermes-agent/pull/139204) が利用中の本体に反映されるまでは、対応した本体パッチが必要です。

## 本体パッチ

`patches/decode-hook.patch` は上流コミット `145f26b5a5198f5a733641ef6b6c3d74f087269d` を基準としています。上流へマージされるまでの暫定措置です。

```bash
git apply --check patches/decode-hook.patch
git apply patches/decode-hook.patch
```

実際に稼働している Hermes 本体の作業ツリーで事前確認してください。既に適用済みの場合や上流の更新が進んだ場合は、差分を確認せずに再適用しないでください。`hermes update` により未コミットの計測配線が autostash される場合があるため、更新後は再確認してください。

パッチは上流 Hermes の MIT 著作権表示を保持しています。詳細は `THIRD_PARTY_NOTICES.md` を参照してください。

## データ契約

イベントは `plugin.hermes-decode.sample`、`schema_version = 3` です。

| キー | 内容 |
| --- | --- |
| `session_id` | 表示・集計対象のセッション |
| `provider` / `model` | 送信元識別。累積値の区切りには使用しない |
| `tok_per_sec` | その時点のセッション累積速度。計測履歴なしの場合 `null` |
| `measured` | **今回の呼び出し**が計測可能だったか。false でも過去の累積速度は維持 |
| `valid_samples` | セッション内で累積した有効呼び出し件数 |
| `schema_version` | 3。旧バージョンの表示イベントを混在させない |

セッション ID が切り替わるコンパクションの直後にも 1 回通知します。これは API 呼び出しではないため `measured: false` ですが、`tok_per_sec` と `valid_samples` は移し終えた累積値なので、表示は `—` に戻りません。移す前のセッションの集計状態は削除するため、同じコンパクションを何度報告されても加算は重複しません。

デコード時間は最初の実データ delta から正常な終端までを計測し、待機・ツール実行・失敗・部分応答は計測対象から除外します。モデルの出力速度は本体計測フックが提供する時間に依存します。

Hermes 標準の `Tokens per second` は直近10回の API 所要時間を使用しますが、このプラグインはセッション累積のデコード秒数を使用します。Hermes 標準のキャッシュヒット表示や DSH のセッション累積指標と集計期間を揃えることを目的としています。ただし、DSH のようなログ永続化はありません。

## テスト

```bash
python -m pytest tests -q
node --check desktop/plugin.js
node tests/desktop-match.test.mjs
```

GitHub Actions の `core-patch-applies` は、固定した上流コミットへ一時パッチを適用し、計測時間のテストを実行します。今回の集計仕様の変更で本体パッチや上流 PR は変更しません。
