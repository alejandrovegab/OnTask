"""py2app configuration for OnTask.app.

Run through scripts/build-mac-app.sh rather than directly: the script also
replaces py2app's launcher with one built against this Mac's SDK and signs the
bundle, neither of which py2app does.
"""

import tomllib
from pathlib import Path

from setuptools import setup

from ontask import BUNDLE_ID

ROOT = Path(__file__).resolve().parents[2]
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]

setup(
    app=[str(Path(__file__).with_name("launch.py"))],
    name="OnTask",
    data_files=[],
    options={
        "py2app": {
            "argv_emulation": False,
            "packages": ["ontask", "rumps", "pynput", "UserNotifications"],
            "plist": {
                "CFBundleName": "OnTask",
                "CFBundleDisplayName": "OnTask",
                "CFBundleIdentifier": BUNDLE_ID,
                "CFBundleVersion": VERSION,
                "CFBundleShortVersionString": VERSION,
                "LSMinimumSystemVersion": "15.0",
                # Menu bar only: no Dock icon, no app switcher entry.
                "LSUIElement": True,
                "NSAppleEventsUsageDescription": (
                    "OnTask reads the address of the active browser tab so it can tell "
                    "whether the site you are on is on your approved list."
                ),
            },
        }
    },
)
