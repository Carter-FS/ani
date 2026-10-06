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
import subprocess
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


def run(cmd):
    """Run a tool without a terminal: no stdin (ffmpeg would grab the tty), output captured."""
    try:
        return subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              errors="replace")
    except OSError as e:  # tool not installed
        return subprocess.CompletedProcess(cmd, 127, "", str(e))


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
            if cur and cur[2] and cur[2][-1].strip().isdigit():
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
    r"第(\d+)話",
    r"(?:^|[\s._\-\[(])E[Pp]?\.?\s*(\d{1,4})(?=$|[\s._\-\])v])",
]
NOISE = re.compile(
    r"\[[^\]]*\]|\([^)]*\)|【[^】]*】"                              # groups, resolution, hashes
    r"|\b(?:AAC|E?-?AC-?3|DDP?|FLAC|Opus|DTS(?:-HD)?|TrueHD|LPCM)\s*\d\.\d\b"  # audio channels
    r"|\b[HhXx]\.?26[45]\b|\b\d+\s*bits?\b|\b\d{3,4}[pPiI]\b|\b[257]\.[01]\b", re.I)
SPECIAL = re.compile(r"\b(?:OVA|OAD|ONA|SP|Specials?|NCOP|NCED|PV|Preview|Menu|Movie)\b", re.I)
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
    bare = NOISE.sub(" ", stem)
    if SPECIAL.search(bare):
        return None  # OVAs/creditless etc. must not collide with regular episode numbers
    if m := DASH_EP.search(bare):  # first " - N": later ones are usually episode titles
        return _num(m.group(1))
    # last standalone number that isn't a year, e.g. "Show_03", "Show.05", "Mob Psycho 100 03"
    bare = re.sub(r"(\d)\.5(?!\d)", r"\1·5", bare)
    nums = [m.group(1) for t in re.split(r"[\s._\-#＃]+", bare)
            if (m := TOKEN.match(t)) and not re.fullmatch(r"(19|20)\d\d", m.group(1))]  # skip years
    return _num(nums[-1]) if nums else None


def visible(f):
    return not f.name.startswith(".")  # skip macOS ._ AppleDouble files and other dotfiles


def synced_path(video):
    """ani's synced subtitle for `video` if one exists, else None."""
    return next((p for suf in SYNCED_SUFFIXES if (p := video.with_name(video.stem + suf)).exists()), None)


def clear_synced(video):
    for suf in SYNCED_SUFFIXES:
        video.with_name(video.stem + suf).unlink(missing_ok=True)  # only ever ani's own output


def find_sub(video, where):
    """Subtitle file for `video` from a file or a folder (matched by episode number)."""
    if where.is_file():
        return where
    ep = episode(video.name)
    if ep is None:
        raise LookupError(f"no episode number in {video.name}")
    hits = [f for f in sorted(where.iterdir())
            if visible(f) and f.suffix.lower() in SUB_EXTS and not f.name.endswith(SYNCED_SUFFIXES)
            and episode(f.name) == ep]
    if len(hits) != 1:
        raise LookupError(f"{len(hits)} subtitles for episode {ep} in {where}")
    return hits[0]


def sync(video, sub):
    """Align `sub` to the video's audio with alass; returns the synced file or None."""
    print(f"syncing {sub.name}", flush=True)
    out = video.with_name(video.stem + ".ani" + sub.suffix.lower())
    # alass trips on [] in paths, so run it on clean symlinks; it also refuses format
    # conversion, so the output keeps the subtitle's own extension.
    # fps guessing off: it misfires on releases with equal frame rates (e.g. 25/23.976).
    try:
        with tempfile.TemporaryDirectory() as tmp:
            v, s, o = (Path(tmp, "v" + video.suffix), Path(tmp, "s" + sub.suffix.lower()),
                       Path(tmp, "o" + sub.suffix.lower()))
            v.symlink_to(video)
            s.symlink_to(sub)
            r = run([ALASS, "--disable-fps-guessing", str(v), str(s), str(o)])
            if r.returncode != 0 or not o.exists():
                msg = f"{r.stderr}\n{r.stdout}"
                why = [ln.strip() for ln in msg.splitlines() if ln.strip().startswith(("error", "caused by"))]
                print(f"alass failed on {video.name}: {' / '.join(why[:3]) or msg.strip()[-200:]}", flush=True)
                return None
            part = out.with_name(f".{out.name}.part")
            shutil.copyfile(o, part)
            part.replace(out)  # atomic, so /play never reads a half-written file
            return out
    except OSError as e:
        print(f"sync failed on {video.name}: {e}", flush=True)
        return None


