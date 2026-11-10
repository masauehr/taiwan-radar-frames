# cron-job.org 設定ガイド — 配信の定期起動（taiwan_radar_frames）

[← MANUAL.md に戻る](MANUAL.md)

作成: 2026-11-08（設定時の質疑も含めて記録）

## 0. 概要 — cron-job.org とは・使う意味・仕組み

### 0-1. cron-job.org とは（ひとことで）
**「決めた時刻になったら、指定したURLにアクセスしてくれる」無料のWebサービス**。目覚まし時計のようなもので、自分のパソコンを起動しておかなくても、インターネット上で時刻どおりに動く。

- もとになっている **cron（クーロン）** は、Linux/Mac にある「毎日9時にこの処理を実行する」といった**定期実行**の仕組み。cron-job.org は、それを**Web上で借りられる**サービス。
- できること: 時刻（5分ごと・毎時・毎日など）を決める → その時刻にURLへHTTPリクエスト（GET/POST）を送る → 結果（成功・失敗・応答時間）を履歴に残す。
- アカウント登録（無料）とブラウザの設定画面だけで使え、サーバーやプログラムは要らない。
- 自分ではデータを処理しない。**「時刻になったら合図を送る」だけ**。実際の画像の取得・変換・公開は、合図を受けた側（このプロジェクトでは GitHub Actions）が行う。

### 0-2. なぜ使うのか（使う意味）
このプロジェクトは、台湾の気象データ（レーダー・観測）を **GitHub Actions の `update-frames` が自動で取得・変換して公開**している。これを**5分ごと**に動かす手段が必要になる。

| 手段 | 問題 |
|------|------|
| GitHub Actions 自身の定期実行（`schedule`） | **時刻が保証されない**。GitHub が混んでいると遅れたり、実行が間引かれたりする（GitHub の仕様）。実際に、10分ごとの設定が約3〜4時間おきにしか動かず、作成直後の約2時間は1回も動かなかった |
| 手元のMacの launchd | Mac が起動・スリープ解除・ネット接続している間しか動かない。**Mac に頼らない方針**（配信は GitHub 側で完結させたい） |
| **cron-job.org** | 5分ごとに時刻どおり合図を送ってくれる。無料。Mac不要 |

つまり、**「時刻の正確さ」だけを cron-job.org に任せ、「処理」は GitHub Actions に任せる**という役割分担にしている。GitHub の `schedule` は、cron-job.org が止まったときの**予備**として残してある。

### 0-3. 技術的な仕組み
```
cron-job.org（5分ごと）
   │  HTTPS で POST を1回送る
   ▼
GitHub の REST API  ─ 「workflow_dispatch（手動実行）」を要求する窓口
   │  認証: 専用トークン（Authorization ヘッダー）
   ▼
GitHub Actions の update-frames が起動
   │  台湾の最新レーダー・観測を取得 → 変換 → data ブランチへ公開
   ▼
GitHub Pages が更新され、アプリ（mobile_app / taiwan_weather_viewer）が読む
```

- **`workflow_dispatch`** は、GitHub Actions に「人が手動で実行する」ためのきっかけ。ボタンを押す代わりに、**APIで同じことをプログラムから行える**。cron-job.org はこのAPIを時刻どおりに叩くだけ。
- **リクエストの中身**: 宛先 `POST https://api.github.com/repos/masauehr/taiwan-radar-frames/actions/workflows/update.yml/dispatches`、本文 `{"ref":"main"}`（どのブランチのワークフローを動かすか）。成功すると **204 No Content**（本文なし）が返る。
- **認証（トークン）**: GitHub に「このリクエストは本人のもの」と伝える**合言葉**。`Authorization: Bearer <トークン>` のヘッダーで送る。このトークンは、**このリポジトリのActionsを動かす権限だけ**（Fine-grained トークン）で、**期限付き**（90日）。コードの書き換えはできない。万一漏れても被害が小さいようにするため。
- **重複実行の防止**: 合図が重なっても、`update.yml` の `concurrency` により、同時には1つだけ実行され、順番に処理される。
- **何度呼んでも安全（冪等）**: 新しいコマ（レーダー画像）が無ければ、ワークフローは**何も変更せずに終わる**。そのため、5分ごとに合図を送っても、無駄な公開や履歴の増加は起きない。台湾側の公開は観測の約8〜16分後なので、**5分ごとに見に行けば、新しいコマが出てから最大5分で取り込める**。
- **時刻の基準**: 5分ごとなので時刻のずれは影響しない。時刻を指定する設定にする場合は、cron-job.org の画面は日本時間が既定である点に注意。

