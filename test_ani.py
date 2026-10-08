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
    "Show - 12 - 2 Years Later [1080p].mkv": 12,
    "Show - 05 - 100 Days.mkv": 5,
    "Show - 05 - 1984.mkv": 5,
    "Show 05 (WEB 1080p AAC 2.0).mkv": 5,
    "[Group] Show 05 [1080p][AAC2.0].mkv": 5,
    "Show 05 H.264.mkv": 5,
    "Show 05 1080p x264 AAC 5.1.mkv": 5,
    "Show 13.5 [1080p].mkv": 13.5,
    "Show 05.5.ass": 5.5,
    "Show 05.ass": 5,
    "ショー #05.srt": 5,
    "ショー ＃05.srt": 5,
    "Mob Psycho 100 03.mkv": 3,
    "[Group] Show - OVA 01.mkv": None,
    "Show - OVA - 01.mkv": None,
    "Show - NCOP1.mkv": None,
    "[VCB-Studio] Show [05][Ma10p_1080p][x265_flac].mkv": 5,
    "[Nekomoe kissaten][Show][05][1080p][JPSC].mp4": 5,
    "[Sakurato] Show [05v2][1080p].mkv": 5,
    "[Group][Show][12 END][1080p].mkv": 12,
    "Special A - 05.mkv": 5,
    "Shokugeki no Souma - 05 - The Menu.mkv": 5,
    "Show - 12 - Something Special [1080p].mkv": 12,
    "Show - 05 - Preview of Doom.mkv": 5,
    "Show - 05 - The OVA Club.mkv": 5,
    "Show 2 - OVA.mkv": None,
    "Show Season 2 - OVA.mkv": None,
    "[Group] Show 2 - NCED [1080p].mkv": None,
    "[Group] Show 3 OVA [1080p].mkv": None,
    "Show - 01 NCOP.mkv": None,
    "[Nekomoe][Show][OVA][01][1080p].mkv": None,
    "[Show][SP][02][1080p].mkv": None,
    "Show 05 - Movie Night.mkv": 5,
    "Show 05 The Menu.mkv": 5,
    "[Group] Show - 05 Special Delivery [1080p].mkv": 5,
    "[Coalgirls]_Show_OVA_01_(1280x720).mkv": None,
    "Show_Special_01.mkv": None,
    "Show_NCOP_01.mkv": None,
    "Show_05_Special.mkv": None,
    "[VCB-Studio] Kaguya-sama 3 - Ultra Romantic [05][Ma10p_1080p][x265_flac].mkv": 5,
    "[Group] Show 2 - Subtitle [05][1080p].mkv": 5,
    "[Group] Bungou Stray Dogs 4 - [05] [1080p].mkv": 5,
    "[Group] Mob Psycho 100 III - [05][1080p].mkv": 5,
    "[Group] Show (ONA) - 05.mkv": 5,
    "Show 05 (ONA).mkv": 5,
    "Show 1x05.mkv": 5,
    "ショー 05「タイトル」.srt": 5,
    "Show 05 Special.mkv": None,
    "Show - 05 (NCOP).mkv": None,
    "no number here.mkv": None,
}
for name, want in cases.items():
    assert s.episode(name) == want, (name, s.episode(name), want)

# seasons sharing a folder sort and match by season too
seasons = tmpdir()
for n in ("Show.S02E01.mkv", "Show.S01E02.mkv", "Show.S01E01.mkv", "Show.S01E01.srt", "Show.S02E01.srt"):
    (seasons / n).write_bytes(b"x")
assert [v.name for v in s.episodes(seasons)] == ["Show.S01E01.mkv", "Show.S01E02.mkv", "Show.S02E01.mkv"]
assert s.find_sub(seasons / "Show.S02E01.mkv", seasons).name == "Show.S02E01.srt"