# --- state -----------------------------------------------------------------

STATE_LOCK = threading.RLock()
BG_DIRS = set()
BG_LOCK = threading.Lock()


def load_json(path, default):
    try:
        return json.loads(path.read_text())
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
        tmp.write_text(json.dumps(STATE, ensure_ascii=False, indent=1))
        tmp.replace(STATE_PATH)


def episodes(folder):
    try:
        files = [f for f in Path(folder).iterdir() if visible(f) and f.suffix.lower() in VIDEO_EXTS]
    except OSError:
        return []
    return sorted(files, key=lambda f: (episode(f.name) is None, episode(f.name) or 0, f.name))


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


def ensure_synced(video):
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
        if out := sync(video, sub):
            return out
        FAILED[video] = fail_key(video, sub)
    return sub


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
                        ensure_synced(v)
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
    out = THUMBS / (hashlib.sha1(str(video).encode()).hexdigest() + ".jpg")
    if out.exists():
        return out
    with THUMB_SLOTS:
        if out.exists():
            return out
        THUMBS.mkdir(parents=True, exist_ok=True)
        r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)])
        try:
            at = float(r.stdout) * 0.3
        except ValueError:
            return None
        part = out.with_name(f".{out.stem}.{threading.get_ident()}.jpg")
        run([FFMPEG, "-nostdin", "-v", "error", "-y", "-ss", f"{at:.1f}", "-i", str(video),
             "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "5", str(part)])
        if part.exists():
            part.replace(out)
    return out if out.exists() else None


# --- anki ------------------------------------------------------------------

# Everything about the episode playing, swapped as one object so a card check
# running during an episode change never mixes lines from one with media from another.
Session = namedtuple("Session", "id file lines normalized")
SESSION = Session("", "", [], [])
DELAY = 0.0
TIME = 0.0, time.time()
DONE_NOTES = set()
CHECK_LOCK = threading.Lock()

OPTIONS = {"deck": "", "sentence": "Sentence", "expression": "Expression",
           "picture": "Picture", "audio": "SentenceAudio", "prev_lines": 0, "next_lines": 0}
OPTIONS.update(load_json(OPTIONS_PATH, {}))


class AnkiError(Exception):
    pass


def token():
    return random.randbytes(8).hex()


def clock():
    return TIME[0] + min(time.time() - TIME[1], 1.0)


def make_session(file, sub_path):
    lines = []
    if sub_path:
        raw = Path(sub_path).read_bytes()
        encodings = ("utf-16",) if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig", "cp932")
        for enc in encodings:  # cp932: Shift-JIS subtitles are common for Japanese
            try:
                content = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            content = raw.decode("utf-8", "replace")
        content = content.replace("\r\n", "\n").replace("\r", "\n")
        is_ass = content.lstrip().startswith("[Script Info]") or "\nDialogue:" in content
        lines = parse_ass(content) if is_ass else parse_srt(content)
    return Session(token(), file, lines, [normalize_str("".join(ln[0])) for ln in lines])


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
         "-frames:v", "1", "-vf", "scale=iw/2:-2", "-q:v", "4", str(path)])
    return store_media(file, path)


def take_audio(src, start, end):
    file = f"autocards-{token()}.mp3"
    path = STATE_DIR / file
    run([FFMPEG, "-nostdin", "-v", "error", "-ss", f"{max(start, 0):.3f}", "-t", f"{max(end - start, 0.1):.3f}",
         "-i", src, "-map", "0:a:0", "-ac", "1", "-c:a", "libmp3lame", "-q:a", "4", str(path)])
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
    if sound := take_audio(s.file, start, end):
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
                eps.append({"path": str(v), "ep": episode(v.name), "name": v.stem,
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
                    if sub_file and sub_file != STATE["overrides"].get(str(video)):
                        STATE["overrides"][str(video)] = sub_file
                        clear_synced(video)
                    if body.get("resync"):
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
            self.send(body={})
            threading.Thread(target=self.server.shutdown).start()
        elif path == "/options":
            OPTIONS.update({k: body[k] for k in OPTIONS if k in body})
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            OPTIONS_PATH.write_text(json.dumps(OPTIONS, ensure_ascii=False))
            self.send(body=OPTIONS)
        else:
            self.send(404)

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"ani server on http://127.0.0.1:{PORT}", flush=True)
    with ThreadingHTTPServer(("127.0.0.1", PORT), Server) as server:
        server.serve_forever()