### 0-4. 気をつけること
- **トークンに期限がある**（このリポジトリは **2026-12-08 00:15 UTC**）。切れると 401 で起動に失敗し、配信が止まる。期限の14日前から Slack DM で通知する（10節）。
- 外部サービスに**トークンを預ける**ことになる。権限を最小（このリポジトリのActionsだけ）・期限付きにして、リスクを抑えている。トークンの値は、チャットやファイル、リポジトリに**書かない**。
- cron-job.org のジョブは、**失敗が続くと自動で無効化**されることがある。止まっていないかを確認する。`update-frames` の失敗や、成功が30分以上ない停止は、Slack DM で通知する（11節）。
- 無料プランには、実行間隔や回数などの制限がある（詳細は cron-job.org の公式ページで確認）。現在の5分ごと（1日288回）は範囲内で動いている。
- cron-job.org 自体が止まっても、予備の `schedule` が動く。ただし数時間おきにしか動かないので、配信は古くなる（アプリの「観測から◯分前」の表示で気づける）。

## 1. 何のための設定か

`taiwan-radar-frames`（台湾レーダーの配信）は、GitHub Actions の `update-frames` ワークフローが最新画像を追加して公開する。この定期起動を、**外部の無料cronサービス（cron-job.org）から5分ごとに行う**ための設定。

### 背景（なぜ必要だったか）
- ワークフローに `schedule`（cron）を書いていたが、GitHubの定期実行は**大幅に間引かれた**。
  - 設定は10分ごと（のちに `7,17,27,37,47,57` 分へ変更）だったが、実際の実行は約3〜4時間おき（16:40、20:31、23:53 UTC）。作成直後の約2時間は1回も実行されなかった。
  - その結果、配信が止まり、アプリのレーダーが古くなった（アプリの「観測から◯分前」の表示で気づけた）。
- 対策として、cron-job.org から GitHub API で `workflow_dispatch`（手動実行）を呼ぶ方式にした。Macは使わない方針のため。

### 全体像
```
cron-job.org（5分ごと）
  → POST https://api.github.com/repos/masauehr/taiwan-radar-frames/actions/workflows/update.yml/dispatches
       （専用トークンで認証）
  → GitHub Actions: update-frames が起動 → 最新フレームを追加 → data ブランチ（GitHub Pages）へ公開
  → アプリ（mobile_app / taiwan_weather_viewer）が index.json を読む
```
`schedule`（GitHub側のcron）はそのまま残してある。将来GitHub側が動いても、同時実行は1つずつ処理される（`concurrency`）ので問題ない。

## 2. 手順A: GitHubで専用トークンを作る（最小権限）

GitHub → 右上のアイコン → **Settings** → 左下の **Developer settings** → **Personal access tokens** → **Fine-grained tokens** → **Generate new token**

| 項目 | 設定 |
|------|------|
| Token name | `taiwan-radar-frames-dispatch` |
| Expiration | 90日を指定（実際の期限は **2026-12-08 00:15 UTC**。cron-job.orgのテスト応答ヘッダーの `github-authentication-token-expiration` で確認した） |
| Resource owner | `masauehr` |
| Repository access | **Only select repositories** → `taiwan-radar-frames` だけ |
| Repository permissions | **Actions: Read and write**（Metadata は自動で Read-only）。ほかは変更しない |

- 生成後に表示されるトークン（`github_pat_` で始まる）は**一度しか表示されない**。コピーして、手順Bのヘッダーにだけ貼る。**チャットやファイルには貼らない**。
- このトークンでできるのは、この1つのリポジトリのActionsの実行だけ。コードの書き換え（push）はできない。

