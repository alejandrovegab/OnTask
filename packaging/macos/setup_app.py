"""Build a real OnTask.app bundle with py2app.

    pip install py2app
    python packaging/macos/setup_app.py py2app

A bundle is worth building once you use OnTask daily: notifications need a
bundled app, the permission prompts name "OnTask" instead of your terminal, and
the grants survive Python upgrades.
"""

from pathlib import Path

from setuptools import setup

ROOT = Path(__file__).resolve().parents[2]

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
                "CFBundleIdentifier": "com.ontask.app",
                "CFBundleVersion": "1.0.0",
                "CFBundleShortVersionString": "1.0.0",
                # Menu bar only: no Dock icon, no app switcher entry.
                "LSUIElement": True,
                "NSAppleEventsUsageDescription": (
                    "OnTask reads the address of the active browser tab so it can tell "
                    "whether the site you are on is on your approved list."
                ),
            },
        }
    },
    setup_requires=["py2app"],
)
