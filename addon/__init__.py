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
from aqt.utils import askUser, openLink, showInfo, showWarning, tooltip

HERE = Path(__file__).parent
BIN = HERE / "user_files" / "bin"  # user_files survives add-on updates
STATE = Path(os.environ.setdefault("ANI_STATE", str(HERE / "user_files")))
# ani used to be a command with its library in its own state folder; on the add-on's first start,
# bring that library along so switching to the add-on doesn't empty it.
CLI_STATE = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "ani"
if not (STATE / "state.json").exists() and CLI_STATE.resolve() != STATE.resolve():
    for name in ("state.json", "options.json"):
        if (CLI_STATE / name).is_file():
            STATE.mkdir(parents=True, exist_ok=True)
            shutil.copy2(CLI_STATE / name, STATE / name)
# Tools already installed win; downloaded ones fill the gaps. Apps opened from the macOS Dock
# don't see Homebrew's PATH, so add its folders too.
os.environ["PATH"] = os.pathsep.join(filter(None, [os.environ.get("PATH"), "/opt/homebrew/bin", "/usr/local/bin", str(BIN)]))

from . import server  # noqa: E402  reads ANI_STATE and PATH on import

URL = f"http://127.0.0.1:{server.PORT}"
ANKICONNECT = 2055492159
RELEASE = "https://github.com/Carter-FS/ani/releases/download/tools-1/"  # never re-upload to a published tag
BUNDLES = {  # (sys.platform, machine) -> (file, sha256); built by addon/tools.sh
    ("darwin", "arm64"): ("ani-tools-mac-arm64.zip", "8498aaaf5d3dea4e1f5b6ace8e01c131ee3b60053c25225233368266d868596a"),
    ("darwin", "x86_64"): ("ani-tools-mac-x64.zip", "9bc7f0d9812dfd75ff3279531076820b346ae9aac100dcd82da90b70304182ec"),
    ("win32", "AMD64"): ("ani-tools-win64.zip", "e460fd0f750cb37a22163d489f11d15d52ffb87a4741bd62bce14bf3651ecc25"),
    ("linux", "x86_64"): ("ani-tools-linux64.zip", "fad36621ec4a971a3716a7c767c4ffa43e5055b15abd4e0f11baaa967a2cf363"),
}
BUNDLES[("win32", "ARM64")] = BUNDLES[("win32", "AMD64")]  # Windows on Arm runs x64 tools emulated



def tools_missing():
    return not (shutil.which("ffmpeg") and shutil.which("ffprobe")
                and (shutil.which("alass-cli") or shutil.which("alass")))