## 3. 手順B: cron-job.org でジョブを作る

https://cron-job.org でアカウントを作り（無料）、**Create cronjob**。

### COMMON タブ
| 項目 | 設定 |
|------|------|
| Title | `taiwan-radar-frames-dispatch`（トークン名と同じにすると見分けやすい。動作には影響しない） |
| URL | `https://api.github.com/repos/masauehr/taiwan-radar-frames/actions/workflows/update.yml/dispatches` |
| Execution schedule | **5分ごと**（Every 5 minutes） |

5分ごとにした理由: 台湾側の公開は観測から8〜16分遅れるため、新しいコマの取り込みを早めるため。新しいコマがなければ、ワークフローは何も変更せずに終わる。

### ADVANCED タブ
| 項目 | 設定 |
|------|------|
| Requires HTTP authentication | **オフのまま**（トークンは下のヘッダーで渡す） |
| Time zone | `Asia/Tokyo`（そのまま） |
| Request method | **POST** |
| Request body | `{"ref":"main"}` |

**Headers**（**+ ADD** で行を増やす。左が Key、右が Value）

| Key | Value |
|-----|-------|
| `Authorization` | `Bearer ` ＋ 半角スペース1つ ＋ コピーしたトークン（全体で `Bearer github_pat_...` の1行） |
| `Accept` | `application/vnd.github+json` |
| `X-GitHub-Api-Version` | `2022-11-28` |
| `Content-Type` | `application/json` |
| `User-Agent` | `cron-job.org`（GitHub APIは必須） |

### NOTIFICATIONS タブ
失敗時の通知を有効にしておくと、トークン切れなどに気づける（任意）。

### 保存と動作確認
1. **TEST RUN** を押し、応答が **204 No Content** なら成功（実際の結果: 204、所要1.85秒。応答ヘッダーに `x-accepted-github-permissions: actions=write`）。
2. テスト画面を **CLOSE** で閉じる。
3. **CREATE（保存）を押す。押さないと定期実行は始まらない。**
4. ジョブ一覧に出て、次回実行の時刻が表示されれば有効。

## 4. 設定時の質疑（記録）

| 質問 | 回答 |
|------|------|
| ジョブのタイトルは `taiwan-radar-frames-dispatch` でよいか | よい。一覧での見分け用で動作に影響しない。GitHubのトークン名と揃えると、どのトークンのジョブか分かりやすい |
| `Bearer` に続くトークンの意味と、どこに入れるか。Value欄か | **Authorization行の Value 欄**に `Bearer <トークン>` の形で入れる。`Bearer` は「これから渡すものはトークンです」という決まった合言葉で、後ろに半角スペースを1つ置く |
| Content-Type はどうするか | 画面の青い案内の **ADD CONTENT-TYPE HEADER NOW** で自動追加できる。値が `application/json` になっているか確認する |
| テスト実行の画面を終了して、Createを押すのか | その通り。**CLOSE** で閉じてから **CREATE**。テストは動作確認だけで、保存しないと定期実行は始まらない |
| HTTP authentication のスイッチは | **オフのまま**。認証はAuthorizationヘッダーで行う |

## 5. 動作の確認（GitHub側）

```bash
# 実行履歴: workflow_dispatch が5分間隔で並べば成功
gh run list --repo masauehr/taiwan-radar-frames --limit 10 --json event,status,conclusion,createdAt --jq '.[] | [.createdAt, .event, .status, .conclusion] | @tsv'

# 配信の最新コマ（UTC）
gh api "repos/masauehr/taiwan-radar-frames/contents/frames?ref=data" --jq '[.[].name] | "コマ数 \(length) / 最新 \(.[-1])"'
```

アプリ側でも、レーダータブの「観測から◯分前」が、おおむね10〜25分以内であれば正常（台湾側の公開の遅れを含む）。

## 6. トラブルシューティング

