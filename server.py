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
import urllib.error
import urllib.request
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
ANKI = os.environ.get("ANKICONNECT", "http://127.0.0.1:8765")
FFMPEG = "ffmpeg"
ALASS = shutil.which("alass-cli") or shutil.which("alass") or "alass-cli"

VIDEO_EXTS = {".mp4", ".m4v", ".mkv", ".webm", ".mov"}
SUB_EXTS = {".srt", ".ass"}
EP_PATTERNS = [
    r"S\d+E(\d+)",
    r"[-_ ]\s*(\d{1,4})(?=\s*[\[(.v]|\s*$)",
    r"第(\d+)話",
]
WATCHED = 0.9


# --- subtitles -------------------------------------------------------------

def to_ms(h=0, m=0, s=0, ms=0):
    return ms + 1000 * (s + 60 * (m + 60 * h))


def parse_srt(srt):
    def parse_ts(ts):
        hh, mm, rest = ts.split(":")
        ss, ms = rest.split(",")
        hh, mm, ss, ms = int(hh), int(mm), int(ss), int(ms)
        return to_ms(hh, mm, ss, ms), f"{hh}:{mm:02}:{round(ss + ms / 1000):02}"

    def parse_times(t):
        start, end = t.strip().split("-->")
        return *parse_ts(start.rstrip()), *parse_ts(end.lstrip())

    blocks = [v.replace("{\\an8}", "").split("\n") for v in srt.strip().split("\n\n")]
    return [[rest, *parse_times(t)] for _, t, *rest in blocks]


def parse_ass(ass):
    def parse_ts(ts):
        h, m, rest = ts.split(":")
        s, cs = rest.split(".")
        h, m, s, ms = int(h), int(m), int(s), int(cs) * 10
        return to_ms(h, m, s, ms), f"{h}:{m:02}:{round(s + ms / 1000):02}"

    lines, cols = [], []
    tag_re = re.compile(r"\{[^}]+\}")
    for line in ass.splitlines():
        line = line.strip()
        if line.startswith("Format:"):
            cols = [x.strip().lower() for x in line[7:].split(",")]
        elif line.startswith("Dialogue:") and cols:
            parts = line[9:].split(",", len(cols) - 1)
            if len(parts) != len(cols):
                continue
            row = dict(zip(cols, parts))
            text = tag_re.sub("", row.get("text", ""))
            text = text.replace("\\N", "\n").replace("\\n", "\n").replace("\\h", " ")
            lines.append([text.split("\n"), *parse_ts(row["start"]), *parse_ts(row["end"])])
    lines.sort(key=lambda x: x[1])
    return lines


DELIM = re.compile(r"(「|」|『|』|\"|\'|\.|!|\?|．|。|…|︒|！|？|︙|\s|<b>|</b>|﻿)")


def normalize_str(s):
    return DELIM.sub("", s)


def episode(name):
    for pat in EP_PATTERNS:
        m = re.search(pat, Path(name).stem, re.IGNORECASE)
        if m:
            return int(m.group(1))
    return None


def synced_path(video):
    return video.with_name(video.stem + ".ja.srt")


def find_sub(video, where):
    """Subtitle file for `video` from a file or a folder (matched by episode number)."""
    if where.is_file():
        return where
    ep = episode(video.name)
    if ep is None:
        raise LookupError(f"no episode number in {video.name}")
    hits = [f for f in sorted(where.iterdir())
            if f.suffix in SUB_EXTS and not f.name.endswith(".ja.srt") and episode(f.name) == ep]
    if len(hits) != 1:
        raise LookupError(f"{len(hits)} subtitles for episode {ep} in {where}")
    return hits[0]


