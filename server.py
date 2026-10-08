# Derived from Autocards by かにふぁん (https://learnjapanese.moe/autocards/)
# SPDX-License-Identifier: GPL-3.0-or-later
"""Local web player: serves videos with Yomitan-scannable subtitles and fills
Anki cards (sentence, screenshot, sentence audio) through AnkiConnect."""

import bisect
import hashlib
import json
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from collections import defaultdict, namedtuple
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

BASE = Path(__file__).parent.resolve()
STATE_DIR = Path(os.environ.get("ANI_STATE")
                 or Path(os.environ.get("XDG_STATE_HOME", "~/.local/state")) / "ani").expanduser()
STATE_PATH = STATE_DIR / "state.json"
OPTIONS_PATH = STATE_DIR / "options.json"
THUMBS = STATE_DIR / "thumbs"
PORT = int(os.environ.get("ANI_PORT", "6969"))
HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
ANKI = os.environ.get("ANKICONNECT", "http://127.0.0.1:8765")
FFMPEG = "ffmpeg"
ALASS = shutil.which("alass-cli") or shutil.which("alass") or "alass-cli"

VIDEO_EXTS = {".mp4", ".m4v", ".mkv", ".webm", ".mov"}
SUB_EXTS = {".srt", ".ass"}
SYNCED_SUFFIXES = (".ani.srt", ".ani.ass")
WATCHED = 0.9
SETTLE = 60  # seconds a file must be unmodified before background sync (still downloading otherwise)


# Background work (season sync, thumbnails) runs at low CPU priority so playback never hitches.
# CPU only: disk throttling (taskpolicy -b, ionice idle) made background syncs up to 25x slower,
# and a play of an episode already syncing in the background waits for that run.
LOW_PRIORITY = ["nice", "-n", "10"] if shutil.which("nice") else []


CHILDREN = set()  # tool processes in flight, killed on shutdown so none are orphaned


def kill_group(p):
    try:
        if os.name == "nt":
            p.kill()  # ponytail: just the tool; an ffmpeg alass started finishes on its own
        else:
            os.killpg(p.pid, signal.SIGKILL)  # the tool and anything it started (alass runs ffmpeg)
    except OSError:
        pass


def run(cmd, low=False, timeout=600):
    """Run a tool without a terminal (ffmpeg would grab the tty), capturing output. A run that
    hangs (e.g. on a file still being written) is killed after `timeout` seconds."""
    if low:
        cmd = LOW_PRIORITY + cmd
    try:
        p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             encoding="utf-8", errors="replace", start_new_session=True,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))  # no console flash on Windows
    except OSError as e:  # tool not installed
        return subprocess.CompletedProcess(cmd, 127, "", str(e))
    CHILDREN.add(p)
    try:
        out, err = p.communicate(timeout=timeout)
        return subprocess.CompletedProcess(cmd, p.returncode, out, err)
    except subprocess.TimeoutExpired:
        kill_group(p)
        out, err = p.communicate()
        return subprocess.CompletedProcess(cmd, -9, out, f"{err}\nerror: timed out after {timeout}s")
    finally:
        CHILDREN.discard(p)


# --- subtitles -------------------------------------------------------------

def to_ms(h=0, m=0, s=0, ms=0):
    return ms + 1000 * (s + 60 * (m + 60 * h))


def label(ms):
    m, s = divmod(round(ms / 1000), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02}:{s:02}"


TAGS = re.compile(r"<[^>]*>|\{[^}]*\}")


def clean(text):
    """Subtitle text lines without markup or ASS drawings; empty lines dropped."""
    out, drawing = [], False
    for piece in re.split(r"(\{[^}]*\})", text):
        if piece.startswith("{") and piece.endswith("}"):
            if modes := re.findall(r"\\p(\d)", piece):  # \p1.. starts a vector drawing, \p0 ends it
                drawing = modes[-1] != "0"
            continue
        if not drawing:
            out.append(piece)
    text = re.sub(r"<[^>]*>", "", "".join(out))
    return [t.strip() for t in text.split("\n") if t.strip()]


def cue(parts, start, end):
    return [parts, start, label(start), end, label(end)]


SRT_TIME = re.compile(r"(\d+):(\d{1,2}):(\d{1,2})[,.](\d{1,3})\s*-->\s*(\d+):(\d{1,2}):(\d{1,2})[,.](\d{1,3})")


def parse_srt(srt):
    """Lenient SRT: cues start at timestamp lines and end at a blank line, so missing or
    extra blank lines, missing indices and junk blocks never merge or drop cues."""
    lines, cur = [], None

    def flush():
        if cur and (parts := clean("\n".join(cur[2]))):
            lines.append(cue(parts, cur[0], cur[1]))

    for row in srt.split("\n"):
        if m := SRT_TIME.search(row):
            if cur and not cur[3][0] and cur[2] and cur[2][-1].strip().isdigit():
                cur[2].pop()  # that number was this cue's index
            flush()
            g = m.groups()
            cur = (to_ms(int(g[0]), int(g[1]), int(g[2]), int(g[3].ljust(3, "0"))),
                   to_ms(int(g[4]), int(g[5]), int(g[6]), int(g[7].ljust(3, "0"))), [], [False])
        elif not row.strip():
            if cur:
                cur[3][0] = True  # a blank line ends the cue's text
        elif cur and not cur[3][0]:
            cur[2].append(row)
    flush()
    lines.sort(key=lambda x: x[1])
    return lines


