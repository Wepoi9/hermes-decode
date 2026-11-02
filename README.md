# Hermes Decode — セッション累積デコード速度

Hermes Desktop のステータスバーに、現在フォーカスしているセッションの累積デコード速度（tok/s）を表示するプラグインです。

### 特徴と集計仕様
- **セッション単位の累積**: 完了したすべての計測可能呼び出しをセッションごとに合算します。単純な平均ではなく、$\sum(\text{出力トークン数}) \div \sum(\text{デコード秒数})$ で算出するため、計測できた呼び出しを対象としたセッション累積デコード速度を把握できます。
- **コンテキストの維持**: 同一セッション内であれば、モデルやプロバイダー、接続先を変更しても累積値を引き継ぎます。
- **コンパクション対応**: セッション ID が切り替わるコンパクション発生時、`post_compaction` イベントを通じて前のセッションから新しいセッションへ累積値を引き継ぎます。なお、旧セッション ID が通知されない場合は、新しい集計として開始されます。
- **メモリ内管理**: 集計データはメモリ上にのみ保持されます。Gateway の再起動やプラグインの再読込によって初期化されます（DSH の永続セッション投影とは異なります）。
- **低負荷・安全**: 接続 URL、認証情報、プロンプトや応答の本文はイベントに含めず、外部サーバーへの通信も行いません。

### ファイル構成
- `__init__.py`: セッションごとのトークン数・デコード時間の集計および通知。
- `desktop/plugin.js`: フォーカス中セッションの速度表示ロジック。
- `plugin.yaml`: `post_api_request` および `post_compaction` のフック宣言。
- `patches/decode-hook.patch`: 上流が `decode_seconds` を提供するまでの暫定的な本体パッチ。
- `tests/`: 集計、データ契約、Desktop 表示の検証テスト。
- `docs/VERIFICATION.md`: 過去の検証履歴。

### インストールと設定
1. リポジトリを `~/.hermes/plugins/hermes-decode` に配置します。
2. `hermes plugins enable hermes-decode` を実行し、プラグインを有効化します。
3. Hermes Desktop の **Capabilities** $\rightarrow$ **Plugins** で `hermes-decode` をオンにします。
4. ステータスバーを右クリックし、「Show in status bar」から `デコード速度` を選択します。
   - ※ Hermes 標準の `Tokens per second` とは別の項目として表示されます。

### 本体パッチの適用
本体が `post_api_request` で `decode_seconds` を提供していない場合、以下のパッチを適用してください。
※ 上流 PR ([NousResearch/hermes-agent#139204](https://github.com/NousResearch/hermes-agent/pull/139204)) が反映済みの場合は不要です。

```bash
git apply --check patches/decode-hook.patch
git apply patches/decode-hook.patch
```
**注意**: 適用前に必ず現在の作業ツリーで確認してください。`hermes update` 後の更新で計測配線が `autostash` されることがあるため、更新後は再確認を推奨します。

### データ契約
イベント名: `plugin.hermes-decode.sample` (schema_version: 3)

| キー | 内容 | 備考 |
| :--- | :--- | :--- |
| `session_id` | 集計対象のセッション ID | |
| `provider` / `model` | 送信元識別子 | 累積値の区切りには使用しません |
| `tok_per_sec` | セッション累積速度 | 計測履歴がない場合は `null` |
| `measured` | 今回の呼び出しが計測可能だったか | `false` でも過去の累積値は維持されます |
| `valid_samples` | セッション内の有効呼び出し件数 | |
| `schema_version` | スキーマバージョン (3) | 旧バージョンとの混在を防止します |

- **コンパクション時**: セッション ID 切り替わり直後に 1 回通知されます（`measured: false`）。これにより表示が `—` に戻ることなく累積値を引き継ぎます。
- **計測除外**: 待機時間、ツール実行時間、失敗、部分応答は除外され、純粋なデコード時間のみを計測します。
- **標準指標との違い**: 標準の `Tokens per second` は直近 10 回の移動平均ですが、本プラグインはセッション全体の累積を使用し、DSH のセッション累積指標と整合性を取っています。

### テスト
```bash
python -m pytest tests -q
node --check desktop/plugin.js
node tests/desktop-match.test.mjs
```
GitHub Actions の `core-patch-applies` により、固定した上流コミットへのパッチ適用と計測テストが自動的に実行されます。
