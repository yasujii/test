# 桜腸活 Threads 自動投稿（GitHub Actions）セットアップ手順

このフォルダは、**あなたのトークンをコードに書かずに**、GitHub の力だけで
Threads に自動投稿する仕組みです。初めての人でも進められるように書いています。

やることは大きく4つだけ。
1. トークンを再発行する
2. トークンを GitHub の「Secrets（秘密の金庫）」に入れる
3. この仕組みを「デフォルトブランチ」に置いて Actions を有効にする
4. 手動テストで1本出るか確認する

---

## 大前提：トークンは必ず「再発行」してから使う

以前チャットに貼ったトークンは、外に出てしまったので**もう使わないでください**。
Meta（developers.facebook.com）で **アプリのシークレットをリセット**し、
**長期トークン（threads_content_publish 権限つき）を再発行**してください。
新しいトークンは、このあと GitHub の Secrets に直接貼ります。**チャットには二度と貼らないでください。**

---

## 手順1：トークンを Secrets に登録する

1. GitHub でこのリポジトリを開く
2. 上のタブ **Settings**（設定）→ 左メニュー **Secrets and variables** → **Actions**
3. 緑の **New repository secret** を押す
4. 次の1つを登録する
   - **Name（名前）**: `THREADS_ACCESS_TOKEN`
   - **Secret（値）**: 再発行した長期トークンを貼り付け
5. **Add secret** を押す

> 補足：`THREADS_USER_ID` は登録しなくてOKです（トークンから自動取得します）。
> もしエラーが出る場合だけ、同じ手順で `THREADS_USER_ID` にあなたのThreadsの数字IDを登録してください。

Secrets に入れたトークンは、GitHub が暗号化して保管し、**リポジトリのファイルには一切残りません**。
これが「ファイルに置く」よりも安全な、正しいやり方です。

---

## 手順2：この仕組みを「デフォルトブランチ」に置く

GitHub Actions の**定期実行は「デフォルトブランチ」にあるワークフローだけが動きます**。
このファイル一式はいまPR（プルリクエスト）に入っているので、
**そのPRをマージ**して、デフォルトブランチに取り込んでください。

- Pull requests タブ → 該当のPRを開く → **Merge** ボタン

マージ後、**Actions** タブに「桜腸活 Threads 自動投稿」が表示されればOKです。
（初回だけ「I understand my workflows, go ahead and enable them」の確認が出たら許可してください）

---

## 手順3：手動テストで1本出るか確認する

いきなり本番を待たず、手動で試せます。

1. **Actions** タブ → 左で「桜腸活 Threads 自動投稿」を選ぶ
2. 右の **Run workflow** を押す → もう一度 **Run workflow**
3. 実行が緑（成功）になり、Threadsアプリに投稿が出れば成功です

> テストで投稿を出したくない場合：`threads-autopost/threads_posts.json` の
> いちばん上の投稿の `datetime_jst` を「いまから数分後」に一時的に変えて試す、
> という方法もあります（確認後は元に戻す）。

---

## 毎日の投稿はどう動くの？

- `threads-autopost/threads_posts.json` に、日付つきで投稿が並んでいます。
- ワークフローが JST 06:00 / 08:00 / 10:00 / 13:00 / 21:00 に自動で動き、
  **その時刻の予約を1本ずつ公開**します。
- `type` が `tree` のものは**スレッド（返信で3連結）**、`single` は**1投稿**です。
- 公開したものは `posted_log.json` に自動記録され、**二重投稿しません**。

### 投稿を追加したいとき
`threads-autopost/threads_posts.json` の `posts` に、同じ形で追記して push するだけです。

```json
{
  "id": "2026-08-22T06:00",
  "datetime_jst": "2026-08-22T06:00:00+09:00",
  "type": "tree",
  "slides": ["1枚目の本文", "2枚目の本文", "3枚目の本文"]
}
```
（`single` のときは `slides` を1個だけにする）

> 「8/22からの1週間分つくって」と私（Claude）に頼めば、この形で用意して push まで行います。

---

## 止め方（「ストップ」したいとき）

- **Actions** タブ → 「桜腸活 Threads 自動投稿」→ 右上 **…** → **Disable workflow**
- これで自動投稿は止まります。再開は同じ場所で **Enable**。

---

## 注意点（先に知っておくと安心）

- **時刻は数分ずれることがあります**。GitHub Actions の定期実行は混雑時に数分〜十数分遅れる仕様です。分単位で正確に出したい場合は Typefully 有料プランの方が向いています。
- **60日ルール**：リポジトリに60日間まったく更新がないと、GitHub が定期実行を自動停止します。定期的に push があれば大丈夫です（このスクリプトは投稿のたびに posted_log を push するので、投稿が続く限り生き続けます）。
- **クオータ**：Threads API 側の1日あたり投稿上限（通常250件/日ほど）に対して、1日5件なので問題ありません。

---

## うまくいかないときの見かた

1. **Actions** タブで、赤い（失敗した）実行を開く
2. 「投稿を実行」のログを見る
   - `401` や `OAuth` 系 → トークンが無効/期限切れ。再発行して Secrets を更新
   - `THREADS_ACCESS_TOKEN が未設定` → 手順1のSecret名が正しいか確認（`THREADS_ACCESS_TOKEN`）
   - `permission` 系でpushが失敗 → リポジトリ Settings → Actions → General →
     Workflow permissions を **Read and write permissions** にする
3. それでも不明なら、そのログを私に貼ってください。直します。
