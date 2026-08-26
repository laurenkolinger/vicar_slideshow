# VICAR Lab Showcase (`vicar_slideshow`)

VICAR Lab Showcase opens a full-screen slideshow and screensaver of lab imagery and video inside VICARIUS, and lets an operator add media to the reel from the UI. It is a presentation resource for anyone showing the lab's work.

- Tags: MISC | Version: 1.1.0 | Type: resource (viewer) | Owner: Lauren Olinger
- Repo: `vicarius/modules/vicar_slideshow/github_repo` | Created: 2026-05-21 | Related studies: none

## What it does and why it exists

The showcase gives the VICAR Lab a single place to play selected imagery and video: a manual slideshow with a grid view, and an auto-scrolling screensaver for a display left running in the lab. Both play the same curated reel of AUV footage, reef surveys, bleaching sequences, and team photos, each slide carrying a caption and a credit.

The module fills a presentation need. When someone wants to show visitors what the lab does, or leave a screen running during an event, they open one card in VICARIUS and click a button. That single click covers the task, with no deck to assemble, no files to hunt for, and no script to run.

`module.yaml` declares `type: resource` and sets `ui.style: showcase`. That flag tells the VICARIUS UI to render the showcase page in place of the generic input/output runner form. The module carries `tags: MISC` and declares no data dictionary, because it produces no analysis-bound datasets. It has no runner, no CLI, no EML (Ecological Metadata Language) file, and no sidecar contract.

Anyone on the team runs it, at any time. The typical operator is whoever is presenting the lab's work or setting up a display, and the typical action is a single click.

## Where it sits in the pipeline

The showcase sits outside the survey processing chain. It does not consume outputs from `bag_metashape_export`, `3D_phase1`, `3D_phase2`, or `reef_point_seg`, and it produces nothing those modules read. It is a viewer that stands on its own.

The only inputs are the media files an operator adds to the reel, and the only downstream consumer is a person watching the screen. Because it is tagged MISC and typed `resource`, the module browser lists it among the utilities.

## Inputs

`module.yaml` declares `inputs: []`. The showcase is a viewer, so it defines no pipeline-contract inputs and reads no upstream dataset.

The content it plays is a reel of media files that live in the static bundle of the module at `slideshow/slideshow_media/`. Each file is named `NN_title.ext`, where the leading number sets play order. The viewer accepts these types: `.mp4`, `.mov`, `.webm`, `.m4v`, `.jpg`, `.jpeg`, `.png`, and `.gif`. The showcase skips files whose names begin with a dot or an underscore, so a working file like `_tmp.mp4` never appears in the reel.

Git does not carry the reel. `slideshow/slideshow_media/` is gitignored, a `pre-commit` hook rejects anything under it, and several files exceed the 100 MB per-file limit on GitHub, so a clone arrives with the folder empty. The one authoritative copy of the curated reel is the working tree on the machine that already runs VICARIUS. The section Resume from cold gives the two ways to restore it.

## Parameters

`module.yaml` declares no runtime parameters, because the module has no runner. The only user-adjustable fields are on the ADD MEDIA form of the showcase page. Each field carries the tooltip a user sees in the UI.

| Field | Type | Default | What it controls |
|-------|------|---------|------------------|
| Media file(s) | file (one or more) | none | Pick one or more images or videos to add to the reel (mp4, mov, webm, m4v, jpg, jpeg, png, gif). |
| Caption | text | file name | Optional caption shown under the slide. Leave blank to fall back to the file name. Applies to every file selected above. |
| Chapter / section | text | empty | Optional grouping label shown with the slide. Applies to every file selected above. |
| Credit | text | `VICAR Lab` | Optional credit line. Defaults to `VICAR Lab` when left blank. |

## Outputs and data-dictionary entries

This module produces no catalogued datasets. `module.yaml` declares `outputs: []`, and no descriptor (a dataset-descriptor YAML file) in `vicarius/_METADATA/dictionary/datasets/` names `vicar_slideshow` as the producing module. The module has no data-dictionary entry, no EML, and no sidecar, because it plays media and catalogues no datasets.

