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


if __name__ == "__main__":
    unittest.main()
