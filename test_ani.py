import os
import tempfile
import time
from pathlib import Path

os.environ["ANI_STATE"] = tempfile.mkdtemp()
import server as s  # noqa: E402


def tmpdir():
    return Path(tempfile.mkdtemp()).resolve()


def old(path):
    """Backdate a file so background sync treats it as fully downloaded."""
    t = time.time() - 3600
    os.utime(path, (t, t))
    return path


# --- episode numbers from real-world release names
cases = {
    "[Shiniori-Raws] Black Clover - 01 (BD 1280x720 x265 10bit AAC).mp4": 1,
    "ブラッククローバー.S01E001.アスタとユノ.WEBRip.Netflix.ja[cc].srt": 1,
    "ブラッククローバー.S01E009.獣(けもの).WEBRip.Netflix.ja[cc].srt": 9,
    "ブラッククローバー 第3話.srt": 3,
    "Black Clover - 12.mkv": 12,
    "[Coalgirls]_Show_03_(1280x720_Blu-ray_FLAC)_[ABCD1234].mkv": 3,
    "Show - 05 - Title.mkv": 5,
    "Show.05.mkv": 5,
    "Show E03.mkv": 3,
    "Show EP07 [1080p].mkv": 7,
    "Show 03 1080p.mkv": 3,
    "Kaiju No. 8 (2024) - 03.mkv": 3,
    "Mob Psycho 100 - 04 [1080p].mkv": 4,
    "Show - 05v2 [1080p].mkv": 5,
    "Show - 05.5.mkv": 5.5,
    "no number here.mkv": None,
}
for name, want in cases.items():
    assert s.episode(name) == want, (name, s.episode(name), want)

# --- SRT parsing is lenient and sorted
srt = ("1\n00:00:05,000 --> 00:00:06,000\n<i>二番</i>\n\n\n"   # extra blank line
       "garbage block\n\n"                                    # no timestamp: skipped
       "00:00:01.5 --> 00:00:03,000 X1:10\nこんにちは\n二行目\n \n"  # no index, '.', position tag, space line
       "3\n00:00:59,600 --> 00:01:00,000\n{\\an8}最後\n\n"
       "4\n00:01:01,000 --> 00:01:02,000\n<b></b>\n")         # empty after tags: dropped
lines = s.parse_srt(srt)
assert [ln[0] for ln in lines] == [["こんにちは", "二行目"], ["二番"], ["最後"]], lines
assert lines[0][1] == 1500 and lines[1][1] == 5000
assert lines[2][2] == "0:01:00"  # 59.6s rounds to a valid label

# --- ASS: drawings, tag-only and duplicate border layers dropped; no Format line needed
ass = ("[Script Info]\n[Events]\n"
       "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,{\\p1}m 0 0 l 10 0 10 10{\\p0}\n"
       "Dialogue: 0,0:00:03.00,0:00:04.00,Default,,0,0,0,,{\\pos(1,2)}\n"
       "Dialogue: 0,0:00:05.00,0:00:06.00,Default,,0,0,0,,{\\bord3}はい\\Nいいえ\n"
       "Dialogue: 1,0:00:05.00,0:00:06.00,Default,,0,0,0,,はい\\Nいいえ\n")
assert [ln[0] for ln in s.parse_ass(ass)] == [["はい", "いいえ"]], s.parse_ass(ass)

# --- card matching: punctuation, whitespace and markup ignored
assert s.normalize_str("こら…　<b>元気</b>すぎるぞ…") == s.normalize_str("<i>こら</i> 元気すぎるぞ")
assert s.normalize_str("…！？") == ""

# --- HTTP Range
assert s.parse_range(None, 100) is None
assert s.parse_range("bytes=0-", 100) == (0, 99)
assert s.parse_range("bytes=10-19", 100) == (10, 19)
assert s.parse_range("bytes=90-500", 100) == (90, 99)
assert s.parse_range("bytes=-10", 100) == (90, 99)
assert s.parse_range("bytes=-", 100) is None
for bad in ("bytes=50-10", "bytes=500-", "bytes=-0"):
    try:
        s.parse_range(bad, 100)
        raise AssertionError(bad)
    except ValueError:
        pass

