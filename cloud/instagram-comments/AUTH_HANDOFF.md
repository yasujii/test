# 認証付き腸活コメント収集：実行状態

更新: 2026-09-24

## 現在の結果

ユーザーは自分のInstagramアカウントでのログインと、他者の公開腸活投稿のコメント読み取りを明示的に許可した。旧HANDOFFの匿名限定方針から、今回だけ認証付き最小試験を追加した。いいね・DM・投稿・非公開データの収集は許可された作業ではない。

パスワードやメールアドレスの値はGitHubファイル・Actions入力・ログ・引き継ぎ資料へ記載していない。

- run: https://github.com/yasujii/test/actions/runs/35946515311
- commit: a438c85f5efc2933afed00ac218adc6e44a56aa5
- job: 107465548397
- UTC 2026-09-24 02:16:02 の実ログ: `SECRETS_MISSING`
- 未提供の専用Secrets名: `IG_RESEARCH_LOGIN`, `IG_RESEARCH_PASSWORD`
- `login_attempted: false`
- 認証・コメント取得のステップはskip。パスワードが正しいかは未確認。Instagram側の認証エラーではない。

ChatGPTコンテナからのwww.instagram.com、i.instagram.com、api.github.com、pypi.orgのDNS確認は全てgaierrorだった。接続済みGitHubツールにはSecretsの登録・更新機能がなく、fetchもSecrets APIを契約上受け付けない。権限の抜け道、公開リポジトリへの秘密情報保存、別サービスへの無断移送を使わない。

## 配置済み

- `.github/workflows/instagram-authenticated-comments.yml`
- `cloud/instagram-comments/authenticated_probe.py`
- `cloud/instagram-comments/login-request.json`

既存の匿名取得ワークフローはそのまま。認証付きワークフローはこのブランチのlogin-request.jsonの更新pushでのみ自動起動する。定期実行は追加していない。

## 残る本人操作

GitHubのyasujii/test → Settings → Secrets and variables → Actions → New repository secretで、上記2項目を登録する。値は本人が入力し、ChatGPTに再送しない。チャットに出たパスワードは変更後の値を登録するよう案内する。

必要項目をSecretsとして設定したとの返信が来たら、担当AIはパスワード値を尋ねず、復号用RSA鍵を自分の実行環境で新規生成し、公開鍵とjob_idだけをlogin-request.jsonへ更新して実行する。秘密鍵はリポジトリ・公開ログに置かない。

同じ作業環境で元の復号鍵が確実に残っている場合は、失敗jobを再実行してもよい。別のチャットへ移った場合に元の鍵があると仮定しない。

## 認証付き試験の仕様

instagrapi 3.0.13、ログイン呼び出し1回。challenge、CAPTCHA、2FA要求、401/403/429は停止し、回避や無限再試行をしない。IG_RESEARCH_2FA_CODEは、本人が求められた通常の2FAを別途許可・入力した時だけ使用する。メールやSMSのコードを勝手に探さない。

公開アカウントであることをuser_info_v1で確認してからコメントを保存。今回の上限は3投稿×30件、計90件の候補。所有者・自分のコメントを除外し、一般コメント投稿者の識別情報は保存しない。受信候補から実体験の5件を担当AIが確認する。90件すべてを有用な実体験として扱わず、100件の目標達成も別途確認する。

投稿・コメント・取得報告の4ファイルはZIP化し、AES-256-GCM、RSA-OAEP-SHA256で暗号化してartifactへ返す。平文やセッションは公開ログ・artifactへ出さない。セッション永続化はこの最小試験には実装していないため、継続運用の前に別途安全なセッション保管を検討する。

## 検証範囲

認証付きコードのオフライン模擬テスト8件は成功。公開状態チェック、所有者除外、認証失敗1回停止、CSV数式対策、認証承諾・URL検証を確認した。これは実ログイン・実コメント取得の証拠ではない。

この時点ではログイン0回・今回の実コメント0件。ユーザーへ成功と報告しない。

参照: https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets
参照: https://github.com/subzeroid/instagrapi
