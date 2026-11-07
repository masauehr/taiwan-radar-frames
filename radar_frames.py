#!/usr/bin/env python3
"""台湾レーダーのフレーム生成（中央氣象署 O-A0058-001 / O-A0059-001）

サブコマンド:
  latest   最新の画像（O-A0058-001・キー不要のPNG）を透過処理して保存する。
           観測時刻だけ、公式JSON（キー必要）の DateTime から正確に取る。
  backfill 過去の格子データ（O-A0059-001）から直近N時間分を画像にして穴埋めする。

出力:
  <out>/frames/YYYYMMDDHHMM.png   （UTC・透明PNG・全フレーム同じ範囲）
  <out>/index.json                （フレーム一覧・範囲・出典）

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


def save_index(out, frames):
    frames = sorted(frames, key=lambda f: f["t"])
    body = {
        "updated": iso_z(datetime.now(timezone.utc)),
        "bounds": [[SOUTH, WEST], [NORTH, EAST]],
        "attribution": ATTRIBUTION,
        "frames": frames,
    }
    path = os.path.join(out, "index.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(body, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


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
    meta = json.loads(http_get("%s/fileapi/v1/opendataapi/O-A0058-001?Authorization=%s&format=JSON" % (API, key)))
    ds = meta["cwaopendata"]["dataset"]
    dt = parse_dt(ds["DateTime"])
    changed = False
    name = "frames/%s.png" % frame_id(dt)
    if any(f["file"] == name for f in frames):
        print("最新フレームは取得済み:", frame_id(dt))
    else:
        image = process_image(http_get(ds["resource"]["ProductURL"]))
        add_frame(args.out, frames, dt, image)
        changed = True
        print("追加:", frame_id(dt))
    if prune(args.out, frames, datetime.now(timezone.utc)):
        changed = True
    if changed:
        save_index(args.out, frames)
    set_output(changed)


def cmd_backfill(args):
    key = get_key()
    ensure_out(args.out)
    frames = load_index(args.out)
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
    if changed:
        save_index(args.out, frames)
    set_output(changed)


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
        args.fn(args)
    except ApiError as e:
        print("エラー:", e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
