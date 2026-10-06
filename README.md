# ani

Watch local anime in the browser with Yomitan-scannable subtitles and one-click Anki cards. Adding a card with Yomitan automatically fills in the subtitle sentence, a screenshot and the sentence audio.

Based on [Autocards](https://learnjapanese.moe/autocards/) by かにふぁん, reworked to run in the browser on macOS and Linux.

## Features

- Subtitles drawn over the video as text, so Yomitan can scan them
- Card fields filled through AnkiConnect: sentence, screenshot (JPEG) and sentence audio (MP3)
- Subtitles synced to the video's audio with [alass](https://github.com/kaegi/alass) on first play; the rest of the season syncs in the background
- Library with thumbnails and watch progress; resumes where you left off
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

git clone <repo-url> ani
ln -s "$PWD/ani/ani" ~/.local/bin/ani   # any directory on PATH
```

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

With a subtitle folder, each video is matched to a subtitle by episode number (`S01E03`, `- 03`, `第3話`). Synced subtitles are saved next to each video as `<video>.ja.srt`.

## Keys

| Key | Action |
| --- | --- |
| Space / K | Play or pause |
| Left / Right | Previous or next line |
| R | Replay the current line |
| `[` / `]` | Subtitle offset -/+ 0.1s |
| F | Fullscreen |
| N / P | Next or previous episode |
| H | Toggle hover pause |
| L | Library |

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `ANI_BROWSER` | system default | Browser to open; macOS app name or Linux command name |
| `ANI_PORT` | `6969` | Server port |
| `ANI_STATE` | `$XDG_STATE_HOME/ani` | State, settings, thumbnails and the server log |
| `ANKICONNECT` | `http://127.0.0.1:8765` | AnkiConnect URL |

## Development

```sh
python3 test_ani.py
```

## Licence

GPL-3.0-or-later. See [LICENSE](LICENSE).
