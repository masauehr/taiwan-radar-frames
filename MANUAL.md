# 技術マニュアル — 台湾レーダーのフレーム配信（taiwan_radar_frames）

[← README.md に戻る](README.md)

## 1. 目的と全体像

台湾中央氣象署（CWA）のレーダー合成エコー図を、**過去3時間分の透過PNG（10分間隔）として静的配信**する。iPhone天気予報アプリ（`mobile_app`）の台湾レーダーが、開いた直後から動画で見られるようにするためのデータ配信用リポジトリ。

```
GitHub Actions（10分ごと）
  ├─ latest   : 最新画像（O-A0058-001, キー不要のPNG）を透過処理して追加
  │             観測時刻だけ公式JSON（キー必要）の DateTime から正確に取得
  ├─ backfill : 過去の格子データ（O-A0059-001, キー必要）から直近N時間分を画像化（手動実行）
  └─ 公開     : data ブランチへ1コミットで置き換え → GitHub Pages で配信
アプリ（mobile_app）→ index.json と frames/*.png を読むだけ（キーを持たない）
```

- **Macは使わない**（GitHub Actionsのみ）。
- APIキーは GitHub の secret（`CWA_API_KEY`）にだけ置く。公開物（Pages）にもコードにも含めない。

## 2. 配信物（data ブランチ）

| パス | 内容 |
|------|------|
| `index.json` | フレーム一覧・範囲・出典・更新時刻 |
| `frames/YYYYMMDDHHMM.png` | 透明PNG（**UTC**・921×881px・約35〜60KB）。全フレームが同じ範囲 |
| `.nojekyll` | Pagesのjekyll処理を無効化 |

```json
{
 "updated": "2026-11-07T09:02:50Z",
 "bounds": [[18.0, 115.0], [29.0, 126.5]],
 "attribution": "提供機關／交通部中央氣象署 雷達整合回波圖",
 "frames": [{"t": "2026-11-07T08:10:00Z", "file": "frames/202611070810.png"}]
}
```

- `bounds` は `[[南緯度, 西経度], [北緯度, 東経度]]`（Leaflet の `L.imageOverlay` にそのまま渡せる）。
- `t` は観測時刻（UTC）。画像の `Last-Modified` からの推定ではなく、公式JSONの `DateTime` なので正確。
- GitHub Pages は `Access-Control-Allow-Origin: *` を返すため、別オリジンのアプリから `fetch` できる。
- **出典表示は規約上の義務**（`attribution` の文言を、利用側の画面に表示すること）。

## 3. データ源と変換

| データ | 取得 | 用途 |
|------|------|------|
| `O-A0058-001`（画像） | 公開S3（キー不要）。同名ファイルを10分ごとに上書き。観測時刻は公式JSON `fileapi/v1/opendataapi/O-A0058-001`（キー必要）の `DateTime` | 定期取得（`latest`） |
| `O-A0059-001`（格子） | `historyapi/v1/getMetadata`（キー必要）で一覧→各コマのXML（約8.9MB）。過去約10日分・10分間隔 | 穴埋め（`backfill`） |

**画像の透過処理（`key_out`）**: 元画像は灰色の背景・格子線・県境・文字・凡例入りの不透明PNG。彩度（max−min）15未満を透明、15〜40を半透明、40以上を不透明にしてエコーだけを残す。右下の凡例と左上の局名マークは領域ごと消す。さらに緯度18.0〜29.0に切り出し、乗算済みアルファで921×881に縮小する。

**格子の画像化（`grid_to_image`）**: 921×881・0.0125度刻み・dBZ。先頭行が**南**（画像との突き合わせで検証済み。一致率92%、逆向きは24%）。無効値 `-999` は透明、0dBZ以上を `PALETTE`（元画像の凡例のカラーバーから読み取った65色）で着色する。

**2経路の整合**: 同じ時刻（2026-11-07 08:50 UTC）で、格子由来と画像由来のフレームを並べて、エコーの形・色がほぼ一致することを確認済み。

## 4. 初回セットアップ（実施済みの手順）

1. 公開リポジトリ `masauehr/taiwan-radar-frames` を作り、`main` にコードを置く。
2. secret を登録（キーは画面に出さず、自分のシェルで実行）:
   ```bash
   grep '^CWA_API_KEY=' ~/projects/mobile_app/.env | cut -d= -f2- | gh secret set CWA_API_KEY --repo masauehr/taiwan-radar-frames
   ```
3. Actions の **update-frames** を手動実行（`backfill_hours` = `3`）。`data` ブランチが作られ、過去3時間分が入る。
4. Pages を有効化（Settings → Pages → Branch: `data` / root）。公開URL: https://masauehr.github.io/taiwan-radar-frames/index.json

## 5. 運用

| 項目 | 内容 |
|------|------|
| 定期実行 | cron `7,17,27,37,47,57 * * * *`（毎時0分・10分などの混雑する時刻を避ける。初期の `*/10` では新規リポジトリで約45分間1回も実行されなかった）。GitHub Actions のcronは数分〜十数分遅れたり抜けたりする（過去にも遅延・欠落の実績あり）。抜けたコマは、利用側が時刻の最も近いフレームを選ぶことで吸収する |
| 履歴 | `data` ブランチは毎回1コミットに置き換える（force push）。リポジトリが肥大化しない |
| 保持 | 直近3時間（`KEEP_HOURS`）。古いフレームは自動削除 |
| 穴埋め | 手動実行で `backfill_hours` を指定。**全期間（10日・約1,440コマ・約12.8GB）は取らない**。CWAは異常な負荷で会員資格を取り消す場合がある |
| キー | CWAオープンデータの会員キー。**1年以上ログインもダウンロードもないと、アカウントとキーが削除される**。漏えい時は会員サイトで「更新授權碼」→ secret を再登録 |
| 60日ルール | 公開リポジトリは、60日間リポジトリの活動がないとスケジュール実行が自動停止されることがある。月1回、Actionsが動いているか確認する |
| テスト | `python -m unittest discover -s tests`（ネットワーク不使用・10件） |

## 6. ローカル実行

```bash
cd ~/projects/taiwan_radar_frames
pip install -r requirements.txt
export CWA_API_KEY="$(grep '^CWA_API_KEY=' ../mobile_app/.env | cut -d= -f2-)"   # 値は表示しない
python radar_frames.py backfill --out data --hours 1     # 過去1時間の穴埋め
python radar_frames.py latest --out data                 # 最新1コマを追加
```

`data/` は `.gitignore` 済み（公開は `data` ブランチ。`main` には入れない）。

## 7. 制約・今後

- 時刻が欠けるコマは、台湾側だけ表示を飛ばすか、近いコマで代用する（利用側で決める）。
- 降水強度の凡例は気象庁用と別（台湾はdBZ）。
- キー不要の最新PNGが将来URL変更された場合は、公式JSON（`ProductURL`）を辿っているため、自動的に追随する。