def sync(video, sub, out):
    """Align `sub` to the video's audio with alass. Writes nothing on failure so it retries later."""
    print(f"syncing {sub.name}", flush=True)
    # alass trips on [] in paths, so run it on clean symlinks.
    # fps guessing off: it misfires on releases with equal frame rates (e.g. 25/23.976).
    with tempfile.TemporaryDirectory() as tmp:
        v, s, o = Path(tmp, "v" + video.suffix), Path(tmp, "s" + sub.suffix), Path(tmp, "o.srt")
        v.symlink_to(video)
        s.symlink_to(sub)
        r = subprocess.run([ALASS, "--disable-fps-guessing", str(v), str(s), str(o)],
                           capture_output=True, text=True)
        if r.returncode == 0 and o.exists():
            shutil.copyfile(o, out)
            return True
    print(f"alass failed on {video.name}: {r.stderr.strip().splitlines()[-1:]}", flush=True)
    return False


# --- state -----------------------------------------------------------------

STATE_LOCK = threading.Lock()
SYNC_LOCK = threading.Lock()
BG_DIRS = set()


def load_json(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


STATE = load_json(STATE_PATH, {})
STATE.setdefault("series", {})     # video dir -> {"subs": subtitle dir or None}
STATE.setdefault("overrides", {})  # video path -> subtitle file
STATE.setdefault("progress", {})   # video path -> {"pos": s, "dur": s}


def save_state():
    with STATE_LOCK:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(STATE, ensure_ascii=False, indent=1))
        tmp.replace(STATE_PATH)


def episodes(folder):
    files = [f for f in Path(folder).iterdir() if f.suffix.lower() in VIDEO_EXTS]
    return sorted(files, key=lambda f: (episode(f.name) is None, episode(f.name) or 0, f.name))


def allowed(path):
    """Only serve video files inside registered series folders."""
    if not path:
        return None
    p = Path(path).resolve()
    if p.suffix.lower() in VIDEO_EXTS and str(p.parent) in STATE["series"] and p.is_file():
        return p
    return None


def ensure_synced(video):
    """Synced subtitle path, or the unsynced source if alass failed."""
    out = synced_path(video)
    if out.exists():
        return out
    src = STATE["overrides"].get(str(video)) or STATE["series"].get(str(video.parent), {}).get("subs")
    sub = find_sub(video, Path(src) if src else video.parent)
    with SYNC_LOCK:
        if out.exists() or sync(video, sub, out):
            return out
    return sub


def sync_season(folder):
    """Sync every episode in the background so autoplaying the next one is instant."""
    if folder in BG_DIRS:
        return
    BG_DIRS.add(folder)

    def run():
        try:
            for v in episodes(folder):
                try:
                    ensure_synced(v)
                except LookupError:
                    pass
        finally:
            BG_DIRS.discard(folder)

    threading.Thread(target=run, daemon=True).start()


def progress(video):
    p = STATE["progress"].get(str(video), {})
    return p.get("pos", 0), p.get("dur", 0)


def is_watched(video):
    pos, dur = progress(video)
    return dur > 0 and pos / dur >= WATCHED


def continue_target():
    last = STATE.get("last")
    if not last or not Path(last).exists():
        return None
    last = Path(last)
    if not is_watched(last):
        return last
    eps = episodes(last.parent)
    i = eps.index(last)
    return eps[i + 1] if i + 1 < len(eps) else last


