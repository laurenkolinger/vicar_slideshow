# VICAR Lab Showcase (`vicar_slideshow`)

VICAR Lab Showcase plays a lab's images and videos as a full-screen screensaver and as a manual slideshow with a grid. The code lives in this repository. The slides and their captions live in a separate content folder that many people can add to.

- Tags: MISC | Version: 2.0.0 | Type: resource (viewer) | Owner: Lauren Olinger
- Repo: `vicarius/modules/vicar_slideshow/github_repo` (remote `laurenkolinger/vicar_slideshow`) | Created: 2026-05-21 | Related studies: none

## Quick start on a new computer

You need Python 3.10 or newer. Chrome or Chromium is recommended for playback. `ffmpeg` is optional and only makes preview pictures for videos. There are no packages to install: the tool uses the Python standard library only.

1. Clone or fork this repository and open a terminal in its top folder.
2. Try it with the example slides first:
   ```bash
   python3 showcase.py screensaver --content example_content
   ```
3. Point the tool at your own content folder (next section), then run:
   ```bash
   python3 showcase.py check          # lists the slides it found and anything it skipped
   python3 showcase.py screensaver    # full-screen auto-scrolling reel
   python3 showcase.py slideshow      # manual slideshow with a grid
   ```

On macOS, double-click `slideshow/start-slideshow.command` or `slideshow/start-screensaver.command` instead. On the Linux lab display, the dock launcher runs `slideshow/start-screensaver.command`. Both launchers call `showcase.py`, so they follow the same settings. On macOS the first double-click may need right-click, Open, Open (Gatekeeper).

### Point the tool at your content folder

The content folder is a plain folder of images, videos and one `captions.csv`. The default is the Dropbox folder `vicar_screensaver_19october26` that `settings.json` names. Change it in one of four ways. The first one set wins, from top to bottom:

| Order | Where | Use it for |
|---|---|---|
| 1 | `--content <folder>` on any command | One run |
| 2 | Environment variable `VICAR_SHOWCASE_CONTENT` | One shell or one launcher |
| 3 | `settings.local.json` in the repository folder | One machine. Git never tracks it. |
| 4 | `settings.json` in the repository folder | The default for everyone who uses your fork |

`settings.json` and `settings.local.json` hold the same two keys:

```json
{
  "content_dir": "~/path/to/your/content folder",
  "captions_file": "captions.csv"
}
```

- `content_dir` is the folder with the slides. A leading `~` means your home folder. A relative path is read from the repository folder.
- `captions_file` is the captions spreadsheet inside that folder. It must be a bare file name ending in `.csv`. Leave it as `captions.csv`.
- JSON has no comments, so this section is the documentation. An unknown key stops the tool and lists the known keys, so a typo never falls back silently.
- In a fork, edit `content_dir` in `settings.json` and commit it. On one computer, copy the file to `settings.local.json` and edit the copy.

`python3 showcase.py check` prints which of the four sources it used and the folder it resolved.

## What the screensaver and slideshow are

The **screensaver** scrolls a horizontal strip of the reel without stopping, with the cursor hidden. It is meant for a display left running in a lab or at an event. Any mouse movement, click or key press ends it.

The **slideshow** shows one slide at a time with its caption and credit, or a grid of every slide. It is meant for presenting to visitors.

Both read the same content folder. Each slide carries a title, a caption, a credit and a chapter.

## Requirements

- Python 3.10 or newer. Linux, macOS and Windows all work for `showcase.py`.
- A browser. Chrome or Chromium is recommended. Videos in some formats (for example HEVC `.mov` from an iPhone) do not play in every browser.
- `ffmpeg` on the PATH, optional. Without it the slideshow grid shows no preview for videos. Everything else works.
- Nothing to install with pip.

## The content folder

Anyone who adds to the shared folder follows these rules.

**Supported files.** Images: `.jpg` `.jpeg` `.png` `.gif` `.webp` `.avif`. Videos: `.mp4` `.mov` `.webm` `.m4v`. Put them directly in the folder, not in subfolders.

**Order.** Start a file name with a number and a separator to set its place: `05_reef-survey.mp4` or `05-reef-survey.mp4`. Slides play in number order, counting the number as an integer (`2_` before `10_`). Files without a number play after the numbered ones, in alphabetical order.

**Never shown.** The tool skips these and `check` reports each one with its reason:

