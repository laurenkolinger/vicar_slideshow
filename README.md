# vicar_slideshow

VICAR Lab Showcase -- the slideshow + screensaver bundle (a VICARIUS module).
Code lives in [`slideshow/`](slideshow/): `slideshow.html`, `screensaver.html`,
the `.command` launchers, captions, and the `_manifest.json` that drives
playback. `module.yaml` advertises it to the VICARIUS UI.

## Media is not in this repo

The ~980 MB of slides and clips in `slideshow/slideshow_media/` (and the
generated `slideshow/_thumbs/`) are **gitignored** and distributed out-of-band
-- several files exceed GitHub's 100 MB limit. A `pre-commit` hook
(see [`.githooks/`](.githooks/)) blocks them from being committed by accident.
Drop the media into `slideshow/slideshow_media/` and regenerate thumbnails to
run the bundle locally.

## After cloning

```bash
git config core.hooksPath .githooks   # enable the media guard
```
