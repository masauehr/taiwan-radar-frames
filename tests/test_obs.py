"""radar_frames.py の地上観測（スナップショット）まわりの単体テスト（ネットワーク不使用）"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import radar_frames as rf  # noqa: E402


def station(sid, name, t="2026-11-08T09:40:00+08:00", **w):
    el = {"Weather": "陰", "WindDirection": "70.0", "WindSpeed": "3.1", "AirTemperature": "22.0",
          "RelativeHumidity": "87", "AirPressure": "1019.8",
          "Now": {"Precipitation": "11.5"}, "GustInfo": {"PeakGustSpeed": "10.7"}}
    el.update(w)
    return {"StationName": name, "StationId": sid, "ObsTime": {"DateTime": t},
            "GeoInfo": {"Coordinates": [{"CoordinateName": "TWD67", "StationLatitude": "25.1", "StationLongitude": "121.7"},
                                        {"CoordinateName": "WGS84", "StationLatitude": "25.133314", "StationLongitude": "121.740475"}],
                        "StationAltitude": "26.7", "CountyName": "基隆市", "TownName": "仁愛區"},
            "WeatherElement": el}


def data(*stations):
    return {"cwaopendata": {"dataset": {"Station": list(stations)}}}


class TestParseObs(unittest.TestCase):
    def test_values_and_meta(self):
        t, meta, vals = rf.parse_obs(data(station("466940", "基隆")))
        self.assertEqual(t, datetime(2026, 11, 8, 1, 40, tzinfo=timezone.utc))  # UTCに変換
        self.assertEqual(meta[0]["name"], "基隆")
        self.assertAlmostEqual(meta[0]["lat"], 25.13331, places=4)   # TWD67ではなくWGS84
        self.assertEqual(meta[0]["alt"], 26.7)
        self.assertEqual(vals["466940"], [70, 3.1, 22.0, 87, 11.5, 10.7, 1019.8, "陰"])
        self.assertEqual(len(vals["466940"]), len(rf.OBS_KEYS))

    def test_missing_values_become_none(self):
        _, _, vals = rf.parse_obs(data(station("X", "山", WindSpeed="-99", AirPressure="-99", Weather="-99",
                                                GustInfo={"PeakGustSpeed": "-99.0"})))
        v = dict(zip(rf.OBS_KEYS, vals["X"]))
        self.assertIsNone(v["ws"])
        self.assertIsNone(v["pres"])
        self.assertIsNone(v["gust"])
        self.assertIsNone(v["weather"])
        self.assertEqual(v["temp"], 22.0)

    def test_skips_stations_without_wgs84(self):
        bad = station("B", "座標なし")
        bad["GeoInfo"]["Coordinates"] = [{"CoordinateName": "TWD67", "StationLatitude": "1", "StationLongitude": "2"}]
        _, meta, vals = rf.parse_obs(data(station("A", "あり"), bad))
        self.assertEqual([m["id"] for m in meta], ["A"])
        self.assertEqual(list(vals), ["A"])

    def test_bad_format_or_empty(self):
        with self.assertRaises(rf.ApiError):
            rf.parse_obs({"x": 1})
        with self.assertRaises(rf.ApiError):
            rf.parse_obs(data())


class TestObsStorage(unittest.TestCase):
    def test_add_stations_index_and_prune(self):
        now = datetime(2026, 11, 8, 3, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as out:
            rf.ensure_out(out)
            obs, frames = [], []
            t_new = now - timedelta(minutes=20)
            t_old = now - timedelta(hours=4)
            rf.add_obs(out, obs, t_new, {"A": [1, 2, 3, 4, 5, 6, 7, "晴"]})
            rf.add_obs(out, obs, t_old, {"A": [None] * 8})
            self.assertTrue(rf.write_stations(out, [{"id": "A", "name": "あ"}]))
            self.assertFalse(rf.write_stations(out, [{"id": "A", "name": "あ"}]))  # 同じ内容は書かない
            open(os.path.join(out, "obs", "orphan.json"), "w").close()
            self.assertTrue(rf.prune_obs(out, obs, now))
            self.assertEqual([o["t"] for o in obs], [rf.iso_z(t_new)])
            self.assertEqual(sorted(os.listdir(os.path.join(out, "obs"))),
                             sorted(["stations.json", os.path.basename(obs[0]["file"])]))
            rf.save_index(out, frames, obs)
            with open(os.path.join(out, "index.json"), encoding="utf-8") as fp:
                body = json.load(fp)
            self.assertEqual(body["obs"]["stations"], "obs/stations.json")
            self.assertEqual(body["obs"]["keys"], rf.OBS_KEYS)
            self.assertEqual(len(body["obs"]["frames"]), 1)
            with open(os.path.join(out, obs[0]["file"]), encoding="utf-8") as fp:
                snap = json.load(fp)
            self.assertEqual(snap["v"]["A"][7], "晴")
            self.assertEqual(snap["k"], rf.OBS_KEYS)

    def test_save_index_keeps_existing_obs_when_not_given(self):
        # レーダーだけ更新する処理（backfill等）で、観測の一覧が消えないこと
        with tempfile.TemporaryDirectory() as out:
            rf.ensure_out(out)
            rf.save_index(out, [], [{"t": "2026-11-08T01:40:00Z", "file": "obs/202611080140.json"}])
            rf.save_index(out, [])
            self.assertEqual(len(rf.load_obs_index(out)), 1)

    def test_no_obs_key_when_empty(self):
        with tempfile.TemporaryDirectory() as out:
            rf.ensure_out(out)
            rf.save_index(out, [])
            with open(os.path.join(out, "index.json"), encoding="utf-8") as fp:
                self.assertNotIn("obs", json.load(fp))   # 観測が無い間は obs を出さない（既存アプリへの影響なし）


class TestUpdateObs(unittest.TestCase):
    def setUp(self):
        self._orig = rf.http_get

    def tearDown(self):
        rf.http_get = self._orig

    def test_adds_once_and_skips_old(self):
        now = datetime(2026, 11, 8, 2, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as out:
            rf.ensure_out(out)
            obs = []
            rf.http_get = lambda url, **k: json.dumps(data(station("A", "あ"))).encode()  # 観測 01:40 UTC
            self.assertTrue(rf.update_obs(out, obs, now))
            self.assertEqual(len(obs), 1)
            rf.update_obs(out, obs, now)          # 同じ観測は再保存しない
            self.assertEqual(len(obs), 1)
            old_obs = []
            self.assertFalse(rf.update_obs(out, old_obs, now + timedelta(hours=5)))  # 古すぎる観測は保存しない
            self.assertEqual(old_obs, [])


if __name__ == "__main__":
    unittest.main()