def parse_ass(ass):
    def parse_ts(ts):
        h, m, rest = ts.strip().split(":")
        s, cs = rest.split(".")
        return to_ms(int(h), int(m), int(s), int(cs.ljust(2, "0")[:2]) * 10)

    lines, cols = [], ["layer", "start", "end", "style", "name", "marginl", "marginr", "marginv", "effect", "text"]
    section = ""
    for line in ass.splitlines():
        line = line.strip()
        if line.startswith("["):
            section = line.lower()
        elif line.startswith("Format:") and section == "[events]":
            cols = [x.strip().lower() for x in line[7:].split(",")]
        elif line.startswith("Dialogue:"):
            parts = line[9:].split(",", len(cols) - 1)
            if len(parts) != len(cols):
                continue
            row = dict(zip(cols, parts))
            text = row.get("text", "").replace("\\N", "\n").replace("\\n", "\n").replace("\\h", " ")
            try:
                start, end = parse_ts(row["start"]), parse_ts(row["end"])
            except (KeyError, ValueError):
                continue
            if parts := clean(text):
                lines.append(cue(parts, start, end))
    lines.sort(key=lambda x: x[1])
    # fansubs duplicate lines across layers for borders; keep one of each
    seen, out = set(), []
    for ln in lines:
        key = (tuple(ln[0]), ln[1], ln[3])
        if key not in seen:
            seen.add(key)
            out.append(ln)
    return out


DELIM = re.compile(r"(「|」|『|』|\"|\'|\.|!|\?|．|。|…|︒|！|？|︙|\s|﻿)")


def normalize_str(s):
    return DELIM.sub("", TAGS.sub("", s))


# --- episode matching ------------------------------------------------------

EP_PATTERNS = [
    r"S\d+\s*E(\d{1,4})",
    r"(?<![\dx])\d{1,2}x(\d{2,3})(?!\d)",  # "Show 1x05"
    r"第(\d+)話",
    r"(?:^|[\s._\-\[(])E[Pp]?\.?\s*(\d{1,4})(?=$|[\s._\-\])v])",
]
NOISE = re.compile(
    r"\[[^\]]*\]|\([^)]*\)|【[^】]*】"                              # groups, resolution, hashes
    r"|\b(?:AAC|E?-?AC-?3|DDP?|FLAC|Opus|DTS(?:-HD)?|TrueHD|LPCM)\s*\d\.\d\b"  # audio channels
    r"|\b[HhXx]\.?26[45]\b|\b\d+\s*bits?\b|\b\d{3,4}[pPiI]\b|\b[257]\.[01]\b", re.I)
BRACKET_EP = re.compile(r"\[(\d{1,4}(?:\.5)?)(?:v\d)?(?:\s*END)?\]", re.I)  # "[Show][05][1080p]"
# A file is a special (OVA, creditless OP/ED, ...) when the keyword is its own tag, sits right
# before the number, or ends the name right after it. In an episode title it doesn't count.
# Boundaries are spelled out because \b doesn't split on "_" ("Show_OVA_01").
KEYWORD = r"(?<![A-Za-z0-9])(?:OVA|OAD|SP|Specials?|NCOP\d*|NCED\d*|PV|Preview|Menu|Movie)(?![A-Za-z])"
SPECIAL_TAG = re.compile(rf"[\[(]{KEYWORD}[\])]", re.I)             # "[Show][OVA][01]", "(NCOP)"
SPECIAL_BEFORE = re.compile(rf"{KEYWORD}[\s._\-]*\d", re.I)          # "OVA 01", "Show_OVA_01", "NCOP1"
SPECIAL_AFTER = re.compile(rf"[\s._\-]*{KEYWORD}[\s._\-]*$", re.I)   # "Show 2 - OVA", "Show - 01 NCOP"
DASH_EP = re.compile(r"\s-\s(\d{1,4}(?:\.5)?)(?:v\d)?(?=$|[\s._-])")
TOKEN = re.compile(r"^(\d{1,4}(?:·5)?)(?:v\d)?$")


def _num(text):
    n = float(text.replace("·", "."))
    return int(n) if n.is_integer() else n


def episode(name):
    """Episode number from a release name (int, or float for .5 specials), else None."""
    stem = Path(name).stem
    for pat in EP_PATTERNS:
        if m := re.search(pat, stem, re.IGNORECASE):
            return int(m.group(1))
    if SPECIAL_TAG.search(stem):
        return None  # specials must not collide with regular episode numbers
    bare = re.sub(r"\s+", " ", NOISE.sub(" ", BRACKET_EP.sub(r" \1 ", stem))).strip()
    if SPECIAL_BEFORE.search(bare):
        return None
    # a bare bracketed number is explicit: "[Group] Kaguya-sama 3 - Ultra Romantic [05][1080p]"
    if (b := BRACKET_EP.search(stem)) and not re.fullmatch(r"(19|20)\d\d", b.group(1)):
        tail = " " + re.sub(r"\s+", " ", NOISE.sub(" ", stem[b.end():])).strip()
        return None if SPECIAL_AFTER.match(tail) else _num(b.group(1))
    # first " - N": later ones are usually episode titles. Ambiguous by nature: "Show 05 - 100 Days"
    # reads as 100, because "Mob Psycho 100 - 04" (the common convention) has the same shape.
    if m := DASH_EP.search(bare):
        return None if SPECIAL_AFTER.match(bare, m.end()) else _num(m.group(1))
    # Otherwise the last standalone non-year number, looking only before any " - Title" part,
    # e.g. "Show_03", "Show.05", "Mob Psycho 100 03", "Show 05 - Movie Night"
    bare = re.sub(r"(\d)\.5(?!\d)", r"\1·5", bare)
    head = len(bare.split(" - ")[0])
    nums = [(m.group(1), t.end()) for t in re.finditer(r"[^\s._\-#＃「」『』]+", bare)
            if (m := TOKEN.match(t.group())) and not re.fullmatch(r"(19|20)\d\d", m.group(1))]
    nums = [n for n in nums if n[1] <= head] or nums
    if not nums or SPECIAL_AFTER.match(bare, nums[-1][1]):
        return None
    return _num(nums[-1][0])