# season/language only narrow ambiguous matches; "S2 - 05" carries a season
assert s.season("[SubsPlease] Show S2 - 05 (1080p).mkv") == 2 and s.season("Show Season 3 - 01.mkv") == 3
assert s.season("[Group] Show 2nd Season 05 [1080p].mkv") is None and s.season("Show.Season.2.E05.mkv") == 2
mixed = tmpdir()
for n in ("Show - S02E05 - Title.mkv", "Title.S01E05.WEBRip.Netflix.ja[cc].srt",
          "Other - 07.mkv", "Other - 07.ja.srt", "Other - 07.en.srt"):
    (mixed / n).write_bytes(b"x")
assert s.find_sub(mixed / "Show - S02E05 - Title.mkv", mixed).name.startswith("Title.S01E05")  # unique: kept
assert s.find_sub(mixed / "Other - 07.mkv", mixed).name == "Other - 07.ja.srt"

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
# no blank lines between cues; text after a blank line is not part of the cue
tight = "1\n00:00:01,000 --> 00:00:02,000\nA\n2\n00:00:03,000 --> 00:00:04,000\nB\n\nstray\n"
assert [ln[0] for ln in s.parse_srt(tight)] == [["A"], ["B"]], s.parse_srt(tight)
# a closed cue whose text is a number keeps it (not mistaken for the next index)
nums = "1\n00:00:01,000 --> 00:00:02,000\n３\n\n2\n00:00:03,000 --> 00:00:04,000\n残り\n100\n\n3\n00:00:05,000 --> 00:00:06,000\nはい\n"
assert [ln[0] for ln in s.parse_srt(nums)] == [["３"], ["残り", "100"], ["はい"]], s.parse_srt(nums)
# vector drawings with unclosed tags can't stall the parser
t0 = time.time()
s.clean("{\\p1" * 5000)
assert time.time() - t0 < 1

# --- ASS: drawings, tag-only and duplicate border layers dropped; no Format line needed
ass = ("[Script Info]\n[Events]\n"
       "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,{\\p1}m 0 0 l 10 0 10 10{\\p0}\n"
       "Dialogue: 0,0:00:03.00,0:00:04.00,Default,,0,0,0,,{\\pos(1,2)}\n"
       "Dialogue: 0,0:00:05.00,0:00:06.00,Default,,0,0,0,,{\\bord3}はい\\Nいいえ\n"
       "Dialogue: 1,0:00:05.00,0:00:06.00,Default,,0,0,0,,はい\\Nいいえ\n")
assert [ln[0] for ln in s.parse_ass(ass)] == [["はい", "いいえ"]], s.parse_ass(ass)
# Styles has its own Format line; non-adjacent duplicate layers are dropped too
ass2 = ("[V4+ Styles]\nFormat: Name, Fontname, Fontsize\nStyle: Default,Arial,20\n[Events]\n"
        "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,A\n"
        "Dialogue: 0,0:00:01.00,0:00:02.00,Sign,,0,0,0,,B\n"
        "Dialogue: 1,0:00:01.00,0:00:02.00,Default,,0,0,0,,A\n")
assert sorted(ln[0][0] for ln in s.parse_ass(ass2)) == ["A", "B"], s.parse_ass(ass2)
# encodings: UTF-16 and Shift-JIS subtitles load
enc = tmpdir()
(enc / "u16.srt").write_bytes("1\n00:00:01,000 --> 00:00:02,000\nはい\n".encode("utf-16"))
(enc / "sjis.srt").write_bytes("1\n00:00:01,000 --> 00:00:02,000\nはい\n".encode("cp932"))
assert s.make_session("v", enc / "u16.srt").lines[0][0] == ["はい"]
assert s.make_session("v", enc / "sjis.srt").lines[0][0] == ["はい"]

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