- subfolders and their contents
- unsupported file types, for example iPhone `.heic` photos (export them as `.jpg` first)
- Dropbox conflicted copies (a name containing `conflicted copy`): open both versions, keep the right one, delete the other
- empty files (usually still syncing)
- links that point outside the folder
- any `.csv` other than the captions file

**Ignored without a report.** File names that start with `.` or `_` (hidden files, the `_superseded` folder, the copy log), `Thumbs.db`, `desktop.ini`.

**Captions.** Keep exactly one `captions.csv` in the folder, with this header:

```
title,caption,credit,chapter
```

`title` is the file name without its number prefix and without its extension. The file `03_grouper-spawning.mp4` is matched by the title `grouper-spawning`, so renumbering a file never breaks its caption. Example row:

```
grouper-spawning,"Grouper gather in large groups to spawn south of St. Thomas.",VI-EPSCoR,USVI Reefs
```

- A title matches exactly first, then ignoring upper and lower case.
- A file with no row still plays, with no caption. A row with no file plays nothing.
- If a title appears twice, the first row wins and `check` warns.
- Save from Excel or Numbers as CSV UTF-8. Windows-1252 is accepted with a warning. A cell may contain line breaks.
- Do not keep two captions files. A Dropbox conflicted copy of `captions.csv` is reported by `check`.

`example_content/` in this repository shows the layout: two numbered images with caption rows and one unnumbered image.

## Copy content into a new folder

`copy-content` fills a content folder from an existing folder of slides and a captions file. It is a safe copy:

- It checks every file against the rules above, and checks free space before it starts.
- It verifies each copy with a SHA-256 checksum.
- It never overwrites a different file. If any destination file already exists with different content, the whole run is refused (exit code 3) and nothing is copied.
- `--replace` allows replacing. The old file moves to `_superseded/<YYYYMMDD-HHMMSS>/` inside the destination. Nothing is deleted.
- `--dry-run` shows what would happen and writes nothing.
- Every action is appended to `_showcase_copy_log.csv` in the destination.

The destination is the content folder from your settings, or the existing folder given with `--to <folder>`. The first copy from this lab workstation's old local folder, run from the repository's top folder:

```bash
python3 showcase.py copy-content --from slideshow/slideshow_media --captions slideshow/captions.csv
```

Add `--dry-run` first to see the plan.

## Controls

**Screensaver.** After a 3.5 second start-up pause, moving the mouse more than a small distance, clicking, or pressing a key closes it. Exceptions: Up arrow, `+` or `=` brighten in 5 percent steps; Down arrow, `-` or `_` dim in 5 percent steps (never below 5 percent, so the screen is never fully black).

**Slideshow.**

| Key or action | What it does |
|---|---|
| `Space` | Switch between the slideshow and the grid |
| `Escape` | Switch between the grid and the slideshow |
| Right arrow | Next slide (slideshow mode) |
| Left arrow | Previous slide (slideshow mode) |
| Click on a slide | Next slide |
| Click on a grid cell | Jump to that slide and play |
| `F` | Browser full screen on or off |

A bottom bar offers speed buttons (3s, 6s, 10s) plus previous, next, grid and full screen.

## Commands

Run every command from the repository's top folder as `python3 showcase.py <command>`.

| Command | What it does |
|---|---|
| `screensaver [--port <port>]` | Starts the screensaver server on port 8766 (or reuses it) and opens the page full screen in Chrome or Chromium; closing the page stops the server |
| `slideshow [--port <port>]` | Starts the slideshow server on port 8765 (or reuses it) and opens the page in the default browser; Ctrl+C stops the server |
| `serve --view <screensaver\|slideshow> --port <port>` | Runs one view's server in the foreground without opening a browser. VICARIUS starts views this way. |
| `check [--json]` | Reads the content folder and prints the slides, skipped files and warnings. `--json` prints the report for programs. |
| `copy-content --from <folder> [--to <folder>] [--captions <file>] [--replace] [--dry-run]` | Copies slides and captions into the content folder, as described above |
| `make-thumbs` | Makes preview pictures for videos with `ffmpeg`, in `.cache/thumbs/` |

`--content <folder>` works on any command and overrides every setting.

When a view's port is already in use, the launcher asks the program on it. If it is this tool serving the same content folder, the launcher reuses it. If it is this tool serving another folder, or any other program, the launcher stops with a message naming the port and what to do.

**Exit codes.**

| Code | Meaning |
|---|---|
| 0 | Done |
| 1 | Settings or usage error (for example an unknown settings key or a bad option) |
| 2 | The content folder does not exist or cannot be read (for `copy-content`: the `--from` or `--to` folder) |
| 3 | Copy refused: conflicting files already exist, not enough free space, or one folder is inside the other |
| 4 | Copy failed (see `_showcase_copy_log.csv`) |