def thumb(video):
    out = THUMBS / (hashlib.sha1(str(video).encode()).hexdigest() + ".jpg")
    if not out.exists():
        THUMBS.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "csv=p=0", str(video)], capture_output=True, text=True)
        try:
            at = float(r.stdout) * 0.3
        except ValueError:
            at = 0
        subprocess.run([FFMPEG, "-v", "error", "-ss", f"{at:.1f}", "-i", str(video),
                        "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "5", str(out)])
    return out if out.exists() else None


# --- anki ------------------------------------------------------------------

ID = ""
FILE = ""
LINES = []
NORMALIZED = []
IGNORE = set()
DELAY = 0
TIME = 0, time.time()
CHECK_LOCK = threading.Lock()

OPTIONS = {"deck": "", "sentence": "Sentence", "expression": "Expression",
           "picture": "Picture", "audio": "SentenceAudio", "prev_lines": 0, "next_lines": 0}
OPTIONS.update(load_json(OPTIONS_PATH, {}))


def token():
    return random.randbytes(8).hex()


def clock():
    return TIME[0] + min(time.time() - TIME[1], 1.0)


def load_subs(path):
    global ID, LINES, NORMALIZED
    content = Path(path).read_text(encoding="utf-8-sig", errors="replace").replace("\r\n", "\n")
    try:
        if content.lstrip().startswith("[Script Info]") or "Dialogue:" in content:
            LINES = parse_ass(content)
        else:
            LINES = parse_srt(content)
    except (ValueError, KeyError) as e:
        print(f"subtitle parse error: {e}", flush=True)
        LINES = []
    NORMALIZED = [normalize_str("".join(line[0])) for line in LINES]
    ID = token()


def invoke(action, **params):
    req = json.dumps({"action": action, "params": params, "version": 6}).encode()
    try:
        r = json.load(urllib.request.urlopen(urllib.request.Request(ANKI, req)))
    except (urllib.error.URLError, ValueError):
        return None
    if r.get("error") is not None:
        print(f"anki {action}: {r['error']}", flush=True)
        return None
    return r.get("result")


def take_screenshot(src, start, end):
    file = f"autocards-{token()}.jpg"
    path = STATE_DIR / file
    subprocess.run([FFMPEG, "-v", "error", "-ss", f"{0.75 * start + 0.25 * end:.3f}", "-i", src,
                    "-frames:v", "1", "-vf", "scale=iw/2:-2", "-q:v", "4", str(path)])
    return store_media(file, path)


def take_audio(src, start, end):
    file = f"autocards-{token()}.mp3"
    path = STATE_DIR / file
    subprocess.run([FFMPEG, "-v", "error", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}",
                    "-i", src, "-map", "0:a:0", "-ac", "1", "-c:a", "libmp3lame", "-q:a", "4",
                    str(path)])
    return store_media(file, path)


def store_media(file, path):
    if not path.exists():
        return None
    r = invoke("storeMediaFile", filename=file, path=str(path))
    path.unlink()
    return r


def update_note(note_id, idx, expression=None, original_sentence=None):
    lo = max(0, idx - OPTIONS.get("prev_lines", 0))
    hi = min(len(LINES) - 1, idx + OPTIONS.get("next_lines", 0))
    subset = LINES[lo:hi + 1]
    sentence = "<br/>".join("<br/>".join(line[0]) for line in subset)

    bold = set()
    if expression:
        bold.add(expression)
    if original_sentence:
        bold.update(filter(None, re.findall(r"<b>(.*?)</b>", original_sentence)))
    if bold:
        pattern = "|".join(re.escape(w) for w in sorted(bold, key=len, reverse=True))
        sentence = re.sub(pattern, lambda m: f"<b>{m.group(0)}</b>", sentence)

    start = subset[0][1] / 1000 + DELAY
    end = subset[-1][3] / 1000 + DELAY
    fields = {OPTIONS["sentence"]: sentence}
    picture = take_screenshot(FILE, start, end)
    if picture:
        fields[OPTIONS["picture"]] = f'<img src="{picture}">'
    sound = take_audio(FILE, start, end)
    if sound:
        fields[OPTIONS["audio"]] = f"[sound:{sound}]"
    invoke("updateNote", note={"id": note_id, "fields": fields})
    print(f"updated note {note_id}", flush=True)


def check(t):
    """Fill new cards whose sentence matches a subtitle line already played."""
    if not CHECK_LOCK.acquire(blocking=False):
        return
    try:
        if not ID or not all(OPTIONS[k] for k in ("deck", "sentence", "picture", "audio")):
            return
        deck = OPTIONS["deck"].strip('"')
        ids = invoke("findCards",
                     query=f'"deck:{deck}" added:1 {OPTIONS["picture"]}: {OPTIONS["audio"]}:')
        if not ids:
            return
        end_idx = bisect.bisect_left(LINES, to_ms(s=t), key=lambda v: v[1])
        subs = {v: i for i, v in enumerate(NORMALIZED[:end_idx])}
        for card in invoke("cardsInfo", cards=[i for i in ids if i not in IGNORE]) or []:
            f = card["fields"]
            sentence = f.get(OPTIONS["sentence"], {}).get("value", "")
            idx = subs.get(normalize_str(sentence))
            if idx is None:
                continue
            expression = f.get(OPTIONS["expression"], {}).get("value") if OPTIONS["expression"] else None
            update_note(card["note"], idx, expression, sentence)
            IGNORE.add(card["cardId"])
    finally:
        CHECK_LOCK.release()


# --- http ------------------------------------------------------------------

def parse_range(header, size):
    """(start, end) inclusive for a `Range: bytes=` header, or None for the whole file."""
    m = re.fullmatch(r"bytes=(\d*)-(\d*)", header or "")
    if not m or not (m[1] or m[2]):
        return None
    if not m[1]:
        return max(0, size - int(m[2])), size - 1
    return int(m[1]), min(int(m[2]), size - 1) if m[2] else size - 1


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
        rng = parse_range(self.headers.get("Range"), size)
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

    def do_GET(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}

        if url.path == "/":
            self.send(body=(BASE / "index.html").read_bytes(), ctype="text/html; charset=utf-8")
        elif url.path == "/api/library":
            folder = q.get("dir") or (str(Path(STATE["last"]).parent) if STATE.get("last") else "")
            if folder not in STATE["series"]:
                return self.send(body={"dir": "", "episodes": [], "series": list(STATE["series"])})
            sync_season(folder)
            target = continue_target()
            eps = []
            for v in episodes(folder):
                pos, dur = progress(v)
                eps.append({"path": str(v), "ep": episode(v.name), "name": v.stem,
                            "synced": synced_path(v).exists(), "pos": pos, "dur": dur,
                            "watched": is_watched(v)})
            self.send(body={"dir": folder, "name": Path(folder).name, "episodes": eps,
                            "series": list(STATE["series"]),
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
        global OPTIONS, TIME, FILE, DELAY
        # Browsers send Origin on cross-site POSTs; refuse them so other pages can't drive the server.
        origin = self.headers.get("Origin")
        if origin and origin not in (f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"):
            return self.send(403)
        size = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(size) or b"{}")
        except ValueError:
            return self.send(400)
        path = urlparse(self.path).path

        if path == "/register":
            folder = str(Path(body["dir"]).resolve())
            entry = STATE["series"].setdefault(folder, {"subs": None})
            if body.get("subs"):
                entry["subs"] = str(Path(body["subs"]).resolve())
            if body.get("video") and body.get("sub_file"):
                STATE["overrides"][str(Path(body["video"]).resolve())] = str(Path(body["sub_file"]).resolve())
            if body.get("video") and body.get("resync"):
                synced_path(Path(body["video"]).resolve()).unlink(missing_ok=True)
            save_state()
            self.send(body={"ok": True})
        elif path == "/play":
            v = allowed(body.get("video"))
            if not v:
                return self.send(404, {"error": "unknown video"})
            error = None
            try:
                load_subs(ensure_synced(v))
            except (LookupError, OSError) as e:
                error = str(e)
                load_subs_empty()
            FILE, DELAY = str(v), 0
            STATE["last"] = str(v)
            save_state()
            sync_season(str(v.parent))
            eps = episodes(v.parent)
            i = eps.index(v)
            pos, dur = progress(v)
            self.send(body={
                "id": ID, "lines": LINES, "error": error, "name": v.stem,
                "series": v.parent.name, "dir": str(v.parent),
                "pos": pos if dur and pos / dur < WATCHED else 0,
                "prev": str(eps[i - 1]) if i > 0 else None,
                "next": str(eps[i + 1]) if i + 1 < len(eps) else None,
            })
        elif path == "/update":
            TIME = float(body.get("time", 0)), time.time()
            DELAY = float(body.get("delay", 0))
            self.send(body={})
        elif path == "/check":
            check(clock())
            self.send(body={})
        elif path == "/progress":
            v = allowed(body.get("video"))
            if v:
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


def load_subs_empty():
    global ID, LINES, NORMALIZED
    ID, LINES, NORMALIZED = token(), [], []


if __name__ == "__main__":
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"ani server on http://127.0.0.1:{PORT}", flush=True)
    with ThreadingHTTPServer(("127.0.0.1", PORT), Server) as server:
        server.serve_forever()
