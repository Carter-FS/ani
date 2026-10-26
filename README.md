# ani

Watch local anime in the browser with Yomitan-scannable subtitles and one-click Anki cards. Adding a card with Yomitan automatically fills in the subtitle sentence, a screenshot and the sentence audio.

Based on [Autocards](https://learnjapanese.moe/autocards/) by かにふぁん, reworked to run in the browser on Windows, macOS and Linux.

<img width="1409" height="813" alt="ani playing an episode with subtitles over the video" src="https://github.com/user-attachments/assets/fc04f900-f5ad-406b-93d4-88c25efc305d" />

**Status:** Active

## Features

- Subtitles drawn over the video as text, so Yomitan can scan them
- Cards get the sentence, a screenshot and the sentence audio automatically
- Subtitles synced to the episode automatically, the rest of the season in the background
- Library with thumbnails and watch progress; resumes where you left off
- Spoiler-free transcript, pause on hovering the subtitle, line-by-line navigation

## Install

You need [Anki](https://apps.ankiweb.net/) 25.07 or later and a browser with [Yomitan](https://yomitan.wiki/).

1. In Anki, open Tools > Add-ons > Get Add-ons, enter `719365920` and restart Anki.
2. Say yes when ani offers to install AnkiConnect, then restart Anki again. On first start ani also downloads the tools it needs (about 80 MB).
3. Open Tools > ani: Library, click the settings icon, type a deck name and click **Create the ani note type for this deck**. Then copy the field list it shows into Yomitan's Anki settings.

Already using [Lapis](https://github.com/donkuri/lapis)? Skip step 3's button: Lapis has the same field names ani fills by default (`Expression`, `Sentence`, `Picture`, `SentenceAudio`), so in ani's settings just pick your mining deck. Keep Lapis's Yomitan setup as it is; ani matches the sentence with its `<b>` tags removed and leaves Lapis's bolding in place.

On Anki from Flathub, also run `flatpak override --user --filesystem=home net.ankiweb.Anki` so ani can find files you drop onto a series.

## Use

- **Tools > ani: Add series** picks a folder of episodes and, if they're elsewhere, a folder of Japanese subtitles. Episodes are matched to subtitles by number (`S01E03`, `- 03`, `第3話`).
- **Tools > ani: Library** opens the player. Anki must stay open while you watch.
- Add a card with Yomitan as usual; ani fills in the picture and audio within a few seconds.
- To add episodes later, drop them onto the series page.
- **Tools > ani: Stop**, or the power button in the player, stops ani.

Your video and subtitle files are never changed; synced subtitles are saved beside each video as `<video>.ani.srt`.

### Keys

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

### Troubleshooting

- **No picture or sound:** the browser can't decode the file. HEVC (x265) works in Firefox 137+ and in Chromium-based browsers on macOS; on Linux it needs hardware decoding (VA-API).
- **Wrong audio language:** the browser plays the first audio track it can decode, so on dual-audio releases put the Japanese track first.
- **Cards not filled:** only cards added today, whose sentence matches a line already played and whose picture and audio fields are empty, are filled. With your own note type, map its sentence field to `{sentence}` in Yomitan and enter the deck and field names in ani's settings.

### Settings outside the app

Set these as environment variables for Anki if the defaults don't suit:

| Variable | Default | Purpose |
| --- | --- | --- |
| `ANI_PORT` | `6969` | Port the player is served on |
| `ANI_HOSTS` | none | Extra `host:port` names the player answers to, comma-separated (see below) |
| `ANI_SEARCH` | Downloads, Desktop, Movies, Videos | Folders searched for files dropped onto a series (`:`-separated, `;` on Windows) |
| `ANKICONNECT` | `http://127.0.0.1:8765` | AnkiConnect URL |

### Watching from another device

ani only listens on this computer and refuses requests addressed to any other name. To watch on another of your devices, forward a private address to the player and list that address in `ANI_HOSTS`. With Tailscale, for example:

```sh
tailscale serve --bg --https=8443 http://127.0.0.1:6969
ANI_HOSTS=yourmachine.your-tailnet.ts.net:8443 anki
```

Then open `https://yourmachine.your-tailnet.ts.net:8443` on the other device. Anyone who can reach that address can control the player, so only use one that is private to you. Cards are still filled through AnkiConnect on the computer running ani, so they are only filled when they reach that computer's Anki.

## Development

- `python3 test_ani.py` runs the tests.
- `addon/build.sh` makes `dist/ani.ankiaddon` (install from file) and `dist/ani-ankiweb.zip` (upload to AnkiWeb). Each names the other as a conflict so only one copy runs.
- `addon/tools.sh` rebuilds the tool bundles the add-on downloads, from pinned sources. Publish a rebuild under a new release tag (`tools-2`, ...) and update `RELEASE` and the checksums in `addon/__init__.py`; never re-upload to a published tag.

## Licence

GPL-3.0-or-later. See [LICENSE](LICENSE).
