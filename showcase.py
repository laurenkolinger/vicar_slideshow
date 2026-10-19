#!/usr/bin/env python3
"""Module: showcase
Purpose: Entry script for the VICAR Lab showcase tool. Refuses old Python versions with
    a clear message, then runs vicar_showcase.cli.main.
Inputs: Command-line arguments (screensaver, slideshow, serve, check, copy-content, make-thumbs).
Outputs: Whatever the chosen command prints; the process exit code.
"""

import sys

MIN_PYTHON = (3, 10)

if sys.version_info < MIN_PYTHON:
    sys.stderr.write(
        "showcase: error: Python %d.%d or newer is required, this is Python %d.%d. "
        "Install a newer Python from python.org and run again.\n"
        % (MIN_PYTHON + sys.version_info[:2])
    )
    sys.exit(1)

from vicar_showcase.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
