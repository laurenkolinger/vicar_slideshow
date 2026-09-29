# VICAR Lab Slideshow (standalone bundle)

This folder holds the static VICAR Lab showcase: `slideshow.html`,
`screensaver.html`, `captions.csv`, the auto-generated `_manifest.json`, the
media in `slideshow_media/`, video thumbnails in `_thumbs/`, and the
`.command` launchers (two for macOS, one for Linux; see Launch standalone
below).

The primary way to use this is through VICARIUS, the Flask UI on port 5090,
which pops these pages out full-screen and provides an add-media uploader (see
the [module README](../README.md) and `module.yaml`, `ui.style: showcase`). This
file documents the **optional standalone path**: running the bundle with no
VICARIUS, by launching the `.command` scripts directly.

## Launch standalone

The two launchers open different views, the slideshow and the screensaver,
and were written with different platforms in mind, though `start-slideshow.command`
also runs on the Ubuntu lab workstation that serves VICARIUS.

- **Slideshow**: double-click `start-slideshow.command`. It opens a Terminal
  window, starts a tiny local web server, and backgrounds a call to `open` to
  pop the slideshow up in your default browser at
  `http://localhost:8765/slideshow.html`. macOS provides `open` natively. On
  the Ubuntu lab workstation that serves VICARIUS, `open` also works: it is a
  symlink Ubuntu's `update-alternatives` system points at `/usr/bin/xdg-open`,
  so the same command opens the default browser there too. Press `Ctrl+C` in
  that Terminal window when done, or just close it. On macOS, if Gatekeeper
  grumbles the first time (*"cannot be opened because it is from an
  unidentified developer"*), right-click the file, choose **Open**, then click
  **Open** in the dialog. After that, double-click works normally.
- **Screensaver**: run `bash start-screensaver.command`. It is written for the
  Ubuntu box that runs the lab display: it calls `gsettings` to hide the dock
  and `wmctrl` to force fullscreen, each one guarded so a missing tool is
  skipped rather than failing the script. It checks with `ss` whether a server
  already listens on port 8766, reusing that server if so and starting one if
  not, then opens `screensaver.html` full-screen in a Chrome or Chromium kiosk window,
  falling back to Firefox. On a stock Mac it does not run: with no
  `google-chrome`, `chromium`, `chromium-browser`, or `firefox` on the PATH,
  the script falls through to `xdg-open`, which macOS does not carry, and it
  exits under `set -e`, taking the server it just started down with it. It
  also un-pins the Ubuntu dock for the run and restores its prior setting on
  exit.

## Controls

Slideshow (`slideshow.html`):

| Key / action  | What it does                                   |
| ------------- | ---------------------------------------------- |
| `Space`       | Toggle between full-screen slideshow and grid  |
| `Escape`      | Same, toggle between grid and slideshow        |
| `→`           | Next slide (in slideshow mode)                 |
| `←`           | Previous slide (in slideshow mode)             |
| `Click`       | Click a slide to advance                       |
| `F`           | Toggle full-screen browser window              |
| Click a cell  | In grid mode, jump to that slide and play      |

A bottom bar adds three speed buttons (`3s` `6s` `10s`) plus
`prev` / `next` / `grid` / `fullscreen`.

Screensaver (`screensaver.html`): a continuously auto-scrolling horizontal strip
of the reel. Press `↑` / `↓` (or `+` / `-`) to brighten / dim in 5% steps (never
below 5%, so it never goes fully black). It exits on any mouse movement past a
small threshold, any click, or any key, but only after a short startup grace
period (so a static cursor at launch does not immediately quit it).

## Edit slides

VICARIUS's ADD MEDIA form (see the [module README](../README.md)) only adds
slides. Removing one, reordering the reel, or changing a caption on a slide
already added takes a direct edit of the files below; none of the three has a
control in the VICARIUS UI. A caption edit shows in Open Slideshow the next
time it opens, with no manifest step needed. A reorder or a removal needs
`_manifest.json` regenerated first: an ADD MEDIA upload, Open Screensaver
(which refreshes the manifest before it serves), or one of the standalone
launchers below.

- **Reorder**: rename a file in `slideshow_media/`; the `NN_` numeric prefix
  sets order. The reel re-sorts the next time something regenerates the
  manifest (see above); until then it keeps the old order.
- **Caption**: edit `captions.csv` (header `title,caption,credit,chapter`).
  Captions match on `title`, the filename minus the number prefix and
  extension, so renumbering does not break them. A caption edit needs no
  manifest regeneration; the viewer reads `captions.csv` fresh on every page
  load.
- **Remove**: delete the file from `slideshow_media/`. Its row in
  `captions.csv` can stay; a caption row with no matching file plays no slide.
  Until something regenerates the manifest (see above), VICARIUS's Open
  Slideshow view still lists the removed file and its slide returns a 404
  when played.
- **Add**: drop a `NN_my-title.ext` file into `slideshow_media/`, add a row
  `my-title,"My caption",VICAR Lab,Chapter Name` to `captions.csv`, then
  regenerate the manifest (see above). (From VICARIUS, the ADD MEDIA form does
  all of this for you.)

Supported types: `.mp4` `.mov` `.webm` `.m4v` `.jpg` `.jpeg` `.png` `.gif`.

## Move it to another Mac

The folder is fully self-contained:

- **AirDrop**: drag the whole `slideshow` folder onto the other Mac, drop it
  anywhere, double-click `start-slideshow.command`.
- **USB drive**: copy the folder to a stick, drag onto the new Mac, double-click
  the launcher.
- **One-zip bundle**: double-click `pack-for-portable.command`. It puts a single
  `vicar-slideshow.zip` on your Desktop, ready to AirDrop / iMessage / drag to
  USB. On the destination: unzip, then double-click `start-slideshow.command`.

The only requirements on the destination are Python 3 (present on every modern
macOS) and a browser. No installs. Same Gatekeeper right-click-Open workaround
applies the first time.

## Troubleshooting

- **Browser opens but no media plays.** Launch via `start-slideshow.command`,
  not by double-clicking `slideshow.html` directly. Browsers block subfolder
  media on `file://` pages; the launcher serves over HTTP, which fixes it.
- **A video looks blurry.** It is a low-resolution source. Replace it with a
  higher-quality file of the same name in `slideshow_media/`.
- **Grid cell has no preview.** Video thumbnails live in `_thumbs/` and are
  regenerated on each launch when `ffmpeg` is installed. Without `ffmpeg`, the
  slideshow still works but new videos show blank cells.

## What is in this folder

```
slideshow/
├── start-slideshow.command     Double-click (macOS) or run (Linux) to launch the slideshow (port 8765)
├── start-screensaver.command   Linux: run to launch the screensaver kiosk (port 8766)
├── pack-for-portable.command   macOS: double-click to make a portable vicar-slideshow.zip
├── slideshow.html              The slideshow page
├── screensaver.html            The auto-scrolling screensaver page
├── captions.csv                Captions, one row per slide title
├── _manifest.json              Auto-generated ordered file list (do not edit)
├── slideshow_media/            All videos and images, NN_title.ext (gitignored)
└── _thumbs/                    Auto-generated grid thumbnails (gitignored)
```

`slideshow_media/` and `_thumbs/` are gitignored and ship out-of-band; see the
[module README](../README.md) for media and git hygiene.

---
Author: Lauren K Olinger
