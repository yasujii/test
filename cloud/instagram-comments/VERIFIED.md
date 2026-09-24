# 実行検証結果（2026-09-24）

GitHub Actionsの標準Ubuntuで、他者の公開例示投稿からコメント本文5件と投稿本文1件を取得した。
Instagramログイン、Metaアクセストークン、有料APIは使用していない。
経路はinstagrapi 3.0.13の非公式公開GraphQLであり、公式Graph APIのコメント権限による取得ではない。

初回接続試験: https://github.com/yasujii/test/actions/runs/35937276668
本体・暗号化出力試験: https://github.com/yasujii/test/actions/runs/35938120612
本体job ID: 107439700218
本体試験commit: 22376ad80b9a02ea289bf09e1b9a30e85c5c06c6

確認した工程:
- 固定依存関係の導入と単体テスト21件が成功。
- コメントの実テキスト5件と投稿本文1件をAPI応答から受信。
- 本文をGitHubの公開ログへ平文では出さず、公開鍵で暗号化して返却。
- 接続済みGitHubツールでログ回収後、依頼側のコンテナで復号。
- CSV/JSONを保存し、コメント本文が空でないこと・件数が5件で一致することを検品。

保存ファイルのSHA256（本文・秘密鍵はこのリポジトリに含めない）:
- posts.csv: 873bea9216c68f55d5919b3e0d594d84a8a62d81ee42c399d3ef77ba62dbe076
- comments.csv: 16abef4e236a5c1e2f8d8e1ff3d14621199f9db7a589a354372e155c1dfaf73d
- data.json: 398d8bbe92f819245b477041c662958ac731e0c0d623f77f5f62e67e99f37312
- run_report.json: a829e5593f375b50c5726f77fd04899ca70d299857a6e9bc4e4b6454837d4e77

限界:
検証対象は例示投稿1件であり、ユーザー指定のインフルエンサー投稿ではない。
全投稿・全コメント・全返信・将来の継続取得は保証しない。
Mac実機での導入・取得、GH_TOKENを使うcloud.py単体の完全往復、Graph API補助機能の実取得は未実施。
設定だけできたこと、単体テストだけ通ったことを実コメント取得の証拠にしていない。
