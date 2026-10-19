# Git hooks

Versioned hooks for this repo. They are activated via `core.hooksPath`, which
is a *local* setting and does not travel with a clone. After cloning, run:

```bash
git config core.hooksPath .githooks
```

## pre-commit

Keeps content out of git. Slides and captions live in the content folder that
`settings.json` names, never in the repository. The hook refuses to commit:

- anything under `slideshow/slideshow_media/`, `slideshow/_thumbs/` or `.cache/`
- any `settings.local.json` (one computer's own setting)
- any image or video file over 2 MB
- any file over 95 MB (GitHub rejects files over 100 MB)

Those paths are already in `.gitignore`; the hook is the backstop against
`git add -f`.

## pre-push

Logs the push to the VICARIUS changelog when the repository sits inside a
VICARIUS checkout. It never blocks a push, and outside VICARIUS it only prints
a one-line notice.
