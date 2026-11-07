# taiwan_radar_frames

台湾中央氣象署（CWA）のレーダー合成エコー図を、**過去3時間分の透過PNG（10分間隔）**として GitHub Pages で静的配信する。iPhone天気予報アプリ（[mobile_app](https://github.com/masauehr/iphone-weather-app)）の台湾レーダーを、開いた直後から動画で見られるようにするためのデータ配信用リポジトリ。

詳しくは [MANUAL.md](MANUAL.md) を参照。

- **配信URL**: https://masauehr.github.io/taiwan-radar-frames/index.json
- **更新**: GitHub Actions が10分ごと（Macは使わない）
- **出典（表示義務）**: 提供機關／交通部中央氣象署 雷達整合回波圖（政府資料開放授權條款）

## 配信物

```
index.json              フレーム一覧・範囲 bounds・出典・更新時刻
frames/YYYYMMDDHHMM.png 透明PNG（UTC・921×881px）。範囲は緯度18.0〜29.0 / 経度115.0〜126.5
```

アプリ側の使い方（Leaflet）:

```js
fetch(BASE + 'index.json').then(r => r.json()).then(idx => {
  idx.frames.forEach(f => L.imageOverlay(BASE + f.file, idx.bounds, {opacity: 0}).addTo(map));
});
```

## 仕組み（概要）

| 処理 | データ | 内容 |
|------|--------|------|
| 定期（10分ごと） | `O-A0058-001`（キー不要のPNG） | 透過処理して追加。観測時刻だけ公式JSONから正確に取得 |
| 穴埋め（手動） | `O-A0059-001`（格子・キー必要） | 過去の格子データを凡例のパレットで画像化 |

APIキーは GitHub の secret `CWA_API_KEY` にだけ置く（公開物・コードには含めない）。

## 開発

```bash
pip install -r requirements.txt
python -m unittest discover -s tests          # 単体テスト（ネットワーク不使用）
export CWA_API_KEY="$(grep '^CWA_API_KEY=' ../mobile_app/.env | cut -d= -f2-)"   # 値は表示しない
python radar_frames.py latest --out data
```