An upload through the ADD MEDIA form changes files on disk inside the bundle of the module. Each upload saves the media file as `NN_<slug>.<ext>` in `slideshow/slideshow_media/`, appends a row to `slideshow/captions.csv`, rewrites `slideshow/_manifest.json` to the current ordered file list, and writes a video thumbnail to `slideshow/_thumbs/<stem>.jpg` when `ffmpeg` can produce one. The upload also appends one row to the VICARIUS processing log with `module = vicar_slideshow` and `status = media_added`. None of these are dictionary-bound datasets; they are the internal state of the module.

## How to run it

### From the UI

1. Open VICARIUS, the Flask UI on port 5090, from the dock VICARIUS icon or the `vicarius` command.
2. Open the module card. Go to the Modules list and click VICAR Lab Showcase, or navigate to `/modules/vicar_slideshow`. Because `module.yaml` sets `ui.style: showcase`, the UI renders the showcase page instead of the runner form.
3. Under VIEW, click Open Slideshow or Open Screensaver. Each button opens that page in a separate popup window (1280 by 800 pixels, resizable). Inside the slideshow, press `F` for browser full-screen. Open Slideshow serves from the VICARIUS app itself; Open Screensaver first starts (or reuses) the screensaver's own server on port 8766, then opens the popup from that server, so the screensaver runs independent of the VICARIUS process from the first frame.
4. Manage the screensaver server with the status row under the VIEW buttons. The row shows whether the server is running. Start server brings it up without opening a window (useful before walking to the lab display); Stop server kills it. The two lifecycles never touch: a playing screensaver survives any VICARIUS quit or restart, and stopping the screensaver server leaves VICARIUS running on 5090.
5. To add media, use the ADD MEDIA form: choose one or more files, optionally fill Caption, Chapter, and Credit, then click ADD TO SHOWCASE. The status line reports how many files were added and the new reel size, and the CURRENT REEL grid refreshes.

There is no Run-in-terminal or Run-in-page choice and no Pause/Resume here, because the showcase plays media and runs no pipeline step.

### From the CLI

The module has no VICARIUS CLI. `module.yaml` declares no `cli` block and no runner, so there is no headless command to launch it through VICARIUS.

The static bundle can run on its own, without VICARIUS, through the double-click launchers in `slideshow/`. From a shell:

```bash
cd vicarius/modules/vicar_slideshow/github_repo/slideshow
bash start-slideshow.command     # serves http://localhost:8765/slideshow.html, opens the default browser
bash start-screensaver.command   # serves http://localhost:8766/screensaver.html in a Chrome/Chromium kiosk
```

Each launcher first regenerates `_manifest.json` from the current `slideshow_media/` contents, then generates any missing video thumbnails when `ffmpeg` is present, then starts `python3 -m http.server` and opens the browser. `start-slideshow.command` opens the default browser through the macOS `open` command; `start-screensaver.command` targets a Linux desktop (Chrome or Chromium in kiosk mode, with Firefox and `xdg-open` fallbacks). See [`slideshow/README.md`](slideshow/README.md) for the full standalone path, including the portable one-zip bundle. Stop a launcher with `Ctrl+C` in its terminal, or close the window.

The UI's screensaver server and `start-screensaver.command` use the same port (8766), so they share one server: whichever side starts first, the other reuses it, and the UI's Stop server button also stops a server the dock launcher started.

## How it works inside

The showcase has no processing steps. Two pieces do all the work: the VICARIUS UI routes in `module_views.py`, and the two static viewer pages in `slideshow/`.

`ui.style: showcase` routes the module page. In `module_views.py`, `module_detail_view` sees the showcase style and renders `templates/module_showcase.html` rather than the runner form. That page holds three sections (VIEW, ADD MEDIA, CURRENT REEL) and a small script that talks to the endpoints of the module.

Flask serves the bundle for the slideshow view. `GET /modules/vicar_slideshow/showcase/asset/<path>` returns any file from `slideshow/` through `send_from_directory(..., conditional=True)`, so range and conditional requests are honored and videos stream and seek.

