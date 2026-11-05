#!/usr/bin/env python3
"""Launch the Eve Online Planetary Production planner (desktop GUI).

    python main.py

On first run, click "Download / Update SDE" to fetch Eve's static data.
"""

from eve_pi.gui import run

if __name__ == "__main__":
    run()
