#!/opt/homebrew/bin/python3
"""update-frames（GitHub Actions）の失敗・停止を Slack DM で知らせる。

Bluesky自動投稿（automated-data-collector の bsky_watchdog）・token_expiry_notify.py と同じ方式:
  launchd が15分おきにこのスクリプトを起動し、`gh` で直近の実行結果を調べ、
  `openclaw message send` 経由で Slack DM に送る。通知のみ（再実行や修復は自動で行わない）。

通知する場合:
  1. 失敗（failure / timed_out / startup_failure）した実行が新しく見つかったとき（まとめて1通）
  2. 成功した実行が STALE_MIN 分以上ない（cron-job.org が止まった等）とき。復旧するまで1回だけ
通知しない場合: 取り消し(cancelled)・実行中。単発の失敗は GitHub 側の一時障害のことが多いため、
  通知文に「次の実行で成功すれば自動で復旧」と添える。

設定ファイル（~/.config/taiwan_radar_frames/ ・git管理外。token_expiry_notify.py と共用）:
  slack_to           通知先（1行。例: slack:Uxxxxxxxx）。環境変数 TRF_NOTIFY_TO があればそれを優先
  failure_state.json 通知済みの実行ID・停止通知の有無（自動で作られる）

使い方:
  failure_notify.py                 通常実行（launchdから）
  failure_notify.py --dry-run       通知文を表示するだけ（送らない・状態も更新しない）
  failure_notify.py --test          テスト通知を送る
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = "masauehr/taiwan-radar-frames"
WORKFLOW = "update.yml"
CONF = Path(os.environ.get("TRF_CONF_DIR") or (Path.home() / ".config" / "taiwan_radar_frames"))
JST = timezone(timedelta(hours=9))
FAIL = ("failure", "timed_out", "startup_failure")
STALE_MIN = 30          # 成功した実行がこの分数ない場合は「停止」とみなす（通常は5分おき）
IGNORE_OLDER_H = 6      # 初回やスリープ明けに古い失敗をまとめて送らない
KEEP_IDS = 200          # 状態ファイルに残す通知済みIDの数


def parse_time(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


def fetch_runs(limit=40):
    """gh で直近の実行を取る。失敗したら None（通知はしない。次回に回す）"""
    exe = shutil.which("gh") or "/opt/homebrew/bin/gh"
    try:
        r = subprocess.run([exe, "run", "list", "-R", REPO, "--workflow", WORKFLOW, "--limit", str(limit),
                            "--json", "databaseId,conclusion,status,createdAt,url,event"],
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            print("gh の取得に失敗:", (r.stderr or r.stdout).strip()[:200], file=sys.stderr)
            return None
        return json.loads(r.stdout)
    except Exception as e:
        print("gh の実行で例外:", e, file=sys.stderr)
        return None


def decide(runs, state, now):
    """(通知文のリスト, 新しい状態)。runs は新しい順の gh の結果"""
    notified = set(state.get("notified", []))
    msgs = []

    # 1. 新しい失敗
    new_fail = [r for r in runs
                if r.get("conclusion") in FAIL and r["databaseId"] not in notified
                and now - parse_time(r["createdAt"]) <= timedelta(hours=IGNORE_OLDER_H)]
    if new_fail:
        lines = "\n".join("  %s JST  %s" % (parse_time(r["createdAt"]).astimezone(JST).strftime("%m/%d %H:%M"), r["url"])
                          for r in sorted(new_fail, key=lambda r: r["createdAt"]))
        latest = next((r for r in runs if r.get("status") == "completed" and r.get("conclusion") != "cancelled"), None)
        now_state = "直近の実行は成功しています（復旧済み）。" if latest and latest["conclusion"] == "success" \
            else "直近の実行も失敗しています。継続している可能性があります。"
        msgs.append("【taiwan-radar-frames】❌ update-frames が %d 回失敗しました\n%s\n%s\n"
                    "単発なら GitHub 側の一時障害のことが多く、次の実行で成功すれば自動で復旧します。\n"
                    "ログ: 上のURL、または gh run view <ID> --log-failed" % (len(new_fail), lines, now_state))

    # 2. 成功した実行が長時間ない（cron-job.org 停止・トークン失効など）
    last_ok = next((parse_time(r["createdAt"]) for r in runs if r.get("conclusion") == "success"), None)
    stale = last_ok is None or now - last_ok >= timedelta(minutes=STALE_MIN)
    stale_notified = state.get("stale_notified", False)
    if stale and not stale_notified:
        when = last_ok.astimezone(JST).strftime("%m/%d %H:%M JST") if last_ok else "（直近40件に無し）"
        msgs.append("【taiwan-radar-frames】⚠️ update-frames の成功が %d 分以上ありません（最後の成功 %s）\n"
                    "cron-job.org の停止、トークンの期限切れ(401)、GitHub 側の障害などが考えられます。\n"
                    "確認: cron-job.org の履歴／ https://github.com/%s/actions" % (STALE_MIN, when, REPO))

    new_state = {
        "notified": sorted(notified | {r["databaseId"] for r in runs if r.get("conclusion") in FAIL})[-KEEP_IDS:],
        "stale_notified": stale,   # 復旧(stale=False)で解除。次の停止でまた通知する
    }
    return msgs, new_state


def _target():
    t = os.environ.get("TRF_NOTIFY_TO", "").strip()
    f = CONF / "slack_to"
    if not t and f.exists():
        t = f.read_text().strip()
    return t


def send(text):
    """Slack DM へ送る。成功したら True（失敗しても例外は出さない）"""
    to = _target()
    exe = shutil.which("openclaw") or "/opt/homebrew/bin/openclaw"
    if not to:
        print("通知先が未設定のため通知しない（" + str(CONF / "slack_to") + "）", file=sys.stderr)
        return False
    try:
        r = subprocess.run([exe, "message", "send", "--channel", "slack", "--target", to, "-m", text[:3500]],
                           capture_output=True, text=True, timeout=90)
        if r.returncode != 0:
            print("Slack通知に失敗:", (r.stderr or r.stdout).strip()[:200], file=sys.stderr)
        return r.returncode == 0
    except Exception as e:
        print("Slack通知で例外:", e, file=sys.stderr)
        return False


def load_state():
    try:
        return json.loads((CONF / "failure_state.json").read_text())
    except (OSError, ValueError):
        return {}


def save_state(state):
    CONF.mkdir(parents=True, exist_ok=True)
    (CONF / "failure_state.json").write_text(json.dumps(state, ensure_ascii=False))


def run(dry_run=False):
    runs = fetch_runs()
    if runs is None:
        return 1
    now = datetime.now(timezone.utc)
    state = load_state()
    msgs, new_state = decide(runs, state, now)
    print("%s 実行 %d件取得 ／ 通知 %d通" % (now.astimezone(JST).strftime("%m/%d %H:%M JST"), len(runs), len(msgs)))
    if dry_run:
        for m in msgs:
            print("--- 通知文（dry-run・送信しない）---\n" + m)
        return 0
    if not msgs:
        save_state(new_state)
        return 0
    if all([send(m) for m in msgs]):
        save_state(new_state)
        print("通知しました")
    else:
        print("通知できなかったため、次回の実行で再送します", file=sys.stderr)
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true", help="通知文を表示するだけ（送らない・状態も更新しない）")
    p.add_argument("--test", action="store_true", help="テスト通知を送る")
    args = p.parse_args(argv)
    if args.test:
        return 0 if send("【taiwan-radar-frames】失敗通知のテストです（このメッセージが届けば設定は正常）") else 1
    return run(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
