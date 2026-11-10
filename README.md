# taiwan_radar_frames

台湾中央氣象署（CWA）のレーダー合成エコー図を、**過去3時間分の透過PNG（10分間隔）**として GitHub Pages で静的配信する。iPhone天気予報アプリ（[mobile_app](https://github.com/masauehr/iphone-weather-app)）の台湾レーダーを、開いた直後から動画で見られるようにするためのデータ配信用リポジトリ。

詳しくは [MANUAL.md](MANUAL.md)（技術仕様）と [DATA_OVERVIEW.md](DATA_OVERVIEW.md)（台湾データの特徴・蓄積サービス・今回の修正のまとめ）を参照。

- **配信URL**: https://masauehr.github.io/taiwan-radar-frames/index.json
- **更新**: GitHub Actions を cron-job.org から5分ごとに起動（Macは使わない。設定は [CRON_JOB_ORG.md](CRON_JOB_ORG.md)）
- **出典（表示義務）**: 提供機關／交通部中央氣象署 雷達整合回波圖（政府資料開放授權條款）

## この配信を使っているアプリ

| アプリ | 公開URL | リポジトリ | 使い方 |
|------|------|------|------|
| 台湾天気ビューア（台湾特化・日本語/中国語併記） | [taiwan-weather-viewer](https://masauehr.github.io/taiwan-weather-viewer/) | [masauehr/taiwan-weather-viewer](https://github.com/masauehr/taiwan-weather-viewer) | レーダーと観測を1画面に重ねて、過去3時間を共通スライダーで再生 |
| 衛星レーダーアプリ（iPhone天気予報アプリのWeb版） | [iphone-weather-app](https://masauehr.github.io/iphone-weather-app/) | [masauehr/iphone-weather-app](https://github.com/masauehr/iphone-weather-app) | レーダー・衛星タブの[台湾]・[台湾観測]ボタン |

## 配信物

```
index.json              レーダーのフレーム一覧・範囲 bounds・出典・更新時刻、観測の一覧(obs)
obs/YYYYMMDDHHMM.json   地上観測のスナップショット（UTC・値だけ・363局・約18KB）
obs/stations.json       観測地点の名前・位置・標高
frames/YYYYMMDDHHMM.png 透明PNG（UTC・921×881px）。範囲は緯度18.0〜29.0 / 経度115.0〜126.5
```

アプリ側の使い方（Leaflet）:

```js
fetch(BASE + 'index.json').then(r => r.json()).then(idx => {
  idx.frames.forEach(f => L.imageOverlay(BASE + f.file, idx.bounds, {opacity: 0}).addTo(map));
});
```

## 台湾データの特徴（要点）

- 気象署（CWA）のレーダー画像・地上観測（363局）は**最新の1回分しか公開されない**ため、10分ごとに溜めて過去3時間分を配信している。
- **公式APIはキー（無料の会員登録）が必要**だが、画像と観測の本体は**キー不要の公開ファイル**でも取れる。キーが要るのは観測時刻（`DateTime`）の取得と、過去の格子データ（穴埋め）だけで、**GitHub の secret にだけ置く**。
- 地上観測の癖: 欠測は`-99`／風向は「吹いてくる方位（度）」／雨量は**当日累積**／気圧は**測站気圧**で観測は約29局のみ（アプリは標高500m以下を海面換算して表示・簡易的な推定）。
- 時刻は台北時間（日本より1時間遅い）。公開は観測の約8〜20分後。
- 出典表示（提供機關／交通部中央氣象署）が義務。

## 蓄積サービス（要点）

- **GitHub Actions**（`update-frames`）が取得・変換 → **GitHub Pages**（`data` ブランチ・毎回1コミットで置き換え）で配信。保持は3時間。
- 定期起動は **cron-job.org から5分ごと**（GitHub の `schedule` は数時間おきにしか動かなかったため）。トークン期限（2026-12-08）の前と、`update-frames` の**失敗・成功停止（30分以上）**のときに **Slack DM** で通知（Macのlaunchd。`ops/token_expiry_notify.py`・`ops/failure_notify.py`）。詳しくは [CRON_JOB_ORG.md](CRON_JOB_ORG.md)。

### 定期起動に使っている cron-job.org（概要）
- **cron-job.org** は、「決めた時刻になったら指定のURLにアクセスしてくれる」**無料の外部cronサービス**。ここでは、**5分ごと**に、**GitHub の API で `update-frames` を起動する合図**を送らせている。
- 使う理由: GitHub Actions 自身の定期実行（`schedule`）は時刻が保証されず、数時間おきにしか動かなかった（新規リポジトリでは最初の約2時間は0回）。Mac にも頼りたくないので、**時刻の正確さは cron-job.org、処理は GitHub Actions** と役割を分けた。`schedule` は予備。
- 仕組み: cron-job.org → `POST .../actions/workflows/update.yml/dispatches`（`workflow_dispatch`・専用トークン認証）→ `update-frames` が起動 → 新しいコマがあれば取り込んで公開。無ければ何もしないので、何度呼んでも安全。
- 注意: 専用トークンに**期限**がある（2026-12-08 00:15 UTC。14日前から Slack DM で通知）。失敗・停止も Slack DM で通知する。詳しい仕組み・設定・更新手順は [CRON_JOB_ORG.md](CRON_JOB_ORG.md)。

## 仕組み（概要）

| 処理 | データ | 内容 |
|------|--------|------|
| 定期（10分ごと） | `O-A0058-001`（キー不要のPNG）／`O-A0003-001`（地上観測・キー不要） | レーダーは透過処理して追加（観測時刻だけ公式JSONから正確に取得）。観測はスナップショットとして保存 |
| 穴埋め（手動） | `O-A0059-001`（格子・キー必要） | 過去の格子データを凡例のパレットで画像化 |

APIキーは GitHub の secret `CWA_API_KEY` にだけ置く（公開物・コードには含めない）。

## 開発

```bash
pip install -r requirements.txt
python -m unittest discover -s tests          # 単体テスト（ネットワーク不使用）
export CWA_API_KEY="$(grep '^CWA_API_KEY=' ../mobile_app/.env | cut -d= -f2-)"   # 値は表示しない
python radar_frames.py latest --out data
```
