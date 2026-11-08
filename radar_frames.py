#!/usr/bin/env python3
"""台湾レーダーのフレーム生成（中央氣象署 O-A0058-001 / O-A0059-001）

サブコマンド:
  latest   最新の画像（O-A0058-001・キー不要のPNG）を透過処理して保存する。
           観測時刻だけ、公式JSON（キー必要）の DateTime から正確に取る。
           あわせて地上観測（O-A0003-001・キー不要・363局）を10分ごとのスナップショットとして保存する。
  backfill 過去の格子データ（O-A0059-001）から直近N時間分を画像にして穴埋めする。

出力:
  <out>/frames/YYYYMMDDHHMM.png   （UTC・透明PNG・全フレーム同じ範囲）
  <out>/obs/YYYYMMDDHHMM.json     （地上観測のスナップショット・UTC・値だけ）
  <out>/obs/stations.json         （観測地点の名前・位置・標高。ほぼ不変）
  <out>/index.json                （フレーム一覧・範囲・出典・観測の一覧）

APIキーは環境変数 CWA_API_KEY から読み、ログやエラーに出さない。
"""
import argparse
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import numpy as np
from PIL import Image

API = "https://opendata.cwa.gov.tw"

# 出力フレームの範囲（格子データ O-A0059-001 と同じ。緯度18.0〜29.0 / 経度115.0〜126.5）
SOUTH, NORTH, WEST, EAST = 18.0, 29.0, 115.0, 126.5
GRID_NX, GRID_NY = 921, 881  # 0.0125度刻み

# 画像データ O-A0058-001 は 3600x3600px で 緯度17.75〜29.25 / 経度115.0〜126.5
IMG_SIZE = 3600
IMG_SOUTH, IMG_NORTH = 17.75, 29.25

KEEP_HOURS = 3  # 保持期間

# 透過処理: 彩度(max-min)が SAT_CLEAR 未満は完全に透明、SAT_FULL 以上は不透明、間は線形
SAT_CLEAR, SAT_FULL = 15, 40
# 元画像の凡例（右下）と局名マーク（左上）は領域ごと消す（画像に対する比率）
LEGEND_X, LEGEND_Y = 0.935, 0.685
MARKER_X, MARKER_Y = 0.06, 0.23

# dBZ(0〜64) → 色。元画像の凡例（カラーバー）から読み取ったもの
PALETTE = [
    [0, 255, 255], [0, 236, 255], [0, 218, 255], [0, 200, 255], [0, 182, 255], [0, 163, 255],
    [0, 145, 255], [0, 127, 255], [0, 109, 255], [0, 91, 255], [0, 72, 255], [0, 54, 255],
    [0, 36, 255], [0, 18, 255], [0, 0, 255], [0, 255, 0], [0, 244, 0], [0, 233, 0],
    [0, 222, 0], [0, 211, 0], [0, 200, 0], [0, 190, 0], [0, 180, 0], [0, 170, 0],
    [0, 160, 0], [0, 150, 0], [51, 171, 0], [102, 192, 0], [153, 213, 0], [204, 234, 0],
    [255, 255, 0], [255, 233, 0], [255, 222, 0], [255, 222, 0], [255, 200, 0], [255, 184, 0],
    [255, 168, 0], [255, 152, 0], [255, 136, 0], [255, 120, 0], [255, 96, 0], [255, 72, 0],
    [255, 48, 0], [255, 24, 0], [255, 0, 0], [244, 0, 0], [233, 0, 0], [222, 0, 0],
    [211, 0, 0], [200, 0, 0], [190, 0, 0], [180, 0, 0], [170, 0, 0], [160, 0, 0],
    [150, 0, 0], [171, 0, 51], [192, 0, 102], [213, 0, 153], [234, 0, 204], [255, 0, 255],
    [234, 0, 255], [213, 0, 255], [192, 0, 255], [171, 0, 255], [150, 0, 255],
]

ATTRIBUTION = "提供機關／交通部中央氣象署 雷達整合回波圖"

# 地上観測 O-A0003-001（有人・主要局。キー不要の公開S3。最新のみで過去は無いため、自分で溜める）
OBS_URL = "https://cwaopendata.s3.ap-northeast-1.amazonaws.com/Observation/O-A0003-001.json"
# スナップショットの値の並び（地点IDごとに、この順の配列）
OBS_KEYS = ["wd", "ws", "temp", "rh", "rain", "gust", "pres", "weather"]
OBS_ATTRIBUTION = "提供機關／交通部中央氣象署 地面觀測資料"


class ApiError(Exception):
    """取得・変換の失敗（URLにキーが含まれるため、URLは載せない）"""


