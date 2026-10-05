"""Keep the suite away from the real config, whatever the environment says.

Anything that falls back to the default config path - a Controller built
without one, a Stats store with no path - would otherwise read and write the
user's own config.json and stats.json.
"""

import atexit
import os
import shutil
import tempfile

_sandbox = tempfile.mkdtemp(prefix="ontask-tests-")
os.environ["ONTASK_CONFIG"] = os.path.join(_sandbox, "config.json")
atexit.register(shutil.rmtree, _sandbox, ignore_errors=True)
