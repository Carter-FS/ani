# SPDX-License-Identifier: GPL-3.0-or-later
"""ani as an Anki add-on: runs the ani server inside Anki and adds entries to the Tools menu.
Cards are still filled through AnkiConnect, which Yomitan needs to add them anyway."""

import atexit
import hashlib
import io
import json
import os
import platform
import shutil
import sys
import threading
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import quote

from aqt import gui_hooks, mw
from aqt.addons import download_addons, show_log_to_user
from aqt.qt import QAction, QFileDialog
from aqt.utils import askUser, openLink, showWarning, tooltip

HERE = Path(__file__).parent
BIN = HERE / "user_files" / "bin"  # user_files survives add-on updates
os.environ.setdefault("ANI_STATE", str(HERE / "user_files"))
# Tools already installed win; downloaded ones fill the gaps. Apps opened from the macOS Dock
# don't see Homebrew's PATH, so add its folders too.
os.environ["PATH"] = os.pathsep.join(filter(None, [os.environ.get("PATH"), "/opt/homebrew/bin", "/usr/local/bin", str(BIN)]))

from . import server  # noqa: E402  reads ANI_STATE and PATH on import

URL = f"http://127.0.0.1:{server.PORT}"
ANKICONNECT = 2055492159
RELEASE = "https://github.com/Carter-FS/ani/releases/download/tools-1/"
BUNDLES = {  # (sys.platform, machine) -> (file, sha256); built by addon/tools.sh
    ("darwin", "arm64"): ("ani-tools-mac-arm64.zip", "453ff55aebeb34106b993bb5bf5fdd3d7afe24be37b14a02150c4245fca8016b"),
    ("win32", "AMD64"): ("ani-tools-win64.zip", "f26537625962fe106a48b0147181da6ea2858bfd7c50f70823fd268eac3f17fa"),
    ("linux", "x86_64"): ("ani-tools-linux64.zip", "5b1172e228714ca580c109bdbdd54b8fe7156b8b8484919a86bc19c21946b81d"),
}


def tools_missing():
    return not (shutil.which("ffmpeg") and shutil.which("ffprobe")
                and (shutil.which("alass-cli") or shutil.which("alass")))


def fetch_tools(name, sha):
    with urllib.request.urlopen(RELEASE + name, timeout=60) as r:
        data = r.read()
    if hashlib.sha256(data).hexdigest() != sha:
        raise ValueError("the download was corrupted")
    BIN.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        z.extractall(BIN)
    for f in BIN.iterdir():
        f.chmod(0o755)  # zip drops the executable bit


def ensure_tools():
    if not tools_missing():
        return
    bundle = BUNDLES.get((sys.platform, platform.machine()))
    if not bundle:
        return showWarning("ani needs ffmpeg and alass. Install them (on a Mac: <code>brew install ffmpeg alass</code>) "
                           "and restart Anki.", title="ani")
    tooltip("ani: downloading ffmpeg and alass, about 80 MB. This happens once.", period=8000)

    def done(fut):
        try:
            fut.result()
            tooltip("ani is ready: Tools &gt; ani: Library")
        except Exception as e:  # noqa: BLE001  any failure here just means "try again next start"
            showWarning(f"ani couldn't download its tools ({e}). It will try again next time Anki starts.",
                        title="ani")

    mw.taskman.run_in_background(lambda: fetch_tools(*bundle), done)


def ensure_ankiconnect():
    # ponytail: asks every start until installed; remember a "no" if that gets annoying
    if str(ANKICONNECT) not in mw.addonManager.allAddons() and askUser(
            "ani needs the AnkiConnect add-on so Yomitan can add cards. Install it now?", title="ani"):
        download_addons(mw, mw.addonManager, [ANKICONNECT], lambda log: show_log_to_user(mw, log))


def start_server():
    try:
        httpd = server.ThreadingHTTPServer(("127.0.0.1", server.PORT), server.Server)
    except OSError:  # most likely the ani command's own server, which works just as well
        return
    threading.Thread(target=httpd.serve_forever, daemon=True, name="ani").start()
    atexit.register(stop_tools)


def stop_tools():  # syncs still running when Anki quits
    for p in list(server.CHILDREN):
        server.kill_group(p)


def library():
    openLink(URL + "/?continue")


def add_series():
    folder = QFileDialog.getExistingDirectory(mw, "ani: choose the folder with the episodes")
    if not folder:
        return
    subs = QFileDialog.getExistingDirectory(
        mw, "ani: choose the Japanese subtitle folder (Cancel if they are with the episodes)", folder)
    folder = str(Path(folder).resolve())  # the same spelling the server stores
    body = {"dir": folder, **({"subs": str(Path(subs).resolve())} if subs else {})}
    try:
        urllib.request.urlopen(urllib.request.Request(URL + "/register", json.dumps(body).encode()), timeout=10)
    except urllib.error.HTTPError as e:
        return showWarning(f"ani couldn't add that folder: {json.load(e).get('error', e)}", title="ani")
    except OSError as e:
        return showWarning(f"ani couldn't add that folder: {e}", title="ani")
    openLink(URL + "/?dir=" + quote(folder))


def on_profile():
    ensure_ankiconnect()
    ensure_tools()


start_server()
for text, fn in (("ani: Library", library), ("ani: Add series...", add_series)):
    action = QAction(text, mw)
    action.triggered.connect(fn)
    mw.form.menuTools.addAction(action)
gui_hooks.profile_did_open.append(on_profile)