def http_get(url, tries=3, timeout=90):
    last = "不明"
    for i in range(tries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "taiwan-radar-frames (personal weather app)"}
            )
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            last = "HTTP %d" % e.code
            if e.code in (400, 401, 403, 404):
                break  # 再試行しても直らない
        except Exception as e:  # 通信エラー・タイムアウト
            last = type(e).__name__
        time.sleep(2 * (i + 1))
    raise ApiError(last)


def get_key():
    key = os.environ.get("CWA_API_KEY", "").strip()
    if not key:
        raise SystemExit("環境変数 CWA_API_KEY が未設定です")
    return key


# ── 時刻・ファイル名 ──
def parse_dt(s):
    """ISO8601（+08:00付き）→ UTCのdatetime"""
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def frame_id(dt):
    return dt.strftime("%Y%m%d%H%M")


def iso_z(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def cst_str(dt):
    """historyapi の timeFrom 用（台北時間・タイムゾーン表記なし）"""
    return dt.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%dT%H:%M:%S")


# ── 画像処理 ──
def key_out(rgb):
    """不透明なRGB画像 → 透過RGBA。彩度の低い画素（灰色の背景・格子線・県境・文字）を透明にする"""
    h, w, _ = rgb.shape
    a = rgb.astype(np.int16)
    sat = a.max(axis=2) - a.min(axis=2)
    ramp = np.round((sat - SAT_CLEAR) / (SAT_FULL - SAT_CLEAR) * 255)
    alpha = np.where(sat < SAT_CLEAR, 0, np.where(sat < SAT_FULL, ramp, 255)).astype(np.uint8)
    alpha[int(h * LEGEND_Y):, int(w * LEGEND_X):] = 0  # 凡例
    alpha[: int(h * MARKER_Y), : int(w * MARKER_X)] = 0  # 局名マーク
    return np.dstack([rgb, alpha])


def process_image(png_bytes):
    """O-A0058-001 のPNG → 格子データと同じ範囲・大きさの透過PNG（PIL Image）"""
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    if img.size != (IMG_SIZE, IMG_SIZE):
        raise ApiError("画像サイズが想定外: %dx%d" % img.size)
    rgba = key_out(np.asarray(img))
    span = IMG_NORTH - IMG_SOUTH
    top = round((IMG_NORTH - NORTH) / span * IMG_SIZE)
    bottom = round((IMG_NORTH - SOUTH) / span * IMG_SIZE)
    crop = Image.fromarray(rgba[top:bottom], "RGBA")
    # 透明部分の色が混ざらないよう、乗算済みアルファで縮小する
    small = crop.convert("RGBa").resize((GRID_NX, GRID_NY), Image.Resampling.BOX)
    return small.convert("RGBA")


def grid_to_image(xml_text):
    """O-A0059-001 の格子XML → 透過PNG（PIL Image）。無効値(-999)は透明"""
    m = re.search(r"<Content>(.*?)</Content>", xml_text, re.S | re.I)
    if not m:
        raise ApiError("格子データの本体が見つからない")
    vals = np.array(re.split(r"[,\s]+", m.group(1).strip()), dtype=float)
    if vals.size != GRID_NX * GRID_NY:
        raise ApiError("格子の大きさが想定外: %d" % vals.size)
    g = vals.reshape(GRID_NY, GRID_NX)  # 先頭行が南（実データで検証済み）
    pal = np.array(PALETTE, dtype=np.uint8)
    idx = np.clip(np.floor(g + 0.5), 0, len(PALETTE) - 1).astype(int)
    rgba = np.zeros((GRID_NY, GRID_NX, 4), dtype=np.uint8)
    mask = g >= 0
    rgba[mask, :3] = pal[idx[mask]]
    rgba[mask, 3] = 255
    return Image.fromarray(rgba[::-1], "RGBA")  # 北が上になるよう反転


# ── 出力（フレーム・索引） ──
def ensure_out(out):
    os.makedirs(os.path.join(out, "frames"), exist_ok=True)
    os.makedirs(os.path.join(out, "obs"), exist_ok=True)
    nojekyll = os.path.join(out, ".nojekyll")
    if not os.path.exists(nojekyll):
        open(nojekyll, "w").close()


def load_index(out):
    path = os.path.join(out, "index.json")
    if not os.path.exists(path):
        return []
    try:
        return json.load(open(path, encoding="utf-8")).get("frames", [])
    except (ValueError, OSError):
        return []


def load_obs_index(out):
    path = os.path.join(out, "index.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("obs", {}).get("frames", [])
    except (ValueError, OSError):
        return []


def save_index(out, frames, obs=None):
    """obs=None のときは、既存の index.json の観測の一覧を引き継ぐ"""
    if obs is None:
        obs = load_obs_index(out)
    frames = sorted(frames, key=lambda f: f["t"])
    body = {
        "updated": iso_z(datetime.now(timezone.utc)),
        "bounds": [[SOUTH, WEST], [NORTH, EAST]],
        "attribution": ATTRIBUTION,
        "frames": frames,
    }
    if obs:
        body["obs"] = {
            "stations": "obs/stations.json",
            "keys": OBS_KEYS,
            "attribution": OBS_ATTRIBUTION,
            "frames": sorted(obs, key=lambda f: f["t"]),
        }
    path = os.path.join(out, "index.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(body, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


# ── 地上観測 ──
def _num(x, nd=1):
    """欠測(-99など)や変換できない値は None。値は小数 nd 桁に丸める"""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if v <= -90 else round(v, nd)


def parse_obs(data):
    """O-A0003-001 の JSON → (観測時刻(UTC), 地点の一覧, {地点ID: [OBS_KEYSの順の値]})"""
    try:
        stations = data["cwaopendata"]["dataset"]["Station"]
    except (KeyError, TypeError):
        raise ApiError("観測データの形式が想定外")
    meta, values, tmax = [], {}, None
    for s in stations:
        w = s.get("WeatherElement") or {}
        geo = s.get("GeoInfo") or {}
        wgs = next((c for c in geo.get("Coordinates", []) if c.get("CoordinateName") == "WGS84"), None)
        sid = s.get("StationId")
        if not wgs or not sid:
            continue
        lat, lon = _num(wgs.get("StationLatitude"), 5), _num(wgs.get("StationLongitude"), 5)
        if lat is None or lon is None:
            continue
        t = s.get("ObsTime", {}).get("DateTime")
        if t:
            d = parse_dt(t)
            tmax = d if tmax is None or d > tmax else tmax
        weather = w.get("Weather")
        weather = weather if weather and weather != "-99" else None
        meta.append({"id": sid, "name": s.get("StationName", ""), "county": geo.get("CountyName", ""),
                     "town": geo.get("TownName", ""), "lat": lat, "lon": lon, "alt": _num(geo.get("StationAltitude"))})
        values[sid] = [
            _num(w.get("WindDirection"), 0), _num(w.get("WindSpeed")), _num(w.get("AirTemperature")),
            _num(w.get("RelativeHumidity"), 0), _num((w.get("Now") or {}).get("Precipitation")),
            _num((w.get("GustInfo") or {}).get("PeakGustSpeed")), _num(w.get("AirPressure")), weather,
        ]
    if not values or tmax is None:
        raise ApiError("観測データが空")
    return tmax, meta, values


def add_obs(out, obs, dt, values):
    name = "obs/%s.json" % frame_id(dt)
    with open(os.path.join(out, name), "w", encoding="utf-8") as f:
        json.dump({"t": iso_z(dt), "k": OBS_KEYS, "v": values}, f, ensure_ascii=False, separators=(",", ":"))
    obs[:] = [o for o in obs if o["file"] != name]
    obs.append({"t": iso_z(dt), "file": name})


def write_stations(out, meta):
    """地点の一覧が変わったときだけ書く。書いたら True"""
    path = os.path.join(out, "obs", "stations.json")
    body = {"stations": meta}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                if json.load(f) == body:
                    return False
        except (ValueError, OSError):
            pass
    with open(path, "w", encoding="utf-8") as f:
        json.dump(body, f, ensure_ascii=False, separators=(",", ":"))
    return True


def prune_obs(out, obs, now):
    """保持期間より古いスナップショットと、索引にないファイルを削除する。変更があれば True"""
    cutoff = now - timedelta(hours=KEEP_HOURS)
    keep = [o for o in obs if parse_dt(o["t"]) >= cutoff]
    changed = len(keep) != len(obs)
    names = {o["file"] for o in keep}
    odir = os.path.join(out, "obs")
    for fn in os.listdir(odir):
        if fn.endswith(".json") and fn != "stations.json" and "obs/" + fn not in names:
            os.remove(os.path.join(odir, fn))
            changed = True
    obs[:] = keep
    return changed


def update_obs(out, obs, now):
    """最新の地上観測を取得して保存する。変更があれば True"""
    tmax, meta, values = parse_obs(json.loads(http_get(OBS_URL)))
    changed = write_stations(out, meta)
    if tmax < now - timedelta(hours=KEEP_HOURS):
        print("観測が古すぎるため保存しない:", iso_z(tmax))
        return changed
    if any(o["file"] == "obs/%s.json" % frame_id(tmax) for o in obs):
        print("最新の観測は取得済み:", frame_id(tmax))
        return changed
    add_obs(out, obs, tmax, values)
    print("観測を追加:", frame_id(tmax), "(%d局)" % len(values))
    return True


def add_frame(out, frames, dt, image):
    name = "frames/%s.png" % frame_id(dt)
    image.save(os.path.join(out, name), optimize=True)
    frames[:] = [f for f in frames if f["file"] != name]
    frames.append({"t": iso_z(dt), "file": name})


def prune(out, frames, now):
    """保持期間より古いフレームと、索引にないPNGを削除する。変更があれば True"""
    cutoff = now - timedelta(hours=KEEP_HOURS)
    keep = [f for f in frames if parse_dt(f["t"]) >= cutoff]
    changed = len(keep) != len(frames)
    names = {f["file"] for f in keep}
    fdir = os.path.join(out, "frames")
    for fn in os.listdir(fdir):
        if fn.endswith(".png") and "frames/" + fn not in names:
            os.remove(os.path.join(fdir, fn))
            changed = True
    frames[:] = keep
    return changed


def set_output(changed):
    """GitHub Actions へ「変更があったか」を渡す（Actions外では何もしない）"""
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a") as f:
            f.write("changed=%s\n" % ("true" if changed else "false"))


# ── コマンド ──
def cmd_latest(args):
    key = get_key()
    ensure_out(args.out)
    frames = load_index(args.out)
    obs = load_obs_index(args.out)
    now = datetime.now(timezone.utc)
    changed = False
    failed = False

    # レーダー（失敗しても観測の取得は続ける）
    try:
        meta = json.loads(http_get("%s/fileapi/v1/opendataapi/O-A0058-001?Authorization=%s&format=JSON" % (API, key)))
        ds = meta["cwaopendata"]["dataset"]
        dt = parse_dt(ds["DateTime"])
        name = "frames/%s.png" % frame_id(dt)
        if any(f["file"] == name for f in frames):
            print("最新フレームは取得済み:", frame_id(dt))
        else:
            add_frame(args.out, frames, dt, process_image(http_get(ds["resource"]["ProductURL"])))
            changed = True
            print("追加:", frame_id(dt))
    except ApiError as e:
        print("レーダーの取得に失敗:", e, file=sys.stderr)
        failed = True

    # 地上観測（失敗してもレーダーには影響しない）
    try:
        if update_obs(args.out, obs, now):
            changed = True
    except ApiError as e:
        print("観測の取得に失敗:", e, file=sys.stderr)
        failed = True

    if prune(args.out, frames, now):
        changed = True
    if prune_obs(args.out, obs, now):
        changed = True
    if changed:
        save_index(args.out, frames, obs)
    set_output(changed)
    return 1 if failed else 0


def cmd_backfill(args):
    key = get_key()
    ensure_out(args.out)
    frames = load_index(args.out)
    obs = load_obs_index(args.out)
    now = datetime.now(timezone.utc)
    hours = args.hours
    q = urllib.parse.urlencode({
        "Authorization": key, "format": "JSON",
        "limit": hours * 6 + 6, "timeFrom": cst_str(now - timedelta(hours=hours)),
    })
    meta = json.loads(http_get("%s/historyapi/v1/getMetadata/O-A0059-001?%s" % (API, q)))
    times = meta["dataset"]["resources"]["resource"]["data"]["time"]
    have = {f["file"] for f in frames}
    added = 0
    for t in times:
        dt = parse_dt(t["DateTime"])
        if "frames/%s.png" % frame_id(dt) in have:
            continue
        try:
            image = grid_to_image(http_get(t["ProductURL"]).decode("utf-8", "replace"))
        except ApiError as e:
            print("スキップ:", frame_id(dt), e)
            continue
        add_frame(args.out, frames, dt, image)
        added += 1
        print("穴埋め:", frame_id(dt))
        time.sleep(1)  # 相手サーバーへの負荷を抑える
    changed = added > 0
    if prune(args.out, frames, now):
        changed = True
    if prune_obs(args.out, obs, now):
        changed = True
    if changed:
        save_index(args.out, frames, obs)
    set_output(changed)
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("latest", help="最新フレームを追加")
    a.add_argument("--out", default="data")
    a.set_defaults(fn=cmd_latest)
    b = sub.add_parser("backfill", help="過去データで穴埋め")
    b.add_argument("--out", default="data")
    b.add_argument("--hours", type=int, default=3)
    b.set_defaults(fn=cmd_backfill)
    args = p.parse_args(argv)
    try:
        return args.fn(args) or 0
    except ApiError as e:
        print("エラー:", e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