SEASON = re.compile(r"S(\d+)\s*E\d|(?<![\dx])(\d{1,2})x\d{2,3}(?!\d)"
                    r"|(?<![A-Za-z0-9])S(\d{1,2})\s*-\s*\d"
                    r"|(?<!\d(?:st|nd|rd|th)[\s._])Season[\s._]*(\d{1,2})(?!\d)", re.I)  # not "2nd Season 05"


def season(name):
    m = SEASON.search(Path(name).stem)
    return int(next(g for g in m.groups() if g)) if m else None


def visible(f):
    return not f.name.startswith(".")  # skip macOS ._ AppleDouble files and other dotfiles


def synced_path(video):
    """ani's synced subtitle for `video` if one exists, else None."""
    return next((p for suf in SYNCED_SUFFIXES if (p := video.with_name(video.stem + suf)).exists()), None)


def clear_synced(video):
    for suf in SYNCED_SUFFIXES:
        video.with_name(video.stem + suf).unlink(missing_ok=True)  # only ever ani's own output


SUB_INDEX = {}  # folder -> (file names, {episode: [subtitle files]})


def sub_index(where):
    """Subtitles in a folder by episode number; re-parsed only when its file list changes."""
    names = sorted(os.listdir(where))
    cached = SUB_INDEX.get(where)
    if cached and cached[0] == names:
        return cached[1]
    index = defaultdict(list)
    for name in names:
        f = where / name
        if visible(f) and f.suffix.lower() in SUB_EXTS and not name.endswith(SYNCED_SUFFIXES):
            index[episode(name)].append(f)
    SUB_INDEX[where] = names, index
    return index


JAPANESE = re.compile(r"(?:^|[._\-\s\[(])(?:ja|jp|jpn|japanese|日本語)(?=$|[._\-\s\])\[])", re.I)


def find_sub(video, where):
    """Subtitle file for `video` from a file or a folder (matched by episode number)."""
    if where.is_file():
        return where
    ep = episode(video.name)
    if ep is None:
        raise LookupError(f"no episode number in {video.name}")
    hits = sub_index(where).get(ep, [])
    vs = season(video.name)
    for keep in (lambda f: vs is None or season(f.name) in (None, vs),   # same season
                 lambda f: JAPANESE.search(f.stem)):                     # "Show - 05.ja.srt" over ".en"
        if len(hits) > 1:
            hits = [f for f in hits if keep(f)] or hits
    if len(hits) != 1:
        raise LookupError(f"{len(hits)} subtitles for episode {ep} in {where}")
    return hits[0]


def read_sub(path):
    """Subtitle text from UTF-8, UTF-16 or Shift-JIS (common for Japanese) files."""
    raw = Path(path).read_bytes()
    encodings = ("utf-16",) if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig", "cp932")
    for enc in encodings:
        try:
            return raw.decode(enc).replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")


def parse_sub(path):
    content = read_sub(path)
    is_ass = content.lstrip().startswith("[Script Info]") or "\nDialogue:" in content
    return parse_ass(content) if is_ass else parse_srt(content)


def synced_name(video, sub):
    return video.with_name(video.stem + ".ani" + sub.suffix.lower())


REF_SKIP = re.compile(r"sign|song|forced|karaoke", re.I)
SUB_PENALTIES = ("7", "30")  # alass's default, and one that resists splitting
AGREE = 0.5  # share of synced lines that must start on a reference line to trust a subtitle reference


def reference_tracks(file):
    """(stream index, extension) of each embedded full-dialogue text subtitle, which whoever muxed
    the release timed to this exact video."""
    r = run(["ffprobe", "-v", "error", "-select_streams", "s", "-show_entries",
             "stream=index,codec_name:stream_tags=title", "-of", "json", file], timeout=30)
    try:
        streams = json.loads(r.stdout).get("streams", [])
    except ValueError:
        return []
    return [(st["index"], ext) for st in streams
            if (ext := {"ass": ".ass", "ssa": ".ass", "subrip": ".srt"}.get(st.get("codec_name")))
            and not REF_SKIP.search(st.get("tags", {}).get("title", ""))]


def agreement(lines, ref):
    """Share of `lines` starting within half a second of a cue in `ref`."""
    starts = sorted({c[1] for c in ref})
    hits = 0
    for c in lines:
        i = bisect.bisect_left(starts, c[1] - 500)
        hits += i < len(starts) and starts[i] <= c[1] + 500
    return hits / len(lines) if lines else 0.0