| 症状（cron-job.orgの応答） | 原因と対処 |
|------|------|
| 401 Unauthorized | トークンが違う・期限切れ・`Bearer ` の付け忘れ。トークンを再発行して、ヘッダーを更新 |
| 403 Forbidden | トークンの権限不足。**Actions: Read and write**、対象が `taiwan-radar-frames` であることを確認 |
| 404 Not Found | URLの誤り（リポジトリ名・`update.yml`）。またはトークンの対象リポジトリが違う |
| 422 Unprocessable | Request body が `{"ref":"main"}` になっていない（ブランチ名の誤り） |
| 204 だが実行が増えない | GitHubのActionsが停止していないか、`update.yml` が `main` にあるかを確認 |
| ジョブが自動で無効になった | 連続失敗でcron-job.orgが停止した可能性。原因を直してからジョブを再度有効化 |

## 7. トークンの更新（期限: 2026-12-08 00:15 UTC ＝ 日本時間 2026-12-08 09:15）

期限が切れると、cron-job.orgからの起動が401で失敗し、配信が止まる。**期限の1週間前（12月1日ごろ）までに更新する。** 期限の14日前から、Slack DMで通知が届く（10節）。

1. GitHubで、手順Aと同じ設定で新しいトークンを作る（または既存のトークンを Regenerate）。
2. cron-job.org のジョブの ADVANCED → Headers → `Authorization` の Value を `Bearer <新トークン>` に更新して保存。
3. **TEST RUN** で 204 を確認。
4. 古いトークンは、GitHubの Fine-grained tokens 一覧から **Delete**（失効）する。
5. **Slack通知の期限ファイルを新しい期限に書き換える**（10節）。`echo '新しい期限' > ~/.config/taiwan_radar_frames/token_expiry`。忘れると、古い期限で「期限切れ」の通知が毎日届く。

## 8. セキュリティ

- トークンは最小権限（1リポジトリ・Actionsのみ）。漏れても、コードの書き換えはできないが、Actionsの実行を起動・停止されうる。
- トークンは第三者サービス（cron-job.org）に預けることになる。疑わしいときは、GitHubでトークンを即座に Delete すればよい。
- トークンをチャット・ファイル・コミットに残さない（このガイドにも値は書かない）。

## 9. 止まったとき・やめるとき

- cron-job.org が止まると、配信は更新されず、アプリのレーダーが古くなる（「観測から◯分前」が大きくなる）。
- 手動で最新に戻す: `gh workflow run update.yml --repo masauehr/taiwan-radar-frames -f backfill_hours=3`（過去3時間分を穴埋め）。
- やめる場合: cron-job.org のジョブを削除し、GitHubのトークンを Delete する。

## 10. トークン期限の Slack 通知（Bluesky自動投稿と同じ方式）

トークンの期限が近づいたら Slack DM で知らせる。Bluesky自動投稿（`automated-data-collector`）と同じく、**Macのlaunchd が毎日スクリプトを起動し、`openclaw message send` 経由で Slack DM に送る**。

| 項目 | 内容 |
|------|------|
| スクリプト | `ops/token_expiry_notify.py`（標準ライブラリのみ。通知の部品は他プロジェクトと共有せずコピーして持つ） |
| 起動 | `ops/com.user.taiwan_radar_token_notify.plist`（毎日 9:00 JST。スリープ中だった日は復帰後に1回） |
| 通知する段階 | 期限の**14日前・7日前・3日前・2日前・1日前**。期限切れ後は**毎日**（更新して `token_expiry` を書き換えるまで）。同じ段階は1回だけ |
| 失敗時 | 送れなかった段階は状態を更新せず、次回の実行で再送する。通知の失敗でスクリプトは止めない |
| 注意 | OpenClaw Gateway が止まっていると届かない（Bluesky通知と同じ制約）。Macが起動していない日は実行されない |

### 設定ファイル（`~/.config/taiwan_radar_frames/`・git管理外）

| ファイル | 内容 |
|------|------|
| `token_expiry` | トークンの期限を**1行**で。例: `2026-12-08T00:15:02Z`（cron-job.orgのテスト実行の応答ヘッダー `github-authentication-token-expiration` の値） |
| `slack_to` | 通知先（1行・`slack:U…`）。Bluesky通知と同じ宛先でよい |
| `notify_state.json` | 通知済みの段階（自動生成。`token_expiry` の値が変わると自動でリセット） |
| `notify.log` | launchdの出力 |

