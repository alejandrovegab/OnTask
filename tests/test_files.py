"""OnTask's own files are private, written all-or-nothing, and size-capped."""

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ontask import ipc
from ontask.core import files
from ontask.core.config import MAX_CONFIG_BYTES, Config
from ontask.stats import Stats

POSIX = os.name != "nt"


def mode(path: Path) -> int:
    return stat.S_IMODE(Path(path).stat().st_mode)


class PrivateFilesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    @unittest.skipUnless(POSIX, "POSIX permissions")
    def test_everything_ontask_writes_is_owner_only(self):
        folder = self.root / "OnTask"
        Config().save(folder / "config.json")
        stats = Stats(path=folder / "stats.json")
        stats.record_session(60, "Deep Work")
        stats.save()
        ipc.Signal(folder, ipc.OPEN_SETTINGS).send()
        self.assertEqual(mode(folder), 0o700)
        for name in ("config.json", "stats.json", ipc.OPEN_SETTINGS):
            self.assertEqual(mode(folder / name), 0o600, name)

    @unittest.skipUnless(POSIX, "POSIX permissions")
    def test_a_file_saved_with_loose_permissions_becomes_private(self):
        # Files written by older versions were 0644.
        path = self.root / "config.json"
        path.write_text("{}")
        os.chmod(path, 0o644)
        Config().save(path)
        self.assertEqual(mode(path), 0o600)

    @unittest.skipUnless(POSIX, "POSIX permissions")
    def test_files_left_loose_by_older_versions_are_tightened_on_use(self):
        # Reported from a real install: the lock and stats.json kept 0644
        # because they had not been rewritten since the upgrade.
        Config().save(self.root / "config.json")
        Stats(path=self.root / "stats.json").save()
        (self.root / ipc.LOCK_NAME).write_text("123")
        for name in ("config.json", "stats.json", ipc.LOCK_NAME):
            os.chmod(self.root / name, 0o644)
        Config.load(self.root / "config.json")
        Stats.load(self.root / "stats.json")
        lock = ipc.Lock(self.root)
        self.addCleanup(lock.release)
        lock.acquire()
        for name in ("config.json", "stats.json", ipc.LOCK_NAME):
            self.assertEqual(mode(self.root / name), 0o600, name)

    @unittest.skipUnless(POSIX, "POSIX permissions")
    def test_a_folder_the_user_chose_is_left_as_it_is(self):
        chosen = self.root / "Documents"
        chosen.mkdir()
        os.chmod(chosen, 0o755)
        Config().save(chosen / "config.json")
        self.assertEqual(mode(chosen), 0o755)

    @unittest.skipUnless(POSIX, "POSIX permissions")
    def test_ontasks_own_folder_is_tightened_on_load(self):
        own = self.root / "OnTask"
        own.mkdir()
        os.chmod(own, 0o755)
        with mock.patch("ontask.core.config.default_config_path", return_value=own / "config.json"):
            Config.load()
        self.assertEqual(mode(own), 0o700)

    def test_a_failed_write_leaves_the_old_file_and_no_debris(self):
        path = self.root / "config.json"
        path.write_text("old")
        with mock.patch("ontask.core.files.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                files.write_private(path, "new")
        self.assertEqual(path.read_text(), "old")
        self.assertEqual([p.name for p in self.root.iterdir()], ["config.json"])

    def test_an_oversized_config_is_set_aside_not_read(self):
        path = self.root / "config.json"
        path.write_text(json.dumps({"padding": "x" * (MAX_CONFIG_BYTES + 1)}))
        cfg = Config.load(path)
        self.assertEqual(cfg.reminder.distraction_grace_seconds, 60)  # the defaults
        self.assertTrue(path.with_suffix(".json.bad").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