def sync(video, sub, background=False):
    """Align `sub` to the video with alass: to the release's own embedded subtitles when they fit,
    else to its Japanese audio. Returns a hidden .part file beside the video for the caller to
    publish, or None on failure."""
    print(f"syncing {sub.name}", flush=True)
    out = synced_name(video, sub)
    # alass trips on [] in paths, so run it on clean symlinks; it also refuses format
    # conversion, so the output keeps the subtitle's own extension.
    # fps guessing off: it misfires on releases with equal frame rates (e.g. 25/23.976).
    try:
        with tempfile.TemporaryDirectory() as tmp:
            v, s, o = (Path(tmp, "v" + video.suffix), Path(tmp, "s" + sub.suffix.lower()),
                       Path(tmp, "o" + sub.suffix.lower()))
            v.symlink_to(video)
            s.write_text(read_sub(sub), encoding="utf-8")  # alass only decodes UTF-8/UTF-16
            # Give alass just the Japanese audio: it only listens to the first audio track (often a
            # dub on dual-audio releases) and fails on files with font attachments of unknown type.
            # The embedded subtitles come out in the same pass, so the file is read once.
            a = Path(tmp, "a.wav")
            audio = [FFMPEG, "-nostdin", "-v", "error", "-i", str(v), "-map", audio_stream(str(video)), "-vn",
                     "-ac", "1", "-ar", "8000", "-c:a", "pcm_s16le", str(a)]
            ref_sub, refs = None, [(Path(tmp, f"ref{i}{ext}"), i) for i, ext in reference_tracks(str(video))]
            x = run(audio + [a for ref, i in refs for a in ("-map", f"0:{i}", "-c:s", "copy", str(ref))], low=background)
            if x.returncode != 0 and refs:
                refs = []
                x = run(audio, low=background)
            # Several tracks can qualify, and some are mostly animated typesetting, dense enough that
            # any timing seems to agree with them; the one with about as many lines as the Japanese
            # subtitle is real dialogue, in whatever language.
            n = len(parse_sub(s))
            if have := [ref for ref, _ in refs if ref.exists()]:
                ref_sub = min(have, key=lambda ref: abs(len(parse_sub(ref)) - n))
            part = out.with_name(f".{out.name}.part")
            if ref_sub and ref_sub.exists():
                # alass's default split penalty over-splits against subtitles on some releases, so try
                # a stricter one too and keep whichever lands more lines on the reference
                best, ref_lines = (0.0, None), parse_sub(ref_sub)
                for penalty in SUB_PENALTIES:
                    o.unlink(missing_ok=True)
                    r = run([ALASS, "--disable-fps-guessing", "--split-penalty", penalty, str(ref_sub), str(s), str(o)],
                            low=background)
                    if r.returncode == 0 and o.exists() and (score := agreement(parse_sub(o), ref_lines)) > best[0]:
                        best = score, o.read_bytes()
                # a subtitle reference can belong to another episode or cut; then trust the audio
                if best[0] >= AGREE:
                    print(f"synced {sub.name} to the embedded subtitles ({best[0]:.0%} agree)", flush=True)
                    part.write_bytes(best[1])
                    return part
            o.unlink(missing_ok=True)
            r = run([ALASS, "--disable-fps-guessing", str(a if x.returncode == 0 and a.exists() else v), str(s), str(o)],
                    low=background)
            if r.returncode == 0 and o.exists():
                print(f"synced {sub.name} to the audio", flush=True)
                shutil.copyfile(o, part)
                return part
            msg = f"{r.stderr}\n{r.stdout}"
            why = [ln.strip() for ln in msg.splitlines() if ln.strip().startswith(("error", "caused by"))]
            print(f"alass failed on {video.name}: {' / '.join(why[:3]) or msg.strip()[-200:]}", flush=True)
            return None
    except OSError as e:
        print(f"sync failed on {video.name}: {e}", flush=True)
        return None


# --- state -----------------------------------------------------------------

STATE_LOCK = threading.RLock()
BG_DIRS = set()
BG_LOCK = threading.Lock()


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


STATE = load_json(STATE_PATH, {})
STATE.setdefault("series", {})     # video dir -> {"subs": subtitle dir or None}
STATE.setdefault("overrides", {})  # video path -> subtitle file
STATE.setdefault("progress", {})   # video path -> {"pos": s, "dur": s}
STATE.setdefault("offsets", {})    # video dir -> subtitle offset in seconds


