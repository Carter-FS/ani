import os
import tempfile

os.environ["ANI_STATE"] = tempfile.mkdtemp()
import server as s  # noqa: E402

# episode numbers from real-world release names
assert s.episode("[Shiniori-Raws] Black Clover - 01 (BD 1280x720 x265 10bit AAC).mp4") == 1
assert s.episode("ブラッククローバー.S01E001.アスタとユノ.WEBRip.Netflix.ja[cc].srt") == 1
assert s.episode("ブラッククローバー.S01E009.獣(けもの).WEBRip.Netflix.ja[cc].srt") == 9
assert s.episode("ブラッククローバー 第3話.srt") == 3
assert s.episode("Black Clover - 12.mkv") == 12
assert s.episode("no number here.mkv") is None

# HTTP Range parsing for video seeking
assert s.parse_range(None, 100) is None
assert s.parse_range("bytes=0-", 100) == (0, 99)
assert s.parse_range("bytes=10-19", 100) == (10, 19)
assert s.parse_range("bytes=90-500", 100) == (90, 99)
assert s.parse_range("bytes=-10", 100) == (90, 99)
assert s.parse_range("bytes=-", 100) is None

# SRT parsing
lines = s.parse_srt("1\n00:00:01,500 --> 00:00:03,000\nこんにちは\n二行目\n\n2\n00:01:00,000 --> 00:01:02,250\nはい\n")
assert lines[0] == [["こんにちは", "二行目"], 1500, "0:00:02", 3000, "0:00:03"]
assert lines[1][1] == 60000

# card sentence matching ignores punctuation and whitespace
assert s.normalize_str("こら…　<b>元気</b>すぎるぞ…") == s.normalize_str("こら… 元気すぎるぞ")

# dropped files are found by name + size and moved into the series
from pathlib import Path  # noqa: E402
tmp = Path(tempfile.mkdtemp())
(tmp / "dl" / "nested").mkdir(parents=True)
(tmp / "show").mkdir()
(tmp / "dl" / "nested" / "Show - 02.mp4").write_bytes(b"x" * 10)
(tmp / "dl" / "Show - 02.mp4").write_bytes(b"x" * 5)  # same name, different size
s.SEARCH_ROOTS = [tmp / "dl"]
s.STATE["series"][str(tmp / "show")] = {"subs": None}
assert s.find_original("Show - 02.mp4", 10) == [tmp / "dl" / "nested" / "Show - 02.mp4"]
assert s.adopt(str(tmp / "show"), "Show - 02.mp4", 10) == tmp / "show" / "Show - 02.mp4"
assert not (tmp / "dl" / "nested" / "Show - 02.mp4").exists()
assert s.adopt(str(tmp / "show"), "Show - 02.mp4", 10) == tmp / "show" / "Show - 02.mp4"  # idempotent
for bad in [("/nope", "a.mp4", 1), (str(tmp / "show"), "a.exe", 1), (str(tmp / "show"), "missing.mp4", 1)]:
    try:
        s.adopt(*bad)
        raise AssertionError(bad)
    except (ValueError, LookupError):
        pass

print("ok")
