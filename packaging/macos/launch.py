"""The bundle's entry point.

py2app runs its entry point as a plain script, where ontask/__main__.py's
relative imports have no package to be relative to; importing the package
here gives them one.
"""

from ontask.__main__ import main

raise SystemExit(main())
