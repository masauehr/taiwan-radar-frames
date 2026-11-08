# cron-job.org 設定ガイド — 配信の定期起動（taiwan_radar_frames）

[← MANUAL.md に戻る](MANUAL.md)

作成: 2026-11-08（設定時の質疑も含めて記録）

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

期限が切れると、cron-job.orgからの起動が401で失敗し、配信が止まる。**期限の1週間前（12月1日ごろ）までに更新する。**

1. GitHubで、手順Aと同じ設定で新しいトークンを作る（または既存のトークンを Regenerate）。
2. cron-job.org のジョブの ADVANCED → Headers → `Authorization` の Value を `Bearer <新トークン>` に更新して保存。
3. **TEST RUN** で 204 を確認。
4. 古いトークンは、GitHubの Fine-grained tokens 一覧から **Delete**（失効）する。

## 8. セキュリティ

- トークンは最小権限（1リポジトリ・Actionsのみ）。漏れても、コードの書き換えはできないが、Actionsの実行を起動・停止されうる。
- トークンは第三者サービス（cron-job.org）に預けることになる。疑わしいときは、GitHubでトークンを即座に Delete すればよい。
- トークンをチャット・ファイル・コミットに残さない（このガイドにも値は書かない）。

## 9. 止まったとき・やめるとき

- cron-job.org が止まると、配信は更新されず、アプリのレーダーが古くなる（「観測から◯分前」が大きくなる）。
- 手動で最新に戻す: `gh workflow run update.yml --repo masauehr/taiwan-radar-frames -f backfill_hours=3`（過去3時間分を穴埋め）。
- やめる場合: cron-job.org のジョブを削除し、GitHubのトークンを Delete する。
