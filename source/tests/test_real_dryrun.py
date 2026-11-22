"""Dry runs on the real stock and crDroid image folders (read-only), with a fake fastboot that
reports NO phone. Skipped if the folders are not there. Nothing is written to the images."""
import json
import os
import subprocess
import sys
import unittest

from helpers import FAKE_FASTBOOT, NOTHING, TOOL, make_workdir, remove

SCRIPT = os.path.join(TOOL, "metroid_flasher.py")
STOCK = os.path.join(NOTHING, "2-Stock-NothingOS-C5.0", "NothingFlasher")
CRDROID = os.path.join(NOTHING, "3-crDroid", "images")


class RealDryRuns(unittest.TestCase):
    def setUp(self):
        self.dir = make_workdir("real")
        with open(os.path.join(self.dir, "config.json"), "w", encoding="utf-8") as f:
            json.dump({"device": False, "vars": {}}, f)

    def tearDown(self):
        remove(self.dir)

    def dry(self, mode, src):
        env = dict(os.environ, FAKE_FB_DIR=self.dir, FAKE_FB_PYTHON=sys.executable)
        p = subprocess.run([sys.executable, "-I", SCRIPT, mode, src, "--dry-run", "--fastboot", FAKE_FASTBOOT,
                            "--work", os.path.join(self.dir, "work")],
                           capture_output=True, text=True, env=env, timeout=600)
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 0, out)
        self.assertIn("self-check: OK", out)
        with open(os.path.join(self.dir, "commands.log"), encoding="utf-8") as f:
            self.assertEqual({json.loads(ln)[0] for ln in f}, {"devices"})
        return out

    @unittest.skipUnless(os.path.isdir(STOCK), "stock image folder not present")
    def test_stock_folder(self):
        out = self.dry("stock", STOCK)
        self.assertIn("Firmware -> both slots", out)
        for n in ("system_a", "vendor_a", "product_a"):
            self.assertIn(n, out)

    @unittest.skipUnless(os.path.isdir(CRDROID), "crDroid images folder not present")
    def test_crdroid_folder(self):
        out = self.dry("rom", CRDROID)
        for n in ("system_a", "system_ext_a", "vendor_a", "product_a", "system_dlkm_a"):
            self.assertIn(n, out)


if __name__ == "__main__":
    unittest.main()