# --- syncing: own output names; failures don't loop; subtitles added later retry
assert s.synced_path(show / "Show - 01.mkv") is None
s.ALASS = "/nonexistent/alass"
old(show / "Show - 01.mkv")
old(dl / "Show - 02.mkv")
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
assert not s.needs_sync(show / "Show - 02.mkv")
(show / "Show - 02.srt").write_text("x")  # subtitle appears later (e.g. copied in Finder)
assert s.needs_sync(show / "Show - 02.mkv")
(show / "Show - 01.srt").write_text("changed!")  # replaced subtitle: retry
assert s.needs_sync(show / "Show - 01.mkv")
(dl / "Show - 02.mkv").write_bytes(b"vv")  # still downloading: background skips, play uses raw sub
assert not s.needs_sync(show / "Show - 02.mkv")
assert s.ensure_synced(show / "Show - 02.mkv") == show / "Show - 02.srt" and len(calls) == 1
s.sync = real_sync

# alass refuses format conversion, so .ass syncs to .ani.ass (fake alass enforces that)
fake = tmpdir() / "alass"
fake.write_text('#!/bin/sh\n[ "${3##*.}" = "${4##*.}" ] && cp "$3" "$4"\n')
fake.chmod(0o755)
s.ALASS = str(fake)
ass_show = tmpdir()
(ass_show / "Show - 03.mkv").write_bytes(b"v")
old(ass_show / "Show - 03.mkv")
(ass_show / "Show - 03.ass").write_text(ass)
s.STATE["series"][str(ass_show)] = {"subs": None}
assert s.ensure_synced(ass_show / "Show - 03.mkv") == ass_show / "Show - 03.ani.ass"
assert s.synced_path(ass_show / "Show - 03.mkv") == ass_show / "Show - 03.ani.ass"
assert s.make_session("v", ass_show / "Show - 03.ani.ass").lines[0][0] == ["はい", "いいえ"]
assert s.find_sub(ass_show / "Show - 03.mkv", ass_show).name == "Show - 03.ass"  # own output ignored
# subtitle source changed while alass ran: the stale result is discarded
s.clear_synced(ass_show / "Show - 03.mkv")
alt = tmpdir()
(alt / "Show - 03.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nx\n")
real_sync = s.sync


def switching_sync(video, sub, *a):
    out = real_sync(video, sub, *a)
    s.STATE["series"][str(ass_show)]["subs"] = str(alt)  # e.g. /register mid-run
    return out


s.sync = switching_sync
out = s.ensure_synced(ass_show / "Show - 03.mkv")  # redone at once against the new source
assert out == ass_show / "Show - 03.ani.srt" and "x" in out.read_text(), out
s.sync = real_sync
s.clear_synced(ass_show / "Show - 03.mkv")

# a resync requested while alass runs discards that run and syncs again
runs = []


def resync_during(video, sub, *a):
    runs.append(sub)
    out = real_sync(video, sub, *a)
    if len(runs) == 1:
        s.RESYNCS[video] += 1
    return out


s.sync = resync_during
assert s.ensure_synced(ass_show / "Show - 03.mkv") and len(runs) == 2
s.sync = real_sync
s.STATE["series"][str(ass_show)]["subs"] = None
s.clear_synced(ass_show / "Show - 03.mkv")
assert s.synced_path(ass_show / "Show - 03.mkv") is None and (ass_show / "Show - 03.ass").exists()

# hung tools are killed with their children after the timeout
t0 = time.time()
r = s.run(["sh", "-c", "sleep 30 & sleep 30"], timeout=1)
assert r.returncode == -9 and time.time() - t0 < 5 and "timed out" in r.stderr

