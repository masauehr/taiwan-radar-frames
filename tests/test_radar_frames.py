"""radar_frames.py の単体テスト（ネットワーク不使用）"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import radar_frames as rf  # noqa: E402


class TestTime(unittest.TestCase):
    def test_parse_and_id(self):
        dt = rf.parse_dt("2026-11-07T16:40:00+08:00")
        self.assertEqual(rf.frame_id(dt), "202611070840")  # UTCに変換
        self.assertEqual(rf.iso_z(dt), "2026-11-07T08:40:00Z")

    def test_cst_str(self):
        dt = datetime(2026, 11, 7, 8, 40, tzinfo=timezone.utc)
        self.assertEqual(rf.cst_str(dt), "2026-11-07T16:40:00")


class TestKeyOut(unittest.TestCase):
    def test_gray_becomes_transparent_and_color_stays(self):
        rgb = np.full((100, 100, 3), 200, dtype=np.uint8)  # 灰色の背景
        rgb[50, 50] = [0, 200, 0]  # 緑のエコー
        out = rf.key_out(rgb)
        self.assertEqual(out[10, 90, 3], 0)  # 灰色は透明
        self.assertEqual(out[50, 50, 3], 255)  # 色付きは不透明
        self.assertEqual(tuple(out[50, 50, :3]), (0, 200, 0))

    def test_legend_and_marker_regions_cleared(self):
        rgb = np.zeros((1000, 1000, 3), dtype=np.uint8)
        rgb[..., 1] = 200  # 全面を緑（彩度あり）
        out = rf.key_out(rgb)
        self.assertEqual(out[900, 980, 3], 0)  # 右下の凡例
        self.assertEqual(out[10, 10, 3], 0)  # 左上の局名マーク
        self.assertEqual(out[500, 500, 3], 255)  # それ以外は残る


class TestGrid(unittest.TestCase):
    def _xml(self, values):
        return "<a><Content>%s</Content></a>" % ",".join(str(v) for v in values)

    def test_orientation_and_invalid(self):
        n = rf.GRID_NX * rf.GRID_NY
        vals = [-999.0] * n
        vals[0] = 30.0  # 先頭行・先頭列 = 最南・最西
        img = rf.grid_to_image(self._xml(vals))
        self.assertEqual(img.size, (rf.GRID_NX, rf.GRID_NY))
        px = np.asarray(img)
        self.assertEqual(px[rf.GRID_NY - 1, 0, 3], 255)  # 画像では左下（南西）
        self.assertEqual(px[0, 0, 3], 0)  # 無効値は透明

    def test_bad_size(self):
        with self.assertRaises(rf.ApiError):
            rf.grid_to_image(self._xml([1, 2, 3]))


class TestProcessImage(unittest.TestCase):
    def test_output_size_and_transparency(self):
        import io
        base = Image.new("RGB", (rf.IMG_SIZE, rf.IMG_SIZE), (200, 200, 200))
        buf = io.BytesIO()
        base.save(buf, "PNG")
        out = rf.process_image(buf.getvalue())
        self.assertEqual(out.size, (rf.GRID_NX, rf.GRID_NY))
        self.assertEqual(np.asarray(out)[..., 3].max(), 0)  # 灰色だけなら全面透明

    def test_wrong_size_rejected(self):
        import io
        buf = io.BytesIO()
        Image.new("RGB", (10, 10)).save(buf, "PNG")
        with self.assertRaises(rf.ApiError):
            rf.process_image(buf.getvalue())


class TestPrune(unittest.TestCase):
    def test_old_frames_and_orphans_removed(self):
        now = datetime(2026, 11, 7, 9, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as out:
            rf.ensure_out(out)
            frames = []
            img = Image.new("RGBA", (4, 4))
            for mins in (10, 170, 200):  # 10分前・170分前は保持、200分前は削除
                rf.add_frame(out, frames, now - timedelta(minutes=mins), img)
            img.save(os.path.join(out, "frames", "orphan.png"))
            self.assertTrue(rf.prune(out, frames, now))
            self.assertEqual(len(frames), 2)
            self.assertEqual(sorted(os.listdir(os.path.join(out, "frames"))), sorted(
                os.path.basename(f["file"]) for f in frames))
            rf.save_index(out, frames)
            with open(os.path.join(out, "index.json"), encoding="utf-8") as fp:
                body = json.load(fp)
            self.assertEqual(body["bounds"], [[18.0, 115.0], [29.0, 126.5]])
            self.assertEqual([f["t"] for f in body["frames"]], sorted(f["t"] for f in body["frames"]))
            self.assertIn("交通部中央氣象署", body["attribution"])


class TestHttp(unittest.TestCase):
    def test_error_message_has_no_url(self):
        # 存在しないホストでも、例外メッセージにURL(=キー)が含まれない
        try:
            rf.http_get("http://127.0.0.1:1/?Authorization=SECRET", tries=1, timeout=1)
        except rf.ApiError as e:
            self.assertNotIn("SECRET", str(e))
            return
        self.fail("例外が出なかった")


if __name__ == "__main__":
    unittest.main()
