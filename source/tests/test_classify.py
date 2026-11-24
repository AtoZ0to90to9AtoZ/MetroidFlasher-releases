"""metroid_flasher.classify: what gets flashed, what is skipped, and completeness checks."""
import unittest

import helpers  # noqa: F401
import metroid_flasher as mf

STOCK = {n: n + ".img" for n in [
    "abl", "xbl", "modem", "boot", "init_boot", "vendor_boot", "dtbo", "vbmeta", "vbmeta_system",
    "system", "system_ext", "product", "vendor", "odm", "vendor_dlkm",
    "persist", "modemst1", "fsg", "frp", "devinfo", "super_empty", "userdata", "weird_thing",
]}


class ClassifyTests(unittest.TestCase):
    def test_never_touch_is_never_flashed(self):
        imgs = dict(STOCK)
        for n in mf.NEVER_TOUCH:
            imgs[n] = n + ".img"
        plan = mf.classify(imgs, "stock", False)
        flashed = set(plan["firmware"]) | set(plan["boot"]) | set(plan["logical"])
        self.assertFalse(flashed & mf.NEVER_TOUCH)
        ignored = {n for n, _ in plan["ignored"]}
        self.assertTrue(mf.NEVER_TOUCH <= ignored)

    def test_groups_and_order(self):
        plan = mf.classify(STOCK, "stock", False)
        self.assertEqual(plan["firmware"], ["abl", "modem", "xbl"])
        self.assertEqual(plan["logical"], ["system", "system_ext", "product", "vendor", "odm", "vendor_dlkm"])
        # boot images alphabetical, vbmeta* last
        self.assertEqual(plan["boot"], ["boot", "dtbo", "init_boot", "vendor_boot", "vbmeta", "vbmeta_system"])

    def test_unknown_images_skipped(self):
        plan = mf.classify(STOCK, "rom", False)
        why = dict(plan["ignored"])
        for n in ("super_empty", "userdata", "weird_thing"):
            self.assertIn("not a partition this tool knows", why[n])
        self.assertIn("never flashed", why["persist"])

    def test_skip_firmware(self):
        plan = mf.classify(STOCK, "stock", True)
        self.assertEqual(plan["firmware"], [])
        why = dict(plan["ignored"])
        self.assertIn("--skip-firmware", why["abl"])
        self.assertIn("--skip-firmware", why["xbl"])

    def test_stock_completeness(self):
        for missing in ("boot", "vendor_boot", "init_boot", "vbmeta", "system", "vendor"):
            imgs = {k: v for k, v in STOCK.items() if k != missing}
            with self.assertRaises(mf.FlashError) as cm:
                mf.classify(imgs, "stock", False)
            self.assertIn(missing, str(cm.exception))

    def test_rom_mode_is_not_strict(self):
        plan = mf.classify({"system": "s", "boot": "b"}, "rom", False)
        self.assertEqual(plan["logical"], ["system"])
        self.assertEqual(plan["boot"], ["boot"])

    def test_no_system_images(self):
        with self.assertRaises(mf.FlashError):
            mf.classify({"boot": "b", "vbmeta": "v"}, "rom", False)

    def test_system_required(self):
        with self.assertRaises(mf.FlashError) as cm:
            mf.classify({"vendor": "v", "boot": "b"}, "rom", False)
        self.assertIn("system.img", str(cm.exception))


class HeaderTests(unittest.TestCase):
    """A broken boot/vbmeta image must stop the run on the PC (real report 2026-11-23: a vbmeta.img
    without AVB0 was only found after super had been flashed)."""

    def make(self, d, overrides=None):
        import os
        good = {"boot": b"ANDROID!", "init_boot": b"ANDROID!", "recovery": b"ANDROID!", "pvmfw": b"ANDROID!",
                "vendor_boot": b"VNDRBOOT", "dtbo": bytes.fromhex("d7b7ab1e01cc0187"),
                "vbmeta": b"AVB0\0\0\0\1", "vbmeta_system": b"AVB0\0\0\0\1", "vbmeta_vendor": b"AVB0\0\0\0\1"}
        good.update(overrides or {})
        paths = {}
        for n, head in good.items():
            p = os.path.join(d, n + ".img")
            with open(p, "wb") as f:
                f.write(head + b"\0" * 64 if head else b"")
            paths[n] = p
        paths["system"] = os.path.join(d, "system.img")
        open(paths["system"], "wb").close()
        return paths

    def test_good_images_pass(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            paths = self.make(d)
            mf.check_headers(mf.classify(paths, "rom", False), paths)

    def test_bad_images_stop_before_flashing(self):
        import tempfile
        for name, head, why in [("vbmeta", b"<html><h", "starts with"), ("vbmeta_system", b"", "empty"),
                                ("init_boot", b"\0" * 8, "all zeros"), ("vendor_boot", b"ANDROID!", "starts with")]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as d:
                paths = self.make(d, {name: head})
                with self.assertRaises(mf.FlashError) as cm:
                    mf.check_headers(mf.classify(paths, "rom", False), paths)
                msg = str(cm.exception)
                self.assertIn(f"{name}.img", msg)
                self.assertIn(why, msg)
                self.assertIn("Nothing was flashed", msg)


if __name__ == "__main__":
    unittest.main()