def save_state():
    with STATE_LOCK:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(STATE, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(STATE_PATH)


def episodes(folder):
    try:
        files = [f for f in Path(folder).iterdir() if visible(f) and f.suffix.lower() in VIDEO_EXTS]
    except OSError:
        return []
    return sorted(files, key=lambda f: (episode(f.name) is None, season(f.name) or 0, episode(f.name) or 0, f.name))


def allowed(path):
    """The video at `path` if it sits in a registered series folder (symlinked files are fine)."""
    if not path:
        return None
    p = Path(path)
    v = p.parent.resolve() / p.name
    if visible(v) and v.suffix.lower() in VIDEO_EXTS and str(v.parent) in STATE["series"] and v.is_file():
        return v
    return None


FAILED = {}  # video -> fail_key when its sync last failed
SYNC_LOCKS = defaultdict(threading.Lock)
RESYNCS = defaultdict(int)  # video -> resync requests; a sync started before one is discarded


def signature(path):
    st = path.stat()
    return st.st_size, st.st_mtime


def settled(video):
    return time.time() - video.stat().st_mtime > SETTLE


def source_sub(video):
    src = STATE["overrides"].get(str(video)) or STATE["series"].get(str(video.parent), {}).get("subs")
    return find_sub(video, Path(src) if src else video.parent)


def fail_key(video, sub):
    """Changes when the video finishes downloading or its subtitle is added or replaced."""
    return signature(video), str(sub), signature(sub)


def needs_sync(video):
    """Unsynced, has a subtitle, finished downloading, and not already failed as-is."""
    try:
        if synced_path(video) or not settled(video):
            return False
        return FAILED.get(video) != fail_key(video, source_sub(video))
    except (LookupError, OSError):
        return False


def ensure_synced(video, retry=True, background=False):
    """Synced subtitle, or the unsynced source when syncing failed or the file is still downloading."""
    if out := synced_path(video):
        return out
    sub = source_sub(video)  # LookupError/OSError: no subtitle to show at all
    if not settled(video) or FAILED.get(video) == fail_key(video, sub):
        return sub
    with SYNC_LOCKS[video]:
        if out := synced_path(video):
            return out
        if FAILED.get(video) == fail_key(video, sub):  # another thread just failed it
            return sub
        generation = RESYNCS[video]
        part = sync(video, sub, background)
        if not part:
            FAILED[video] = fail_key(video, sub)
            return sub
        try:
            current = source_sub(video) == sub and RESYNCS[video] == generation
        except (LookupError, OSError):
            current = False
        if current:
            out = synced_name(video, sub)
            try:
                part.replace(out)  # atomic publish, so /play never reads a half-written file
                return out
            except OSError as e:
                print(f"couldn't save synced subtitle for {video.name}: {e}", flush=True)
                part.unlink(missing_ok=True)
                FAILED[video] = fail_key(video, sub)
                return sub
        part.unlink(missing_ok=True)  # subtitle source changed or a resync was requested meanwhile
    return ensure_synced(video, retry=False, background=background) if retry else source_sub(video)


def sync_season(folder):
    """Sync every episode in the background so autoplaying the next one is instant."""
    with BG_LOCK:
        if folder in BG_DIRS or not any(needs_sync(v) for v in episodes(folder)):
            return
        BG_DIRS.add(folder)

    def work():
        # rescan after each pass so files added mid-run (e.g. dropped in the UI) are picked up
        last = None
        try:
            while (todo := [v for v in episodes(folder) if needs_sync(v)]) and todo != last:
                last = todo
                for v in todo:
                    try:
                        ensure_synced(v, background=True)  # low priority: never hitch playback
                    except (LookupError, OSError):
                        pass
        finally:
            with BG_LOCK:
                BG_DIRS.discard(folder)

    threading.Thread(target=work, daemon=True).start()


SEARCH_ROOTS = [Path(p).expanduser() for p in os.environ.get(
    "ANI_SEARCH", "~/Downloads:~/Desktop:~/Movies:~/Videos").split(os.pathsep)]


def find_original(name, size, mtime_ms=None, roots=None, depth=4):
    """Locate a dropped file on disk by name and size (the browser never sees its path)."""
    hits = []
    for root in roots or SEARCH_ROOTS:
        if not root.is_dir():
            continue
        base = len(root.parts)
        for d, dirs, files in os.walk(root):
            dirs[:] = [x for x in dirs if not x.startswith(".")] if len(Path(d).parts) - base < depth else []
            if name in files:
                p = Path(d, name)
                try:
                    if p.stat().st_size == size:
                        hits.append(p)
                except OSError:
                    pass
    if len(hits) > 1 and mtime_ms:
        hits = [p for p in hits if abs(p.stat().st_mtime * 1000 - mtime_ms) < 2000] or hits
    return hits


def adopt(folder, name, size, mtime_ms=None):
    """Move a dropped episode or subtitle into a series. Returns the new path."""
    name = Path(name).name
    ext = Path(name).suffix.lower()
    if folder not in STATE["series"] or not name or name.startswith(".") or ext not in VIDEO_EXTS | SUB_EXTS:
        raise ValueError("unsupported file or unknown series")
    subs = STATE["series"][folder].get("subs")
    dest = Path(subs if ext in SUB_EXTS and subs and Path(subs).is_dir() else folder) / name
    if dest.exists():
        if dest.stat().st_size == size:
            return dest  # already in place
        raise FileExistsError(f"a different {name} is already in the series")
    hits = find_original(name, size, mtime_ms)
    if not hits:
        raise LookupError(f"couldn't find {name} in {', '.join(map(str, SEARCH_ROOTS))}")
    if len(hits) > 1:
        raise LookupError(f"{len(hits)} files named {name}; move it with the CLI instead")
    shutil.move(hits[0], dest)
    print(f"moved {hits[0]} -> {dest}", flush=True)
    return dest


def progress(video):
    p = STATE["progress"].get(str(video), {})
    return p.get("pos", 0), p.get("dur", 0)


def is_watched(video):
    pos, dur = progress(video)
    return dur > 0 and pos / dur >= WATCHED


def continue_target():
    last = STATE.get("last")
    if not last or not Path(last).is_file():
        return None
    last = Path(last)
    if not is_watched(last):
        return last
    eps = episodes(last.parent)
    if last not in eps:
        return last
    i = eps.index(last)
    return eps[i + 1] if i + 1 < len(eps) else last


THUMB_SLOTS = threading.Semaphore(3)  # bound concurrent ffmpeg runs from a library page load


def thumb(video):
    key = hashlib.sha1(str(video).encode()).hexdigest()
    out = THUMBS / f"{key}-{hashlib.sha1(str(signature(video)).encode()).hexdigest()[:12]}.jpg"
    if out.exists():
        return out
    with THUMB_SLOTS:
        if out.exists():
            return out
        THUMBS.mkdir(parents=True, exist_ok=True)
        r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
                low=True, timeout=30)
        try:
            at = float(r.stdout) * 0.3
        except ValueError:
            return None
        part = out.with_name(f".{out.stem}.{threading.get_ident()}.jpg")
        run([FFMPEG, "-nostdin", "-v", "error", "-y", "-ss", f"{at:.1f}", "-i", str(video),
             "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "5", str(part)], low=True, timeout=60)
        if part.exists():
            part.replace(out)
            for stale in THUMBS.glob(f"{key}-*.jpg"):  # older thumbnails of this file (taken mid-download)
                if stale != out:
                    stale.unlink(missing_ok=True)
    return out if out.exists() else None


# --- anki ------------------------------------------------------------------

# Everything about the episode playing, swapped as one object so a card check
# running during an episode change never mixes lines from one with media from another.
Session = namedtuple("Session", "id file lines normalized audio")
SESSION = Session("", "", [], [], "0:a:0")
DELAY = 0.0
TIME = 0.0, time.time()
DONE_NOTES = set()
CHECK_LOCK = threading.Lock()

OPTIONS = {"deck": "", "sentence": "Sentence", "expression": "Expression",
           "picture": "Picture", "audio": "SentenceAudio", "prev_lines": 0, "next_lines": 0}
OPTIONS.update(load_json(OPTIONS_PATH, {}))


NOTE_TYPE = "ani"  # the note type "Create note type" makes; its field names are OPTIONS' defaults
NOTE_FIELDS = ["Expression", "Reading", "Meaning", "Sentence", "Picture", "SentenceAudio", "WordAudio"]
NOTE_FRONT = '<div class="sentence">{{Sentence}}</div><div class="word">{{Expression}}</div>'
NOTE_BACK = ('{{FrontSide}}<hr id="answer"><div class="reading">{{Reading}}</div>{{Picture}}'
             '<div class="meaning">{{Meaning}}</div>{{SentenceAudio}} {{WordAudio}}')
NOTE_CSS = (".card { font-family: sans-serif; font-size: 22px; text-align: center; }\n"
            ".sentence { font-size: 28px; } .sentence b { color: #e0632b; }\n"
            ".word { font-size: 18px; opacity: 0.7; margin-top: 0.5em; }\n"
            ".meaning { font-size: 18px; text-align: left; } img { max-width: 100%; }")


class AnkiError(Exception):
    pass


def save_options():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    OPTIONS_PATH.write_text(json.dumps(OPTIONS, ensure_ascii=False), encoding="utf-8")


def anki_lists():
    """Deck and field names for the settings form's suggestions."""
    models = invoke("modelNames")
    fields = {f for m in models for f in invoke("modelFieldNames", modelName=m)}
    return {"decks": sorted(invoke("deckNames")), "fields": sorted(fields)}


def create_note_type(deck):
    """Make the deck and the ani note type (unless they exist) and point the options at them."""
    invoke("createDeck", deck=deck)
    if NOTE_TYPE not in invoke("modelNames"):
        invoke("createModel", modelName=NOTE_TYPE, inOrderFields=NOTE_FIELDS, css=NOTE_CSS,
               cardTemplates=[{"Name": "Mining", "Front": NOTE_FRONT, "Back": NOTE_BACK}])
    OPTIONS.update(deck=deck, sentence="Sentence", expression="Expression", picture="Picture", audio="SentenceAudio")
    save_options()


def token():
    return random.randbytes(8).hex()


def clock():
    return TIME[0] + min(time.time() - TIME[1], 1.0)


def audio_stream(file):
    """ffmpeg map for the audio the browser plays: Japanese, else the default track, else the first."""
    r = run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
             "stream_tags=language:stream_disposition=default", "-of", "json", file], timeout=30)
    try:
        streams = json.loads(r.stdout).get("streams", [])
    except ValueError:
        streams = []
    for i, st in enumerate(streams):
        if st.get("tags", {}).get("language", "").lower() in ("jpn", "ja", "jp"):
            return f"0:a:{i}"
    for i, st in enumerate(streams):
        if st.get("disposition", {}).get("default"):
            return f"0:a:{i}"
    return "0:a:0"


