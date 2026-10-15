# 第三者表記（THIRD PARTIES）

## 上流 Hermes（本体パッチ）

`patches/decode-hook.patch` と `patches/decode-hook-v0.22.2.patch`（同一内容・二つの基準）は Hermes 本体（`agent/` と `tests/agent/` の計測配線）へ当リポジトリが当上流 PR
で計測が受け付けられるまでの間だけ適用する差分で、上流のデコード経路・ストリーミング処理・既存テストの
上流派生コードの上に成り立っています。したがってこれらのパッチを当リポジトリで再配布する限り、次の表記を外
ません（上流が計測をマージした後は本体ビルドインプラグインへ移り、当パッチは削除予定で、この表記の必
要は消えます）。

    Copyright (c) 2025 Nous Research

- 上流リポジトリ: https://github.com/NousResearch/hermes-agent（MIT ライセンス）
- 基準コミット（`decode-hook.patch`）: `cca83848ec79a4a6a2ecc10285725559bf72c97c`（上流 main の固定コミット）
- 基準コミット（`decode-hook-v0.22.2.patch`）: `d923a5eb487a17c0a68801bba9ef0f56c81040a8`（公表済み v0.22.2 タグコミット）
- 上流 PR: NousResearch/hermes-agent#139204（マージ待ち）

上流の著作権表示は上流側の MIT 条件の一部であり、当リポジトリの `LICENSE`（MIT、© 2026 Wepoi9）は
プラグイン本体のコード（集計・表示・テスト・CI・このドキュメント）にだけ及びます。上流コードを含む
上記 2 パッチの再配布条件は上記表記の保持を条件として上流の MIT が及びます。