The screensaver view is detached from the VICARIUS process. Its `module.yaml` entry carries `detached: true` and `port: 8766`, and three routes manage its standalone server: `GET /api/modules/vicar_slideshow/showcase/detached/screensaver/status` reports `{running, port, url}`; `POST .../start` regenerates the manifest and spawns `python3 -m http.server 8766 --bind 127.0.0.1` rooted at `slideshow/` in its own session (`start_new_session=True`), reusing any server already on the port; `POST .../stop` SIGTERMs every `http.server 8766` process found by exact argv match in `/proc` (SIGKILL for stragglers), and nothing else. Because the server runs in its own session and the stop targets only `http.server` argv matches, the two lifecycles cannot touch each other: VICARIUS quits, restarts, and port sweeps leave the screensaver serving, and Stop server can never kill the UI on 5090 (the endpoint additionally refuses any view port below 1024 or equal to the UI's own port). Start and stop each append a processing-log row (`screensaver_started`, `screensaver_stopped`), and the server's pidfile and log land at `/tmp/vicarius_showcase_vicar_slideshow_screensaver.pid` and `.log`. `GET /api/modules/vicar_slideshow/showcase/media` returns the current reel as JSON (`files`, `count`, `media_dir`). `POST /api/modules/vicar_slideshow/showcase/media` accepts `multipart/form-data` with one or more `file` fields plus optional `caption`, `chapter`, and `credit`. For each upload it derives a title slug (leading `NN_` prefix and extension stripped, lower-cased, non-alphanumerics collapsed to dashes), saves the file as `NN_<slug>.<ext>` at the next free number, writes a video thumbnail when the type is video, appends a captions row, and regenerates the manifest. It then writes a processing-log row and returns `{ok, added, count, total_slides}`.

Playback reads two files. `_manifest.json` is the ordered list of filenames to play, regenerated on every add and on every standalone launch. `captions.csv` has the header `title,caption,credit,chapter`; the viewer matches a slide to a row by `title`, which is the filename minus the `NN_` number prefix and the extension, so renumbering files never breaks captions. Thumbnails come from `ffmpeg` (`-ss 0.5`, one frame, scaled to 480 pixels wide) and land in `slideshow/_thumbs/<stem>.jpg`; a missing thumbnail leaves a blank grid cell and nothing else.

The slideshow page (`slideshow.html`) supports: Space to toggle slideshow and grid, Escape to toggle grid and slideshow, arrow keys for next and previous in slideshow mode, a click on the stage to advance, a click on a grid cell to jump to that slide, and `F` for browser full-screen. A bottom bar offers speed buttons `3s`, `6s`, and `10s` (default `6s`) plus prev, next, grid, and fullscreen. The screensaver page (`screensaver.html`) scrolls a horizontal strip of the reel continuously; ArrowUp, `+`, or `=` brighten in 5 percent steps, ArrowDown, `-`, or `_` dim in 5 percent steps (floored at 5 percent so it never goes fully black), and any mouse movement past a small threshold, click, or key exits after a 3.5 second startup grace period.

No GPU or special hardware is needed. `ffmpeg` on the PATH is optional and only affects video thumbnails. The standalone launchers need Python 3 and a browser.

## Gotchas and troubleshooting

- Opening a viewer page directly over `file://` shows no media. Browsers block subfolder media on `file://` pages. Serve over HTTP: through VICARIUS, or through the `.command` launchers, both of which serve over `http://localhost`.
- New videos show blank grid cells when `ffmpeg` is missing. The slideshow still plays; only the thumbnails are absent. Install `ffmpeg` and re-add or relaunch to fill them in.
- An upload returns `media folder missing` (HTTP 500) when `slideshow/slideshow_media/` does not exist. Create the folder, then add media again.
- An upload returns `unsupported type` (HTTP 400) for any extension outside the allowed set (`.mp4`, `.mov`, `.webm`, `.m4v`, `.jpg`, `.jpeg`, `.png`, `.gif`).
- Stop server stops the shared screensaver server no matter who started it. The UI and the dock launcher share port 8766, so stopping from the UI also stops a server the dock kiosk started; the kiosk window then sits on its last loaded slides until exited.
- The screensaver's standalone server does not honor range requests (`python -m http.server` has no range support), so its videos download progressively instead of seeking. Playback is linear autoplay, so this changes nothing visible, and it matches how the dock launcher has always served the screensaver. The slideshow view keeps full seek support through the VICARIUS asset route.
- Media is gitignored and ships out-of-band. `slideshow/slideshow_media/` (roughly 980 MB, with individual files up to about 271 MB) and the generated `slideshow/_thumbs/` are not committed. Several files exceed the 100 MB per-file ceiling on GitHub, so they cannot be pushed even with `--force`. A `pre-commit` hook in `.githooks/` blocks anything under those directories and any file over 95 MB. After cloning, enable the hook with `git config core.hooksPath .githooks`. Restore the reel afterward by copying `slideshow_media/` from a host that already holds it, or by unpacking the portable `vicar-slideshow.zip` bundle. The section Resume from cold spells out both routes and the exact host path.

## Resume from cold

A person with no prior context can run this module from the two paths below.

Set up the environment. For the VICARIUS path, no extra setup is required: the module is served by the VICARIUS Flask UI on port 5090, which the `vicarius` command or the dock icon starts. `ffmpeg` on the PATH is optional and only affects video thumbnails. For the standalone path, the only requirements are Python 3 and a browser.

Know where everything lives. The static bundle is `vicarius/modules/vicar_slideshow/github_repo/slideshow/`. Inside it: media in `slideshow_media/` (named `NN_title.ext`, gitignored and shipped out-of-band), video thumbnails in `_thumbs/` (gitignored, regenerated on demand), captions in `captions.csv`, and the ordered play list in `_manifest.json`.

Restore the curated reel. A fresh clone arrives with `slideshow_media/` empty, because git never carries the media. Two routes bring the reel back. The route that works on any machine copies the media straight from a host that already has it: the authoritative reel lives in the working tree at `vicarius/modules/vicar_slideshow/github_repo/slideshow/slideshow_media/` on the box that serves VICARIUS, which on the lab workstation is `/mnt/rip/vicarius_drive/vicarius/modules/vicar_slideshow/github_repo/slideshow/slideshow_media/`, so an `rsync`, `scp`, or USB copy of that folder brings the reel over. The second route uses the portable bundle: on a Mac that already holds the folder, double-click `slideshow/pack-for-portable.command`, which writes `vicar-slideshow.zip` (the whole `slideshow/` folder, media included) to the Desktop; carry that zip to the new machine and unzip it in place. No committed copy and no public download exists, so the curated showcase cannot be reproduced without one of these two copies. The module still launches with an empty `slideshow_media/`, and an operator builds a fresh reel through the ADD MEDIA form or by dropping `NN_title.ext` files into the folder.

Run it.

- Through VICARIUS: open `/modules/vicar_slideshow`, then click Open Slideshow or Open Screensaver. The screensaver starts its own server on port 8766 and keeps playing through VICARIUS quits and restarts; stop it with the Stop server button on the same card. Add media with the ADD MEDIA form.
- Standalone: `cd` into `slideshow/` and run `bash start-slideshow.command` (serves `http://localhost:8765/slideshow.html`) or `bash start-screensaver.command` (serves `http://localhost:8766/screensaver.html`).

Verify success.

- In the UI, the CURRENT REEL heading shows a count and the grid shows that many thumbnails. `GET /api/modules/vicar_slideshow/showcase/media` returns `{"files": [...], "count": N, "media_dir": "..."}`, where `N` matches the number of playable files in `slideshow_media/`.
- Opening a view plays the reel. The slideshow advances through the slides; the screensaver scrolls the strip.
- `GET /api/modules/vicar_slideshow/showcase/detached/screensaver/status` returns `{"running": true, "port": 8766, ...}` while the screensaver server is up, and `"running": false` after Stop server. Restarting VICARIUS while the screensaver plays leaves `http://127.0.0.1:8766/screensaver.html` serving; stopping the screensaver leaves the UI on 5090 serving.
- After an upload, the status line confirms the number added and reports the new reel total (`added X`, `reel now N slides`), the grid refreshes, and the processing log gains a row with `status = media_added`. An empty reel shows `reel is empty` instead of thumbnails, which means `slideshow_media/` holds no playable files.

## Provenance and links

- Repo: `vicarius/modules/vicar_slideshow/github_repo`
- Module contract: [`module.yaml`](module.yaml)
- Standalone bundle guide: [`slideshow/README.md`](slideshow/README.md)
- Git hooks: [`.githooks/README.md`](.githooks/README.md)
- Data-dictionary descriptors: none. This module declares `inputs: []` and `outputs: []` and has no catalogued datasets.
- Related modules: none. The showcase stands apart from the survey processing chain.

Author: Lauren Olinger