### 導入手順

```bash
mkdir -p ~/.config/taiwan_radar_frames
echo '2026-12-08T00:15:02Z' > ~/.config/taiwan_radar_frames/token_expiry
cp ~/.config/bsky/slack_to ~/.config/taiwan_radar_frames/slack_to        # 中身は表示しない
chmod 600 ~/.config/taiwan_radar_frames/slack_to
cp ~/projects/taiwan_radar_frames/ops/com.user.taiwan_radar_token_notify.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.user.taiwan_radar_token_notify.plist
```

動作確認:

```bash
python3 ~/projects/taiwan_radar_frames/ops/token_expiry_notify.py --test            # テスト通知（Slackに届けば設定は正常）
python3 ~/projects/taiwan_radar_frames/ops/token_expiry_notify.py --dry-run --now 2026-12-02T00:00:00Z   # 通知文だけ表示（送らない）
launchctl print gui/$(id -u)/com.user.taiwan_radar_token_notify | head                # 登録の確認
```

### トークンを更新したとき（7節の最後に必ず行う）

```bash
echo '新しい期限（例 2027-01-07T00:15:02Z）' > ~/.config/taiwan_radar_frames/token_expiry
```

期限が変わると状態が自動でリセットされ、新しいトークンの期限に向けて、また14日前から通知される。**書き換えを忘れると、古い期限で「期限切れ」の通知が毎日届く**（通知が届いたら、まずこれを疑う）。

### 止める・やめる

```bash
launchctl bootout gui/$(id -u)/com.user.taiwan_radar_token_notify
rm ~/Library/LaunchAgents/com.user.taiwan_radar_token_notify.plist
```

## 11. 失敗・停止の Slack 通知（2026-11-10〜）

`update-frames` が失敗したとき、または成功が止まったとき Slack DM で知らせる。10節と同じく、Macのlaunchd がスクリプトを起動し、`gh` で直近の実行を調べて `openclaw message send` で送る（通知のみ。再実行や修復はしない）。

| 項目 | 内容 |
|------|------|
| スクリプト | `ops/failure_notify.py`（標準ライブラリ＋`gh`）。テストは `tests/test_failure_notify.py` |
| 起動 | `ops/com.user.taiwan_radar_failure_notify.plist`（15分おき。スリープ中の分は復帰後に1回） |
| 失敗の通知 | `failure` / `timed_out` / `startup_failure` の実行が新しく見つかったとき、まとめて1通。実行URLと「直近は成功（復旧済み）か継続中か」を添える。同じ実行は再通知しない。6時間より古い失敗は無視 |
| 停止の通知 | 成功した実行が**30分以上**ない（cron-job.org停止・トークン失効(401)・GitHub障害など）とき。復旧するまで1回だけ |
| 通知しない | `cancelled`（同時実行の取り消し）・実行中 |
| 設定 | `~/.config/taiwan_radar_frames/slack_to` を10節と共用。状態は `failure_state.json`、ログは `failure_notify.log` |
| 注意 | OpenClaw Gateway が止まっている・Macが起動していないと届かない（Bluesky通知と同じ制約）。単発の失敗は GitHub 側の一時障害のことが多い（2026-11-09に403で1回、18時台に4回） |

### 導入手順（`slack_to` は10節で設定済みなら不要）

```bash
cp ~/projects/taiwan_radar_frames/ops/com.user.taiwan_radar_failure_notify.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.user.taiwan_radar_failure_notify.plist
```

動作確認:

```bash
python3 ~/projects/taiwan_radar_frames/ops/failure_notify.py --test       # テスト通知
python3 ~/projects/taiwan_radar_frames/ops/failure_notify.py --dry-run    # 通知文だけ表示（送らない・状態も更新しない）
launchctl print gui/$(id -u)/com.user.taiwan_radar_failure_notify | head  # 登録の確認
```

止める: `launchctl bootout gui/$(id -u)/com.user.taiwan_radar_failure_notify` と、`~/Library/LaunchAgents/` の plist を削除。
