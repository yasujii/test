# 実行検証結果（2026-09-24）

GitHub Actionsの標準Ubuntuで、他者の公開例示投稿からコメント本文5件と投稿本文1件を取得した。
Instagramログイン、Metaアクセストークン、有料APIは使用していない。
経路はinstagrapi 3.0.13の非公式公開GraphQLであり、公式Graph APIのコメント権限による取得ではない。

## 最新の往復確認

実行: https://github.com/yasujii/test/actions/runs/35938393730
job ID: 107440564422
commit: f7d58889bf654d40ad96670cdf89d8ed3f812196
取得時間: 2026-09-24 00:26:12〜00:26:27 UTC
対象: https://www.instagram.com/p/CjPUjEvDKT4/

確認した工程:
- 固定依存関係instagrapi 3.0.13 / cryptography 50.0.1の導入とコア単体テスト21件が成功。
- コメントの実テキスト5件と投稿本文1件をAPI応答から受信。
- 本文をGitHubの公開ログへ平文では出さず、公開鍵で暗号化して返却。
- 接続済みGitHubツールでログを回収後、依頼側のコンテナで復号。
- CSV/JSONを保存。コメント本文が空でないこと、CSVが5行、出典URLが指定と一致することを確認。
- JSONコメント配列のSHA-256と実行報告のハッシュが一致。
- コメント文字数は4 / 14 / 3 / 3 / 15、投稿本文338文字。短文・絵文字を含む動作試験。
- 追加起動クライアントの単体テスト8件成功、run.py importを実GitHubログで実行して保存まで成功。

最新保存ファイルのSHA256（本文・秘密鍵はリポジトリに含めない）:
- posts.csv: 4daea97171b044c4790f205bd91bb1ee31aa4402fc928aa41dbfc6b8bb2395b2
- comments.csv: 0689153f1a1a9cb7dfa6a57b615b5b82ac3a08f117277a9945f95215b792d451
- data.json: 0968c69a342a33f4fb3b6f5718e5c422a688a34ba85b0102533d9b016210c466
- run_report.json: 4547e86e78bdbc062e7b6c9e994e7320d77203242b6b5ea543b808289569bc77

## 先行試験

初回接続試験: https://github.com/yasujii/test/actions/runs/35937276668
最初の本体試験: https://github.com/yasujii/test/actions/runs/35938120612
本体コード検証commit: 22376ad80b9a02ea289bf09e1b9a30e85c5c06c6

## 限界と引き継ぎ

検証対象は例示投稿1件であり、ユーザー指定のインフルエンサー投稿ではない。
次ページありなので全件ではない。返信全件も未取得。サンプル成功を任意の投稿・長期安定動作の保証にしない。
Mac実機での導入・取得、GH_TOKENを使うcloud.py/run.py単体のREST完全往復、Graph API補助機能は未実通信検証。
GitHubコネクタを使った起動・実取得・ログ回収・復号・保存は実証済み。

GitHub直接利用は同じフォルダのHANDOFF.mdとcloud.pyを使う。
別配布ZIP instagram-cloud-local-setupはbootstrap.pyとrun.pyを持つ起動キットで、同じ検証済みコアを取り込み利用する。両クライアントの引数を混ぜない。
設定だけできたこと、単体テストだけ通ったことを実コメント取得の証拠にしていない。
