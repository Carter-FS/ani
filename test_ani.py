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

print("ok")