# real tools on synthetic media: Shift-JIS subtitles sync to the Japanese track; card audio prefers it too
import shutil as _sh, subprocess as _sp  # noqa: E402
_alass = _sh.which("alass-cli") or _sh.which("alass")
if _sh.which("ffmpeg") and _alass:
    media = tmpdir()
    vid = media / "Clip - 01.mkv"
    # beeps at 2-3s and 6-7s on a Japanese track, an English (default) track first, and a font
    # attachment of unknown type (alass 2.0 can't parse ffprobe's output for those)
    (media / "font.bin").write_bytes(b"font")
    _sp.run(["ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=10",
             "-f", "lavfi", "-i", "sine=f=800:d=10", "-f", "lavfi", "-i", "sine=f=500:d=10",
             "-filter_complex", "[2:a]volume='if(between(t,2,3)+between(t,6,7),1,0)':eval=frame[jp]",
             "-map", "0:v", "-map", "1:a", "-map", "[jp]", "-metadata:s:a:0", "language=eng",
             "-metadata:s:a:1", "language=jpn", "-disposition:a:0", "default", "-c:v", "libx264",
             "-c:a", "aac", "-attach", str(media / "font.bin"), "-metadata:s:t", "mimetype=application/octet-stream",
             "-shortest", str(vid)], check=True)
    assert s.audio_stream(str(vid)) == "0:a:1"
    sjis = media / "Clip - 01.srt"
    sjis.write_bytes("1\n00:00:02,500 --> 00:00:03,500\nはい\n\n2\n00:00:06,500 --> 00:00:07,500\nいいえ\n".encode("cp932"))
    old(vid)
    s.ALASS = _alass
    s.STATE["series"][str(media)] = {"subs": None}
    synced = s.ensure_synced(vid)
    assert synced.name == "Clip - 01.ani.srt", synced  # synced, not the raw fallback
    lines = s.make_session(str(vid), synced).lines
    assert lines[0][0] == ["はい"]
    assert abs(lines[0][1] - 2000) < 300, lines  # aligned to the Japanese track, not the first one
    # Japanese track only, still with the attachment
    solo = media / "Clip - 02.mkv"
    _sp.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(vid), "-map", "0:v", "-map", "0:a:1", "-map", "0:t",
             "-c", "copy", str(solo)], check=True)
    (media / "Clip - 02.srt").write_bytes(sjis.read_bytes())
    old(solo)
    synced = s.ensure_synced(solo)
    assert synced.name == "Clip - 02.ani.srt", synced
    assert abs(s.make_session(str(solo), synced).lines[0][1] - 2000) < 300
    # embedded full subtitles are the reference over the audio (here they deliberately disagree,
    # at 4s and 8s); a signs-and-songs track is not
    ref = media / "ref.srt"
    ref.write_text("1\n00:00:04,000 --> 00:00:05,000\nYes\n\n2\n00:00:08,000 --> 00:00:09,000\nNo\n")
    for n, title, start in (("03", "Full Subtitles", 4000), ("04", "Signs & Songs", 2000)):
        clip = media / f"Clip - {n}.mkv"
        _sp.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(solo), "-i", str(ref), "-map", "0", "-map", "1",
                 "-c", "copy", "-metadata:s:s:0", f"title={title}", str(clip)], check=True)
        (media / f"Clip - {n}.srt").write_bytes(sjis.read_bytes())
        old(clip)
        synced = s.ensure_synced(clip)
        assert synced.name == f"Clip - {n}.ani.srt", synced
        assert abs(s.make_session(str(clip), synced).lines[0][1] - start) < 300, (title, synced.read_text())
    # a dense typesetting track muxed first must not win over the real dialogue track after it: its
    # cues bunch around the unsynced lines (2-3s, 6-7s), so aligning to it would leave them there
    ts = lambda ms: f"00:00:{ms // 1000:02},{ms % 1000:03}"
    dense = media / "dense.srt"
    dense.write_text("".join(f"{i + 1}\n{ts(t)} --> {ts(t + 20)}\nsign\n\n" for i, t in
                             enumerate([*range(2000, 3000, 25), *range(6000, 7000, 25)])))
    clip = media / "Clip - 05.mkv"
    _sp.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(solo), "-i", str(dense), "-i", str(ref), "-map", "0",
             "-map", "1", "-map", "2", "-c", "copy", "-metadata:s:s:0", "title=Dialogue",
             "-metadata:s:s:1", "title=Dialogue", str(clip)], check=True)
    (media / "Clip - 05.srt").write_bytes(sjis.read_bytes())
    old(clip)
    synced = s.ensure_synced(clip)
    assert abs(s.make_session(str(clip), synced).lines[0][1] - 4000) < 300, synced.read_text()