def fetch_tools(name, sha):
    with urllib.request.urlopen(RELEASE + name, timeout=60) as r:
        data = r.read()
    if hashlib.sha256(data).hexdigest() != sha:
        raise ValueError("the download was corrupted")
    # unpack beside bin, then swap it in whole: a crash mid-extract never leaves half-written tools
    part = BIN.with_name("bin.part")
    shutil.rmtree(part, ignore_errors=True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        z.extractall(part)
    for f in part.iterdir():
        f.chmod(0o755)  # zip drops the executable bit
    shutil.rmtree(BIN, ignore_errors=True)
    part.replace(BIN)


FETCHING = threading.Event()  # a profile switch mid-download mustn't start a second one


def ensure_tools():
    if not tools_missing() or FETCHING.is_set():
        return
    bundle = BUNDLES.get((sys.platform, platform.machine()))
    if not bundle:
        return showWarning("ani needs ffmpeg and alass. Install them and restart Anki.", title="ani")
    FETCHING.set()
    tooltip("ani: downloading ffmpeg and alass, about 80 MB. This happens once.", period=8000)

    def done(error):
        FETCHING.clear()
        if error:
            return showWarning(f"ani couldn't download its tools ({error}). It will try again next time Anki starts.",
                               title="ani")
        server.FAILED.clear()  # syncs tried before the tools arrived get another go
        tooltip("ani is ready: Tools &gt; ani: Library")

    def work():
        # A plain daemon thread, not Anki's task queue: that one also runs syncs and reviews, and
        # Anki's exit would wait for the download.
        try:
            fetch_tools(*bundle)
            error = None
        except Exception as e:  # noqa: BLE001  any failure here just means "try again next start"
            error = e
        mw.taskman.run_on_main(lambda: done(error))

    threading.Thread(target=work, daemon=True, name="ani tools").start()


def ensure_ankiconnect():
    """Offer to install AnkiConnect, from AnkiWeb or by hand under any folder name."""
    mgr = mw.addonManager
    found = [d for d in mgr.allAddons() if d == str(ANKICONNECT) or "ankiconnect" in mgr.addonName(d).lower().replace(" ", "")]
    if not found:
        # ponytail: asks every start until installed; remember a "no" if that gets annoying
        if askUser("ani needs the AnkiConnect add-on so Yomitan can add cards. Install it now?", title="ani"):
            download_addons(mw, mgr, [ANKICONNECT], lambda log: show_log_to_user(mw, log))
    elif not any(mgr.isEnabled(d) for d in found):
        showWarning("AnkiConnect is turned off, so Yomitan can't add cards. Turn it on in Tools > Add-ons "
                    "and restart Anki.", title="ani")


THREAD = None


def running():
    try:
        with urllib.request.urlopen(URL + "/options", timeout=2) as r:
            return isinstance(json.load(r), dict)
    except (OSError, ValueError):
        return False


def start_server():
    """Start the server unless it is running. Returns whether ani answers on its port."""
    global THREAD
    if THREAD and THREAD.is_alive():
        if not server.STOPPED.is_set():
            return True
        THREAD.join(2)  # stopping: let it free the port
    server.STOPPED.clear()
    try:
        httpd = server.Http(("127.0.0.1", server.PORT), server.Server)
    except OSError:  # the port is taken: fine if ani already answers there (another Anki window)
        if running():
            return True
        showWarning(f"Another program is using port {server.PORT}, so ani can't start. Close it, or set the "
                    "ANI_PORT environment variable to another port.", title="ani")
        return False
    THREAD = threading.Thread(target=serve, args=(httpd,), daemon=True, name="ani")
    THREAD.start()
    return True


def serve(httpd):
    with httpd:
        httpd.serve_forever()  # returns after /quit
    stop_tools()


def stop_tools():  # syncs still running when ani stops or Anki quits
    for p in list(server.CHILDREN):
        server.kill_group(p)


def stop():
    try:
        urllib.request.urlopen(urllib.request.Request(URL + "/quit", b"{}"), timeout=5).close()
        tooltip("ani stopped")
    except OSError:
        tooltip("ani isn't running")


def library():
    if start_server():
        openLink(URL + "/?continue")


def add_series():
    if not start_server():
        return
    folder = QFileDialog.getExistingDirectory(mw, "ani: choose the folder with the episodes")
    if not folder:
        return
    # starts beside the episode folder, where a subtitle folder usually is; picking the episode folder
    # itself means the subtitles are with the episodes, same as Cancel
    subs = QFileDialog.getExistingDirectory(
        mw, "ani: choose the Japanese subtitle folder (Cancel if they are with the episodes)", str(Path(folder).parent))
    folder = str(Path(folder).resolve())  # the same spelling the server stores
    subs = subs and str(Path(subs).resolve())
    body = {"dir": folder, **({"subs": subs} if subs and subs != folder else {})}
    try:
        urllib.request.urlopen(urllib.request.Request(URL + "/register", json.dumps(body).encode()), timeout=10).close()
    except urllib.error.HTTPError as e:
        try:
            why = json.load(e).get("error", e)
        except ValueError:
            why = e
        return showWarning(f"ani couldn't add that folder: {why}", title="ani")
    except OSError as e:
        return showWarning(f"ani couldn't add that folder: {e}", title="ani")
    openLink(URL + "/?dir=" + quote(folder))


def flatpak_note():
    """Anki from Flathub sees only folders picked in a dialog, so files dropped onto a series can't be
    found in Downloads. Say once how to give it access."""
    noted = HERE / "user_files" / "flatpak-noted"
    if not os.path.exists("/.flatpak-info") or noted.exists() or any(r.is_dir() for r in server.SEARCH_ROOTS):
        return
    noted.parent.mkdir(parents=True, exist_ok=True)
    noted.touch()
    showInfo("This Anki is the Flathub version, which can't see your Downloads or Videos folders. Adding "
             "series through Tools &gt; ani works, but files dropped onto a series page won't be found. "
             "To fix that, run this in a terminal and restart Anki:<br><br>"
             "<code>flatpak override --user --filesystem=home net.ankiweb.Anki</code>", title="ani", textFormat="rich")


def on_profile():
    start_server()
    ensure_ankiconnect()
    ensure_tools()
    flatpak_note()


atexit.register(stop_tools)
for text, fn in (("ani: Library", library), ("ani: Add series...", add_series), ("ani: Stop", stop)):
    action = QAction(text, mw)
    action.triggered.connect(fn)
    mw.form.menuTools.addAction(action)
gui_hooks.profile_did_open.append(on_profile)