def make_session(file, sub_path):
    lines = parse_sub(sub_path) if sub_path else []
    return Session(token(), file, lines, [normalize_str("".join(ln[0])) for ln in lines],
                   audio_stream(file) if file else "0:a:0")


def invoke(action, **params):
    req = json.dumps({"action": action, "params": params, "version": 6}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(ANKI, req), timeout=10) as resp:
            r = json.load(resp)
    except (OSError, ValueError) as e:
        raise AnkiError(f"{action}: {e}") from e
    if not isinstance(r, dict) or r.get("error") is not None:
        raise AnkiError(f"{action}: {r.get('error') if isinstance(r, dict) else r}")
    return r.get("result")


def take_screenshot(src, start, end):
    file = f"autocards-{token()}.jpg"
    path = STATE_DIR / file
    run([FFMPEG, "-nostdin", "-v", "error", "-ss", f"{0.75 * start + 0.25 * end:.3f}", "-i", src,
         "-frames:v", "1", "-vf", "scale=iw/2:-2", "-q:v", "4", str(path)], timeout=60)
    return store_media(file, path)


def take_audio(src, start, end, stream="0:a:0"):
    file = f"autocards-{token()}.mp3"
    path = STATE_DIR / file
    run([FFMPEG, "-nostdin", "-v", "error", "-ss", f"{max(start, 0):.3f}", "-t", f"{max(end - start, 0.1):.3f}",
         "-i", src, "-map", stream, "-ac", "1", "-c:a", "libmp3lame", "-q:a", "4", str(path)], timeout=60)
    return store_media(file, path)


def store_media(file, path):
    if not path.exists():
        return None
    try:
        return invoke("storeMediaFile", filename=file, path=str(path))
    except AnkiError:
        return None
    finally:
        path.unlink(missing_ok=True)