# --- folders: dotfiles ignored, symlinked episodes allowed, missing folders harmless
show, dl = tmpdir(), tmpdir()
(show / "Show - 01.mkv").write_bytes(b"v")
(show / "._Show - 01.mkv").write_bytes(b"x")
(dl / "Show - 02.mkv").write_bytes(b"v")
(show / "Show - 02.mkv").symlink_to(dl / "Show - 02.mkv")
(show / "Show - 01.srt").write_text("x")
(show / "._Show - 01.srt").write_text("x")
s.STATE["series"][str(show)] = {"subs": None}
assert [v.name for v in s.episodes(show)] == ["Show - 01.mkv", "Show - 02.mkv"]
assert s.find_sub(show / "Show - 01.mkv", show).name == "Show - 01.srt"
assert s.allowed(str(show / "Show - 02.mkv")) == show / "Show - 02.mkv"
assert s.allowed(str(show / "._Show - 01.mkv")) is None
assert s.allowed(str(dl / "Show - 02.mkv")) is None  # target folder itself isn't a series
assert s.episodes(show / "gone") == []

# --- syncing: output name never collides with a user's .ja.srt; failures don't loop
assert s.synced_path(show / "Show - 01.mkv").name == "Show - 01.ani.srt"
s.ALASS = "/nonexistent/alass"
old(show / "Show - 01.mkv")
calls = []
real_sync = s.sync
s.sync = lambda *a: calls.append(a) or real_sync(*a)
s.sync_season(str(show))
for _ in range(50):
    if str(show) not in s.BG_DIRS:
        break
    time.sleep(0.05)
assert str(show) not in s.BG_DIRS and len(calls) == 1, calls  # ep 2 has no subtitle, ep 1 fails once
assert s.ensure_synced(show / "Show - 01.mkv") == show / "Show - 01.srt" and len(calls) == 1  # no re-run
s.FAILED.clear()
assert s.needs_sync(show / "Show - 01.mkv")
(dl / "Show - 02.mkv").write_bytes(b"vv")  # fresh write: not settled, so background skips it
assert not s.needs_sync(show / "Show - 02.mkv")
s.sync = real_sync

# --- card checks: empty sentences never match; multi-card notes filled once; failures retried
s.SESSION = s.Session("id", "f.mkv", [[["…"], 0, "", 500, ""], [["はい"], 1000, "", 2000, ""]], ["", "はい"])
s.OPTIONS.update(deck="Mining", sentence="Sentence", expression="Word", picture="Picture", audio="Audio")
filled, fail_once = [], [True]


def fake_invoke(action, **p):
    if action == "findCards":
        return [1, 2, 3]
    if action == "cardsInfo":
        return [{"cardId": 1, "note": 10, "fields": {"Sentence": {"value": ""}}},
                {"cardId": 2, "note": 20, "fields": {"Sentence": {"value": "はい！"}}},
                {"cardId": 3, "note": 20, "fields": {"Sentence": {"value": "はい！"}}}]


def fake_update(sess, delay, note, idx, *a):
    if fail_once.pop() if fail_once else False:
        raise s.AnkiError("busy")
    filled.append((note, idx))


s.invoke, s.update_note = fake_invoke, fake_update
s.check(5)
assert filled == [] and 20 not in s.DONE_NOTES  # failed: retried next time
s.check(5)
assert filled == [(20, 1)], filled  # note 10 (empty sentence) untouched; note 20 once
s.check(0.5)  # nothing played yet: no new fills
assert filled == [(20, 1)]

# --- dropped files are found by name + size and moved into the series
root = tmpdir()
(root / "dl" / "nested").mkdir(parents=True)
(root / "show").mkdir()
(root / "dl" / "nested" / "Show - 02.mp4").write_bytes(b"x" * 10)
(root / "dl" / "Show - 02.mp4").write_bytes(b"x" * 5)  # same name, different size
s.SEARCH_ROOTS = [root / "dl"]
s.STATE["series"][str(root / "show")] = {"subs": None}
assert s.find_original("Show - 02.mp4", 10) == [root / "dl" / "nested" / "Show - 02.mp4"]
s.FAILED[Path("x")] = (0, 0)
assert s.adopt(str(root / "show"), "Show - 02.mp4", 10) == root / "show" / "Show - 02.mp4"
assert not (root / "dl" / "nested" / "Show - 02.mp4").exists() and not s.FAILED
assert s.adopt(str(root / "show"), "Show - 02.mp4", 10) == root / "show" / "Show - 02.mp4"  # idempotent
for bad in [("/nope", "a.mp4", 1), (str(root / "show"), "a.exe", 1), (str(root / "show"), "missing.mp4", 1),
            (str(root / "show"), "../../etc/x.mp4", 1), (str(root / "show"), "._x.mp4", 1)]:
    try:
        s.adopt(*bad)
        raise AssertionError(bad)
    except (ValueError, LookupError):
        pass

print("ok")
