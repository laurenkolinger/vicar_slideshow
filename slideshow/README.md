# VICAR Lab Slideshow

The whole slideshow lives in this one folder. Nothing else is required to run it.

## Launch it

Double-click **`start-slideshow.command`**. It opens a Terminal window, starts a tiny local web server, and pops the slideshow up in your default browser at `http://localhost:8765/slideshow.html`. Press `Ctrl+C` in that Terminal window when you're done, or just close it.

If macOS Gatekeeper grumbles the first time (*"cannot be opened because it is from an unidentified developer"*), right-click `start-slideshow.command`, choose **Open**, then click **Open** again in the dialog. After that, double-click works normally.

## Keyboard shortcuts

| Key            | What it does                                   |
| -------------- | ---------------------------------------------- |
| `Space`        | Toggle between fullscreen slideshow and grid   |
| `Escape`       | Same — toggle between grid and slideshow       |
| `→`            | Next slide (in slideshow mode)                 |
| `←`            | Previous slide (in slideshow mode)             |
| `Click`        | Click anywhere on a slide to advance           |
| `F`            | Toggle fullscreen browser window               |
| Click a cell   | In grid mode, jump to that slide and play      |

There are also three speed buttons in the bottom bar (`3s` `6s` `10s`) and `prev` / `next` / `grid` / `fullscreen` buttons.

## Edit slides

To **reorder** a slide, rename its file in `slideshow_media/`. The numeric prefix (`01_`, `02_`, ...) sets the order. The slideshow re-sorts every time you relaunch.

To **change a caption**, open `captions.csv` in any spreadsheet or text editor. Captions are matched by the `title` column, which is the filename minus the number prefix and extension. Renumbering files does not break captions.

To **add a slide**, drop a file named `NN_my-title.ext` into `slideshow_media/` and add a row `my-title,"My caption",VICAR Lab,Chapter Name` to `captions.csv`, then relaunch.

Supported file types: `.mp4` `.mov` `.webm` `.m4v` `.jpg` `.jpeg` `.png` `.gif`.

## Move it to another Mac

The slideshow folder is fully self-contained. You can move it any way you like:

- **Easiest — AirDrop**: drag the whole `slideshow` folder onto the other Mac via AirDrop. Drop it on the Desktop (or wherever). Double-click `start-slideshow.command`. Done.
- **USB drive**: copy the folder to a USB stick, drag onto the new Mac, double-click the launcher.
- **One-zip bundle**: double-click `pack-for-portable.command` in this folder. It puts a single `vicar-slideshow.zip` on your Desktop, ready to AirDrop / iMessage / drag-to-USB. On the destination Mac: unzip → double-click `start-slideshow.command` inside.

The only requirements on the destination Mac are Python 3 (every modern macOS has it) and a browser. No installs.

### If the destination Mac complains the launcher won't open

Same Gatekeeper workaround as above: right-click `start-slideshow.command`, choose **Open**, click **Open**. Once.

## Troubleshooting

- **Browser opens but no media plays.** Confirm you launched via `start-slideshow.command`, not by double-clicking `slideshow.html` directly. Chrome blocks subfolder media when the page is loaded from `file://`. The launcher serves over HTTP, which fixes it.
- **A video shows up blurry.** It's either a low-resolution source or got grainy when the GIF was converted. Drop a higher-quality MP4 with the same filename into `slideshow_media/` to replace it.
- **Grid cell has no preview.** Video thumbnails live in `_thumbs/`. The launcher auto-generates any missing thumbs on each run, as long as `ffmpeg` is installed. Without `ffmpeg`, you can still use the slideshow but new videos will show blank cells.

## What's in this folder

```
slideshow/
├── start-slideshow.command     Double-click this to launch
├── pack-for-portable.command   Double-click this to make a portable zip
├── slideshow.html              The slideshow page
├── captions.csv                Captions, one row per slide title
├── _manifest.json              Auto-generated file list (don't edit)
├── slideshow_media/            All the videos and images, NN_title.ext
├── _thumbs/                    Auto-generated grid thumbnails
└── _dropped/                   Files removed during edits (safe to delete)
```