def update_note(s, delay, note_id, idx, expression=None, original_sentence=None):
    lo = max(0, idx - int(OPTIONS.get("prev_lines") or 0))
    hi = min(len(s.lines) - 1, idx + int(OPTIONS.get("next_lines") or 0))
    subset = s.lines[lo:hi + 1]
    sentence = "<br/>".join("<br/>".join(line[0]) for line in subset)

    bold = set()
    if expression:
        bold.add(expression)
    if original_sentence:
        bold.update(filter(None, re.findall(r"<b>(.*?)</b>", original_sentence)))
    if bold:
        pattern = "|".join(re.escape(w) for w in sorted(bold, key=len, reverse=True))
        sentence = re.sub(pattern, lambda m: f"<b>{m.group(0)}</b>", sentence)

    start = subset[0][1] / 1000 + delay
    end = subset[-1][3] / 1000 + delay
    fields = {OPTIONS["sentence"]: sentence}
    if picture := take_screenshot(s.file, start, end):
        fields[OPTIONS["picture"]] = f'<img src="{picture}">'
    if sound := take_audio(s.file, start, end, s.audio):
        fields[OPTIONS["audio"]] = f"[sound:{sound}]"
    invoke("updateNote", note={"id": note_id, "fields": fields})
    print(f"updated note {note_id}", flush=True)


def check(t):
    """Fill new cards whose sentence matches a subtitle line already played."""
    if not CHECK_LOCK.acquire(blocking=False):
        return
    s, delay = SESSION, DELAY
    try:
        if not s.id or not s.lines or not all(OPTIONS[k] for k in ("deck", "sentence", "picture", "audio")):
            return
        deck = str(OPTIONS["deck"]).strip('"')
        ids = invoke("findCards", query=f'"deck:{deck}" added:1 "{OPTIONS["picture"]}:" "{OPTIONS["audio"]}:"')
        if not ids:
            return
        end_idx = bisect.bisect_right([ln[1] for ln in s.lines], to_ms(s=t))
        subs = {v: i for i, v in enumerate(s.normalized[:end_idx]) if v}
        seen = set()
        for card in invoke("cardsInfo", cards=ids) or []:
            note = card.get("note")
            if note in DONE_NOTES or note in seen:
                continue  # notes with several cards appear once per card
            seen.add(note)
            f = card.get("fields", {})
            key = normalize_str(f.get(OPTIONS["sentence"], {}).get("value", ""))
            if not key or (idx := subs.get(key)) is None:
                continue
            expression = f.get(OPTIONS["expression"], {}).get("value") if OPTIONS["expression"] else None
            try:
                update_note(s, delay, note, idx, expression, f[OPTIONS["sentence"]]["value"])
                DONE_NOTES.add(note)
            except AnkiError as e:
                print(f"card fill failed: {e}", flush=True)
    except AnkiError:
        pass  # Anki closed or busy; the page checks again shortly
    finally:
        CHECK_LOCK.release()


# --- http ------------------------------------------------------------------

def parse_range(header, size):
    """(start, end) inclusive for a `Range: bytes=` header; None to send the whole file.
    Raises ValueError when the range can't be satisfied."""
    m = re.fullmatch(r"bytes=(\d*)-(\d*)", header or "")
    if not m or not (m[1] or m[2]):
        return None
    if not m[1]:
        n = int(m[2])
        if n == 0 or size == 0:
            raise ValueError
        return max(0, size - n), size - 1
    start = int(m[1])
    end = min(int(m[2]), size - 1) if m[2] else size - 1
    if start >= size or start > end:
        raise ValueError
    return start, end


class BadRequest(Exception):
    pass


