# VICAR Lab Slideshow (standalone bundle)

This folder holds the static VICAR Lab showcase: `slideshow.html`,
`screensaver.html`, `captions.csv`, the auto-generated `_manifest.json`, the
media in `slideshow_media/`, video thumbnails in `_thumbs/`, and the macOS
`.command` launchers.

The primary way to use this is through VICARIUS, the Flask UI on port 5077,
which pops these pages out full-screen and provides an add-media uploader (see
the [module README](../README.md) and `module.yaml`, `ui.style: showcase`). This
file documents the **optional standalone path**: running the bundle off a single
Mac with no VICARIUS, by double-clicking the `.command` launchers.

## Launch standalone (macOS)

- **Slideshow**: double-click `start-slideshow.command`. It opens a Terminal
  window, starts a tiny local web server, and pops the slideshow up in your
  default browser at `http://localhost:8765/slideshow.html`. Press `Ctrl+C` in
  that Terminal window when done, or just close it.
- **Screensaver**: double-click `start-screensaver.command`. It serves on port
  8766 and opens `screensaver.html` full-screen in a Chrome / Chromium kiosk
  window (falling back to Firefox or the default browser).

If macOS Gatekeeper grumbles the first time (*"cannot be opened because it is
from an unidentified developer"*), right-click the `.command` file, choose
**Open**, then click **Open** in the dialog. After that, double-click works
normally.

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

- **Reorder**: rename a file in `slideshow_media/`; the `NN_` numeric prefix sets
  order. The reel re-sorts on each launch.
- **Caption**: edit `captions.csv` (header `title,caption,credit,chapter`).
  Captions match on `title`, the filename minus the number prefix and extension,
  so renumbering does not break them.
- **Add**: drop a `NN_my-title.ext` file into `slideshow_media/`, add a row
  `my-title,"My caption",VICAR Lab,Chapter Name` to `captions.csv`, then
  relaunch. (From VICARIUS, the ADD MEDIA form does all of this for you.)

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
├── start-slideshow.command     Double-click to launch the slideshow (port 8765)
├── start-screensaver.command   Double-click to launch the screensaver kiosk (port 8766)
├── pack-for-portable.command   Double-click to make a portable vicar-slideshow.zip
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