## How it works inside

The tool is the Python package `vicar_showcase/` plus the entry script `showcase.py`. It serves the two pages in `slideshow/` on 127.0.0.1 only. Each request to `/_manifest.json` rescans the content folder, so a file you add shows up on the next page load, with no manifest file written to disk. Media is served from the content folder with range requests, so videos seek. Anything outside the folder, and any directory listing, answers 404.

Preview pictures for videos are cached in `.cache/thumbs/` (git ignores it) and keyed by the video's path, size and modification time.

The repository holds code, two pages, settings and a few small example images. A `pre-commit` hook in `.githooks/` refuses content: anything under the media, thumbnail or cache folders, `settings.local.json`, any file over 95 MB, and any image or video over 2 MB. Turn it on after cloning with `git config core.hooksPath .githooks`.

## Inside VICARIUS

For the lab operator. Open the module card (`/modules/vicar_slideshow`) and use the VIEW buttons.

- **Open Screensaver** and **Open Slideshow** start the module's own server for that view (port 8766 and port 8765) with `showcase.py serve`, then open the page in a pop-out window. Each view keeps playing when VICARIUS quits or restarts. Stop server on the card ends it.
- The card finds the content folder by running `showcase.py check --json`, declared as `ui.content_cmd` in [`module.yaml`](module.yaml). It shows the reel's thumbnails from there and displays the folder's name only.
- **Add media** writes the uploaded files into the content folder and appends their caption rows to its `captions.csv`. Staged files get a caption prefilled from the file name, editable in place, with an optional Clean up button that asks the lab's local language model to tidy the wording. Chapter and credit apply to the whole batch.
- The card has no control to remove a slide, reorder the reel, or edit an existing caption. Rename a file in the content folder to reorder it, edit its row in `captions.csv` to change its caption, and move it out of the folder to remove it.
- The dock launcher (`slideshow/start-screensaver.command`) and the card use port 8766 and share one server when both point at the same content folder.
- To change which folder VICARIUS plays, edit `settings.local.json` on that machine and restart the VICARIUS desktop.

The module declares `inputs: []` and `outputs: []`: it catalogues no datasets, so it has no data-dictionary entry or EML file. It sits outside the survey processing chain and no other module reads from it. Its lock, a per-module switch in the VICARIUS admin panel, blocks only the module page.

## Troubleshooting

- **Port already in use.** Another program holds 8765 or 8766. Close it, or run the other tool's view on a different port with `serve --view <view> --port <port>`. If the message says this tool is serving a different content folder, stop that server (Stop server on the VICARIUS card, or close its terminal) and start again.
- **Content folder not found (exit code 2).** The folder named by the winning setting does not exist on this computer. Run `python3 showcase.py check` to see the folder and which source named it, then fix the setting or create the folder. A Dropbox folder that has not synced yet looks the same.
- **A slide is missing.** Run `python3 showcase.py check`. The skipped list gives a reason for each file: wrong type, conflicted copy, still syncing, or a subfolder.
- **A video does not play.** Chrome and Chromium cannot decode some codecs, for example HEVC `.mov` from an iPhone. Re-export the video as H.264 `.mp4`.
- **The grid shows no video previews.** Install `ffmpeg`, then run `python3 showcase.py make-thumbs`.
- **Pages show no media when opened as a file.** Browsers block media on `file://` pages. Start a view with the commands above, which serve over `http://127.0.0.1`.
- **Captions look scrambled.** Re-save the spreadsheet as CSV UTF-8.

## Old local copy on the lab workstation

Before the content folder moved out of the repository, the reel lived in `slideshow/slideshow_media/` with its captions in `slideshow/captions.csv`, `slideshow/captions.numbers` and `slideshow/_manifest.json`. Those files stay on disk, untracked, as local leftovers. The tool does not read them, and nothing deletes them. The `copy-content` command above is the way to move them into the content folder. Anyone who kept their own media in `slideshow_media/` should do the same, which is why this release is 2.0.0.

## Provenance and links

- Module contract: [`module.yaml`](module.yaml)
- Settings: [`settings.json`](settings.json)
- Example slides: [`example_content/`](example_content/)
- Git hooks: [`.githooks/README.md`](.githooks/README.md)
- Data-dictionary descriptors: none.

Author: Lauren Olinger