class Server(BaseHTTPRequestHandler):
    def send(self, code=200, body: bytes | dict | list = b"", ctype="application/json"):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, ctype):
        size = path.stat().st_size
        try:
            rng = parse_range(self.headers.get("Range"), size)
        except ValueError:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            return self.end_headers()
        start, end = rng or (0, size - 1)
        self.send_response(206 if rng else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if rng:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        try:
            with open(path, "rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = f.read(min(1 << 20, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def trusted(self):
        """Refuse DNS-rebinding (foreign Host) and cross-site requests (foreign Origin)."""
        origin = self.headers.get("Origin")
        return self.headers.get("Host") in HOSTS and (not origin or origin.split("//")[-1] in HOSTS)

    def do_GET(self):
        if not self.trusted():
            return self.send(403)
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}

        if url.path == "/":
            self.send(body=(BASE / "index.html").read_bytes(), ctype="text/html; charset=utf-8")
        elif url.path == "/api/library":
            folder = q.get("dir") or (str(Path(STATE["last"]).parent) if STATE.get("last") else "")
            series = list(STATE["series"])
            if folder not in STATE["series"] or not Path(folder).is_dir():
                return self.send(body={"dir": "", "episodes": [], "series": series,
                                       "missing": folder if folder in STATE["series"] else None})
            sync_season(folder)
            target = continue_target()
            eps = []
            for v in episodes(folder):
                pos, dur = progress(v)
                eps.append({"path": str(v), "ep": episode(v.name), "season": season(v.name), "name": v.stem,
                            "synced": bool(synced_path(v)), "pos": pos, "dur": dur,
                            "watched": is_watched(v)})
            self.send(body={"dir": folder, "name": Path(folder).name, "episodes": eps,
                            "series": series, "syncing": folder in BG_DIRS,
                            "continue": str(target) if target and str(target.parent) == folder else None})
        elif url.path == "/api/continue":
            target = continue_target()
            self.send(body={"video": str(target) if target else None})
        elif url.path == "/video":
            v = allowed(q.get("path"))
            if not v:
                return self.send(404)
            ctype = {".mkv": "video/x-matroska", ".webm": "video/webm"}.get(v.suffix.lower(), "video/mp4")
            self.send_file(v, ctype)
        elif url.path == "/thumb":
            v = allowed(q.get("path"))
            t = thumb(v) if v else None
            if not t:
                return self.send(404)
            self.send_file(t, "image/jpeg")
        elif url.path == "/options":
            self.send(body=OPTIONS)
        elif url.path == "/api/anki":
            try:
                self.send(body=anki_lists())
            except AnkiError as e:
                self.send(body={"error": str(e)})
        else:
            self.send(404)

    def do_POST(self):
        if not self.trusted():
            return self.send(403)
        try:
            size = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(size) or b"{}")
            if not isinstance(body, dict):
                raise BadRequest("expected a JSON object")
            self.post(urlparse(self.path).path, body)
        except (BadRequest, KeyError, TypeError, ValueError) as e:
            self.send(400, {"error": str(e) or "bad request"})

    def post(self, path, body):
        global OPTIONS, TIME, DELAY, SESSION

        if path == "/adopt":
            try:
                dest = adopt(body.get("dir", ""), body.get("name", ""), int(body.get("size", -1)), body.get("mtime"))
            except FileExistsError as e:
                return self.send(409, {"error": str(e)})
            except (ValueError, LookupError, OSError) as e:
                return self.send(400, {"error": str(e)})
            sync_season(body["dir"])
            self.send(body={"path": str(dest)})
        elif path == "/register":
            folder = Path(body["dir"]).resolve()
            if not folder.is_dir():
                raise BadRequest(f"not a folder: {folder}")
            video = None
            if body.get("video"):  # validate everything before touching state
                p = Path(body["video"])
                video = p.parent.resolve() / p.name
                if video.suffix.lower() not in VIDEO_EXTS:
                    raise BadRequest(f"unsupported video type {video.suffix or '(none)'}; "
                                     f"supported: {', '.join(sorted(VIDEO_EXTS))}")
                if video.parent != folder or not video.is_file():
                    raise BadRequest("video is not in the series folder")
            subs = str(Path(body["subs"]).resolve()) if body.get("subs") else None
            sub_file = str(Path(body["sub_file"]).resolve()) if body.get("sub_file") else None
            with STATE_LOCK:
                entry = STATE["series"].setdefault(str(folder), {"subs": None})
                if subs and subs != entry.get("subs"):
                    entry["subs"] = subs
                    for v in episodes(folder):  # new subtitle source: earlier syncs are stale
                        if str(v) not in STATE["overrides"]:
                            clear_synced(v)
                if video:
                    if subs and not sub_file and STATE["overrides"].pop(str(video), None):
                        clear_synced(video)  # back to folder matching for this video
                    if sub_file and sub_file != STATE["overrides"].get(str(video)):
                        STATE["overrides"][str(video)] = sub_file
                        clear_synced(video)
                    if body.get("resync"):
                        RESYNCS[video] += 1  # a sync already running for it gets discarded
                        clear_synced(video)
                        FAILED.pop(video, None)
                save_state()
            self.send(body={"ok": True})
        elif path == "/play":
            v = allowed(body.get("video"))
            if not v:
                return self.send(404, {"error": "This video isn't in a registered series."})
            error = None
            try:
                SESSION = make_session(str(v), ensure_synced(v))
            except (LookupError, OSError) as e:
                error = str(e)
                SESSION = make_session(str(v), None)
            DELAY, TIME = float(STATE.get("offsets", {}).get(str(v.parent), 0.0)), (0.0, time.time())
            with STATE_LOCK:
                STATE["last"] = str(v)
                save_state()
            sync_season(str(v.parent))
            eps = episodes(v.parent)
            i = eps.index(v) if v in eps else -1
            pos, dur = progress(v)
            self.send(body={
                "id": SESSION.id, "lines": SESSION.lines, "error": error, "name": v.stem, "offset": DELAY,
                "series": v.parent.name, "dir": str(v.parent),
                "pos": pos if dur and pos / dur < WATCHED else 0,
                "prev": str(eps[i - 1]) if i > 0 else None,
                "next": str(eps[i + 1]) if 0 <= i < len(eps) - 1 else None,
            })
        elif path == "/offset":
            if v := allowed(body.get("video")):
                with STATE_LOCK:
                    STATE.setdefault("offsets", {})[str(v.parent)] = round(float(body["delay"]), 2)
                    save_state()
            self.send(body={})
        elif path == "/update":
            TIME = float(body.get("time", 0)), time.time()
            DELAY = float(body.get("delay", 0))
            self.send(body={})
        elif path == "/check":
            check(clock())
            self.send(body={})
        elif path == "/progress":
            if v := allowed(body.get("video")):
                with STATE_LOCK:
                    STATE["progress"][str(v)] = {"pos": float(body["pos"]), "dur": float(body["dur"])}
                    save_state()
            self.send(body={})
        elif path == "/quit":
            log_event("stopping: quit requested")
            self.send(body={})
            threading.Thread(target=self.server.shutdown).start()
        elif path == "/options":
            OPTIONS.update({k: body[k] for k in OPTIONS if k in body})
            save_options()
            self.send(body=OPTIONS)
        elif path == "/api/note-type":
            try:
                create_note_type(str(body.get("deck") or "").strip() or "Mining")
            except AnkiError as e:
                return self.send(body={"error": str(e)})
            self.send(body={**OPTIONS, "model": NOTE_TYPE})
        else:
            self.send(404)

    def log_message(self, format, *args):
        pass


def log_event(msg):
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


if __name__ == "__main__":
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with ThreadingHTTPServer(("127.0.0.1", PORT), Server) as server:
        log_event(f"ani server on http://127.0.0.1:{PORT} (pid {os.getpid()})")

        def stop(signum, _frame):  # say why the server went away instead of vanishing
            log_event(f"stopping on {signal.Signals(signum).name}")
            threading.Thread(target=server.shutdown).start()  # shutdown() waits for serve_forever

        for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            signal.signal(sig, stop)
        try:
            server.serve_forever()
        finally:
            for child in list(CHILDREN):
                kill_group(child)
            log_event("stopped")
