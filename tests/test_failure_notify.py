"""ops/failure_notify.py の単体テスト（ネットワーク・Slack不使用）"""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

_TMP = tempfile.TemporaryDirectory()
os.environ["TRF_CONF_DIR"] = _TMP.name
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ops"))
import failure_notify as f  # noqa: E402

NOW = datetime(2026, 11, 10, 3, 0, 0, tzinfo=timezone.utc)


def run(id_, minutes_ago, conclusion="success", status="completed"):
    t = (NOW - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"databaseId": id_, "conclusion": conclusion, "status": status, "createdAt": t,
            "url": "https://github.com/x/actions/runs/%d" % id_, "event": "workflow_dispatch"}


class TestDecide(unittest.TestCase):
    def test_all_success_no_message(self):
        msgs, st = f.decide([run(3, 2), run(2, 7), run(1, 12)], {}, NOW)
        self.assertEqual(msgs, [])
        self.assertFalse(st["stale_notified"])

    def test_new_failure_notified_once(self):
        runs = [run(3, 2), run(2, 7, "failure"), run(1, 12)]
        msgs, st = f.decide(runs, {}, NOW)
        self.assertEqual(len(msgs), 1)
        self.assertIn("1 回失敗", msgs[0])
        self.assertIn("復旧済み", msgs[0])
        msgs2, _ = f.decide(runs, st, NOW)   # 同じ失敗は再通知しない
        self.assertEqual(msgs2, [])

    def test_failures_batched_and_ongoing(self):
        runs = [run(3, 2, "failure"), run(2, 7, "failure")]
        msgs, _ = f.decide(runs, {}, NOW)
        self.assertEqual(len(msgs), 2)       # 失敗2回の通知 + 成功なしの停止通知
        self.assertIn("2 回失敗", msgs[0])
        self.assertIn("継続", msgs[0])

    def test_cancelled_and_running_ignored(self):
        runs = [run(3, 1, None, "in_progress"), run(2, 7, "cancelled"), run(1, 12)]
        msgs, _ = f.decide(runs, {}, NOW)
        self.assertEqual(msgs, [])

    def test_old_failure_ignored(self):
        runs = [run(2, 2), run(1, 60 * 20, "failure")]   # 20時間前の失敗
        msgs, _ = f.decide(runs, {}, NOW)
        self.assertEqual(msgs, [])

    def test_stale_notified_once_then_reset(self):
        runs = [run(1, 45)]                  # 最後の成功が45分前
        msgs, st = f.decide(runs, {}, NOW)
        self.assertEqual(len(msgs), 1)
        self.assertIn("成功が 30 分以上", msgs[0])
        self.assertTrue(st["stale_notified"])
        msgs2, st2 = f.decide(runs, st, NOW)  # 停止中は再通知しない
        self.assertEqual(msgs2, [])
        msgs3, st3 = f.decide([run(2, 1)] + runs, st2, NOW)  # 復旧
        self.assertEqual(msgs3, [])
        self.assertFalse(st3["stale_notified"])
        msgs4, _ = f.decide(runs, st3, NOW)   # 再び停止したらまた通知
        self.assertEqual(len(msgs4), 1)

    def test_no_runs_is_stale(self):
        msgs, _ = f.decide([], {}, NOW)
        self.assertEqual(len(msgs), 1)


if __name__ == "__main__":
    unittest.main()