# agreement: share of lines starting within 0.5s of a reference cue
cues = lambda *ms: [[["x"], t, "", t + 500, ""] for t in ms]
assert s.agreement(cues(1000, 5000), cues(1400, 9000)) == 0.5
assert s.agreement(cues(), cues(1000)) == 0.0 and s.agreement(cues(1000), []) == 0.0

# --- card checks: empty sentences never match; multi-card notes filled once; failures retried
s.SESSION = s.Session("id", "f.mkv", [[["…"], 0, "", 500, ""], [["はい"], 1000, "", 2000, ""]], ["", "はい"], "0:a:0")
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

# --- "Create note type": made once, reused after, and the options point at it
models, made = ["Basic"], []


def anki_invoke(action, **p):
    if action == "modelNames":
        return list(models)
    if action == "createModel":
        models.append(p["modelName"])
        made.append(p)
    if action == "modelFieldNames":
        return {"Basic": ["Front", "Back"]}.get(p["modelName"], s.NOTE_FIELDS)
    if action == "deckNames":
        return ["Mining", "Default"]


s.invoke = anki_invoke
s.create_note_type("Words")
s.create_note_type("Words")
assert len(made) == 1 and made[0]["inOrderFields"] == s.NOTE_FIELDS
assert all(s.OPTIONS[k] in s.NOTE_FIELDS for k in ("sentence", "expression", "picture", "audio"))
assert s.OPTIONS["deck"] == "Words" and s.load_json(s.OPTIONS_PATH, {})["deck"] == "Words"
assert s.anki_lists() == {"decks": ["Default", "Mining"], "fields": sorted({"Front", "Back", *s.NOTE_FIELDS})}
s.invoke = lambda action, **p: ["ani"] if action == "modelNames" else ["Expression"] if action == "modelFieldNames" else None
try:
    s.create_note_type("Words")  # an "ani" note type someone pruned: say which fields are gone
    raise AssertionError("missing fields not reported")
except s.AnkiError as e:
    assert "Sentence" in str(e), e

# --- after a stop no new tool starts
s.STOPPED.set()
assert s.run(["echo", "hi"]).returncode == -9
s.STOPPED.clear()
assert s.run(["echo", "hi"]).stdout == "hi\n"

# --- dropped files are found by name + size and moved into the series
root = tmpdir()
(root / "dl" / "nested").mkdir(parents=True)
(root / "show").mkdir()
(root / "dl" / "nested" / "Show - 02.mp4").write_bytes(b"x" * 10)
(root / "dl" / "Show - 02.mp4").write_bytes(b"x" * 5)  # same name, different size
s.SEARCH_ROOTS = [root / "dl"]
s.STATE["series"][str(root / "show")] = {"subs": None}
assert s.find_original("Show - 02.mp4", 10) == [root / "dl" / "nested" / "Show - 02.mp4"]
assert s.adopt(str(root / "show"), "Show - 02.mp4", 10) == root / "show" / "Show - 02.mp4"
assert not (root / "dl" / "nested" / "Show - 02.mp4").exists()
assert s.adopt(str(root / "show"), "Show - 02.mp4", 10) == root / "show" / "Show - 02.mp4"  # idempotent
for bad in [("/nope", "a.mp4", 1), (str(root / "show"), "a.exe", 1), (str(root / "show"), "missing.mp4", 1),
            (str(root / "show"), "../../etc/x.mp4", 1), (str(root / "show"), "._x.mp4", 1)]:
    try:
        s.adopt(*bad)
        raise AssertionError(bad)
    except (ValueError, LookupError):
        pass

print("ok")
