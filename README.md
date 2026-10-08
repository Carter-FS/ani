# ani

Watch local anime in the browser with Yomitan-scannable subtitles and one-click Anki cards. Adding a card with Yomitan automatically fills in the subtitle sentence, a screenshot and the sentence audio.

Based on [Autocards](https://learnjapanese.moe/autocards/) by かにふぁん, reworked to run in the browser on macOS and Linux.

<img width="1409" height="813" alt="ani playing an episode with subtitles over the video" src="https://github.com/user-attachments/assets/fc04f900-f5ad-406b-93d4-88c25efc305d" />

**Status:** Active

## Features

- Subtitles drawn over the video as text, so Yomitan can scan them
- Card fields filled through AnkiConnect: sentence, screenshot (JPEG) and sentence audio (MP3)
- Subtitles synced with [alass](https://github.com/kaegi/alass) on first play, to the release's own embedded subtitles when they fit, else to the Japanese audio track; the rest of the season syncs in the background
- Library with thumbnails and watch progress; resumes where you left off
- Drag and drop episodes or subtitles onto a series to move them in and sync them
- Spoiler-free transcript, pause on hovering the subtitle, line-by-line navigation

## Requirements

- Python 3.10+, ffmpeg, alass
- Anki with [AnkiConnect](https://ankiweb.net/shared/info/2055492159)
- A browser with [Yomitan](https://yomitan.wiki/) that can decode your video files. HEVC (x265) works in Firefox 137+ and in Chromium-based browsers on macOS; on Linux it needs hardware decoding (VA-API).

## Install

```sh
# macOS
brew install python ffmpeg alass

# Arch Linux
sudo pacman -S python ffmpeg
yay -S alass

git clone https://github.com/Carter-FS/ani.git
cd ani
ln -sf "$PWD/ani" ~/.local/bin/ani   # any directory on PATH
```

If you move the clone later, run the `ln` line again from its new location.

## Anki setup

1. Pick a mining deck and note type. The note type needs fields for the sentence, the target word, a picture and the sentence audio. The picture and sentence audio fields must be ones Yomitan leaves empty.
2. In Yomitan's Anki settings, map the sentence field to `{sentence}`.
3. Open ani, click the settings icon, and enter the deck and field names.

Cards are matched by their sentence against subtitle lines already played. Only cards added today with empty picture and audio fields are filled in.

## Usage

```sh
ani <video> <subtitles>    # first play; subtitles is a file or a folder
ani <video>                # later plays
ani <folder> [subtitles]   # open a series in the library
ani                        # continue where you left off
ani --resync <video>       # sync the subtitle again
ani --stop                 # stop the background server
```

With a subtitle folder, each video is matched to a subtitle by episode number (`S01E03`, `- 03`, `第3話`). Synced subtitles are saved next to each video as `<video>.ani.srt` (or `.ani.ass`); files you provide are never modified. Files still being written are synced once unchanged for a minute.

The browser plays the first audio track it can decode, so put the Japanese track first on dual-audio releases. A track the browser can't decode is skipped, which happened with FLAC in MKV under Zen (Firefox-based).

To add episodes from the browser, drop files onto a series page or use **Add episodes**. Browsers do not expose file paths, so the server finds each file by name and size in the `ANI_SEARCH` folders and moves it into the series. Subtitles go to the series' subtitle folder.

## Keys

| Key | Action |
| --- | --- |
| Space / K | Play or pause |
| Left / Right | Previous or next line |
| R | Replay the current line |
| `[` / `]` | Subtitle offset -/+ 0.1s, remembered per series |
| F | Fullscreen |
| N / P | Next or previous episode |
| H | Toggle hover pause |
| L | Library |

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `ANI_BROWSER` | system default | Browser to open; macOS app name or Linux command name |
| `ANI_PORT` | `6969` | Server port |
| `ANI_SEARCH` | `~/Downloads:~/Desktop:~/Movies:~/Videos` | Folders searched for dropped files |
| `ANI_STATE` | `$XDG_STATE_HOME/ani` | State, settings, thumbnails and the server log |
| `ANKICONNECT` | `http://127.0.0.1:8765` | AnkiConnect URL |

## Development

```sh
python3 test_ani.py
```

## Licence

GPL-3.0-or-later. See [LICENSE](LICENSE).
