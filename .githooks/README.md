# Git hooks

Versioned hooks for this repo. They are activated via `core.hooksPath`, which
is a *local* setting and does not travel with a clone. After cloning, run:

```bash
git config core.hooksPath .githooks
```

## pre-commit

Refuses to commit anything under `slideshow/slideshow_media/` or
`slideshow/_thumbs/`, or any file larger than 95 MB. Those paths are already in
`.gitignore`; the hook is the backstop against `git add -f` and against an
oversized file stranding a commit (GitHub hard-rejects files over 100 MB).
