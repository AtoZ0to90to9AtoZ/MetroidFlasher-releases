"""End to end: run metroid_flasher.py against the FAKE fastboot on tiny fake images.

No phone is involved. Every run passes --fastboot pointing at tests\\fake_fastboot\\fastboot.cmd,
so the real fastboot.exe is never started.
"""
import json
import os
import subprocess
import sys
import unittest

from helpers import BLK, FAKE_FASTBOOT, TOOL, make_workdir, remove, pattern, write_sparse
import lpsuper

MiB = 1024 * 1024
SUPER_SIZE = 64 * MiB
SCRIPT = os.path.join(TOOL, "metroid_flasher.py")

PHONE_OK = {
    "device": True,
    "vars": {"product": "sun", "is-userspace": "no", "unlocked": "yes", "current-slot": "b",
             "partition-size:super": hex(SUPER_SIZE)},
    "device_info": "(bootloader) \tDevice unlocked: true\n(bootloader) \tDevice critical unlocked: true",
    "fail": {},
}


class FakePhoneTest(unittest.TestCase):
    """Builds a folder of fake images and a fake phone; runs the real tool against them."""

    def setUp(self):
        self.dir = make_workdir("e2e")
        self.imgdir = os.path.join(self.dir, "images")
        self.fbdir = os.path.join(self.dir, "fakefb")
        self.work = os.path.join(self.dir, "work")
        for d in (self.imgdir, self.fbdir, self.work):
            os.makedirs(d)
        self.raw = {}
        # system: sparse with every chunk kind, 2.5 MiB expanded -> 3 pieces of 1 MiB
        self.raw["system"] = write_sparse(os.path.join(self.imgdir, "system.img"), [
            ("raw", pattern(100 * BLK, 1)), ("fill", b"\x5a\xa5\x0f\xf0", 200), ("crc",),
            ("dontcare", 40), ("raw", pattern(300 * BLK, 2))])
        self.raw["vendor"] = self._raw("vendor", MiB + BLK, 3)      # 2 pieces
        self.raw["product"] = self._raw("product", 2 * BLK, 4)      # 1 piece
        for n, seed in (("boot", 5), ("init_boot", 6), ("vendor_boot", 7), ("vbmeta", 8),
                        ("abl", 9), ("xbl", 10), ("persist", 11), ("mystery", 12)):
            self._raw(n, BLK, seed)
        self.super_path = os.path.join(self.dir, "super.bin")
        with open(self.super_path, "wb") as f:
            f.truncate(SUPER_SIZE)
        self.cfg = json.loads(json.dumps(PHONE_OK))
        self.cfg["super_path"] = self.super_path

    def tearDown(self):
        remove(self.dir)

    def _raw(self, name, size, seed):
        data = pattern(size, seed)
        with open(os.path.join(self.imgdir, name + ".img"), "wb") as f:
            f.write(data)
        return data

    def run_tool(self, mode="rom", *extra, stdin=""):
        with open(os.path.join(self.fbdir, "config.json"), "w", encoding="utf-8") as f:
            json.dump(self.cfg, f)
        env = dict(os.environ, FAKE_FB_DIR=self.fbdir, FAKE_FB_PYTHON=sys.executable)
        cmd = [sys.executable, "-I", SCRIPT, mode, self.imgdir, "--fastboot", FAKE_FASTBOOT,
               "--work", self.work, *extra]
        p = subprocess.run(cmd, input=stdin, capture_output=True, text=True, env=env, timeout=300)
        self.output = p.stdout + p.stderr
        return p.returncode

    def commands(self):
        path = os.path.join(self.fbdir, "commands.log")
        if not os.path.isfile(path):
            return []
        with open(path, encoding="utf-8") as f:
            return [json.loads(ln) for ln in f]

    def writes(self):
        """Commands that change the phone (everything except devices/getvar/oem device-info)."""
        out = []
        for c in self.commands():
            if c[0] in ("devices", "getvar") or c[:2] == ["oem", "device-info"]:
                continue
            out.append(c[:2] if c[0] == "flash" else c)
        return out

    def assertRc(self, rc, want):
        self.assertEqual(rc, want, self.output)


BOOT_BOTH = [["flash", "boot_b"], ["flash", "boot_a"], ["flash", "init_boot_b"], ["flash", "init_boot_a"],
             ["flash", "vendor_boot_b"], ["flash", "vendor_boot_a"], ["flash", "vbmeta_b"], ["flash", "vbmeta_a"]]
END = [["erase", "misc"], ["-w"], ["set_active", "b"], ["reboot"]]


class FlashOrderTests(FakePhoneTest):
    """0.7 order: firmware -> ONE super.img -> boot images on both slots, vbmeta last -> wipe -> slot -> reboot."""

    def test_rom_full_order(self):
        rc = self.run_tool("rom", "--yes")
        self.assertRc(rc, 0)
        cmds = self.commands()
        self.assertEqual(cmds[0], ["devices"])
        self.assertIn(["oem", "device-info"], cmds)
        expect = [["flash", "abl_b"], ["flash", "xbl_b"], ["flash", "super"]] + BOOT_BOTH + END
        self.assertEqual(self.writes(), expect, self.output)
        supers = [c[2] for c in cmds if c[:2] == ["flash", "super"]]
        self.assertEqual([os.path.basename(p) for p in supers], ["super.img"])
        flat = " ".join(" ".join(c) for c in cmds)
        self.assertNotIn("persist", flat)
        self.assertNotIn("mystery", flat)
        self.assertNotIn("--disable-verification", flat)
        self.assertIn("DONE", self.output)
        self.assertFalse(os.path.exists(os.path.join(self.work, "super.img")))  # temp super removed

    def test_super_contents_read_back(self):
        """The whole super.img lands in the fake super; read the partition table and data back."""
        self.assertRc(self.run_tool("rom", "--yes"), 0)
        with open(self.super_path, "rb") as f:
            sup = f.read()
        info = lpsuper.parse_metadata_region(sup[:lpsuper.first_logical_byte()])
        self.assertEqual(info["super_size"], SUPER_SIZE)
        parts = {name: (start, size) for name, _, start, size in info["partitions"]}
        for name in ("system", "product", "vendor"):
            start, size = parts[name + "_b"]
            self.assertEqual(size, len(self.raw[name]), name)
            self.assertEqual(sup[start:start + size], self.raw[name], name)
            if name + "_a" in parts:
                self.assertEqual(parts[name + "_a"][1], 0)
        order = [n for n, _, _, _ in info["partitions"]][:3]
        self.assertEqual(order, ["system_b", "product_b", "vendor_b"])

    def test_switches_to_slot_b_first(self):
        self.cfg["vars"]["current-slot"] = "a"
        self.assertRc(self.run_tool("rom", "--yes"), 0)
        w = self.writes()
        self.assertEqual(w[0], ["set_active", "b"])
        self.assertEqual(w[1:], [["flash", "abl_b"], ["flash", "xbl_b"], ["flash", "super"]] + BOOT_BOTH + END)

    def test_stock_keep_data_no_reboot(self):
        rc = self.run_tool("stock", "--yes", "--keep-data", "--no-reboot")
        self.assertRc(rc, 0)
        w = self.writes()
        # stock: firmware to BOTH slots
        self.assertEqual(w[:4], [["flash", "abl_b"], ["flash", "abl_a"], ["flash", "xbl_b"], ["flash", "xbl_a"]])
        self.assertEqual(w[4], ["flash", "super"])
        self.assertEqual(w[5:13], BOOT_BOTH)
        self.assertEqual(w[-2:], [["erase", "misc"], ["set_active", "b"]])
        self.assertNotIn(["-w"], w)
        self.assertNotIn(["reboot"], w)
        self.assertIn("ROM part finished", self.output)  # --no-reboot: more steps follow, no DONE
        with open(self.super_path, "rb") as f:
            info = lpsuper.parse_metadata_region(f.read(lpsuper.first_logical_byte()))
        self.assertEqual(info["partitions"][0][0], "system_b")

    def test_skip_firmware(self):
        self.assertRc(self.run_tool("rom", "--yes", "--skip-firmware"), 0)
        flat = str(self.writes())
        self.assertNotIn("abl", flat)
        self.assertNotIn("xbl", flat)
        self.assertEqual(self.writes()[0], ["flash", "super"])

    def test_confirmation_required(self):
        rc = self.run_tool("rom", stdin="no\n")
        self.assertRc(rc, 1)
        self.assertEqual(self.writes(), [])
        self.assertIn("Cancelled", self.output)

    def test_confirmation_flash(self):
        self.assertRc(self.run_tool("rom", stdin="FLASH\n"), 0)
        self.assertEqual(self.writes()[-1], ["reboot"])


class StopTests(FakePhoneTest):
    def test_stop_on_failed_super(self):
        self.cfg["fail"] = {"flash super": "FAILED (remote: 'Error writing super')"}
        rc = self.run_tool("rom", "--yes")
        self.assertRc(rc, 2)
        w = self.writes()
        self.assertEqual(w[-1], ["flash", "super"])
        for c in (["flash", "boot_b"], ["flash", "vbmeta_b"], ["erase", "misc"], ["-w"], ["reboot"]):
            self.assertNotIn(c, w)
        self.assertIn("STOPPED", self.output)
        self.assertIn("Error writing super", self.output)
        self.assertFalse(os.path.exists(os.path.join(self.work, "super.img")))  # temp super removed

    def test_critical_partition_hint(self):
        self.cfg["fail"] = {"flash abl_b": "FAILED (remote: 'Flashing is not allowed for Critical Partitions')"}
        rc = self.run_tool("rom", "--yes")
        self.assertRc(rc, 2)
        self.assertEqual(self.writes(), [["flash", "abl_b"]])
        self.assertIn("unlock_critical", self.output)
        self.assertIn("--skip-firmware", self.output)

    def test_stop_on_nonzero_exit_without_failed_text(self):
        self.cfg["fail"] = {"flash boot_b": "fastboot: error: Command failed"}
        self.assertRc(self.run_tool("rom", "--yes"), 2)
        w = self.writes()
        self.assertEqual(w[-1], ["flash", "boot_b"])
        self.assertNotIn(["flash", "vbmeta_b"], w)   # vbmeta never written after a failed boot image
        self.assertNotIn(["-w"], w)

    def test_needs_critical_unlock_for_firmware(self):
        self.cfg["device_info"] = "(bootloader) \tDevice critical unlocked: false"
        rc = self.run_tool("rom", "--yes")
        self.assertRc(rc, 2)
        self.assertEqual(self.writes(), [])
        self.assertIn("critical", self.output)
        # ...but fine without firmware
        self.assertRc(self.run_tool("rom", "--yes", "--skip-firmware"), 0)

    def test_wrong_product(self):
        self.cfg["vars"]["product"] = "pong"
        self.assertRc(self.run_tool("rom", "--yes"), 2)
        self.assertEqual(self.writes(), [])

    def test_fastbootd_refused(self):
        self.cfg["vars"]["is-userspace"] = "yes"
        self.assertRc(self.run_tool("rom", "--yes"), 2)
        self.assertEqual(self.writes(), [])
        self.assertIn("fastbootd", self.output)

    def test_locked_bootloader(self):
        self.cfg["vars"]["unlocked"] = "no"
        self.assertRc(self.run_tool("rom", "--yes"), 2)
        self.assertEqual(self.writes(), [])
        self.assertIn("locked", self.output)

    def test_bad_slot_answer(self):
        self.cfg["vars"]["current-slot"] = ""
        self.assertRc(self.run_tool("rom", "--yes"), 2)
        self.assertEqual(self.writes(), [])

    def test_super_too_small(self):
        self.cfg["vars"]["partition-size:super"] = hex(4 * MiB)
        rc = self.run_tool("rom", "--yes")
        self.assertRc(rc, 2)
        self.assertEqual(self.writes(), [])
        self.assertIn("too big", self.output)
        self.assertNotIn("Traceback", self.output)


class DryRunTests(FakePhoneTest):
    def test_dry_run_with_phone(self):
        self.assertRc(self.run_tool("rom", "--dry-run"), 0)
        self.assertEqual(self.writes(), [])
        self.assertIn("self-check: OK", self.output)
        self.assertIn("Nothing was sent", self.output)

    def test_dry_run_never_switches_slot(self):
        self.cfg["vars"]["current-slot"] = "a"
        self.assertRc(self.run_tool("rom", "--dry-run"), 0)
        self.assertEqual(self.writes(), [], "a dry run must not change the phone, not even the slot")

    def test_dry_run_without_phone(self):
        self.cfg["device"] = False
        self.assertRc(self.run_tool("stock", "--dry-run", "--slot", "b"), 0)
        self.assertEqual({c[0] for c in self.commands()}, {"devices"})
        self.assertIn("planning for slot b", self.output)

    # author 2026-11-20: a phone that isn't ready must FAIL the dry run, not just the real flash
    def _dry_fails(self):
        self.assertRc(self.run_tool("rom", "--dry-run"), 2)
        self.assertEqual(self.writes(), [])

    def test_dry_run_fails_wrong_product(self):
        self.cfg["vars"]["product"] = "pong"
        self._dry_fails()

    def test_dry_run_fails_locked(self):
        self.cfg["vars"]["unlocked"] = "no"
        self._dry_fails()

    def test_dry_run_fails_fastbootd(self):
        self.cfg["vars"]["is-userspace"] = "yes"
        self._dry_fails()

    def test_dry_run_fails_super_too_small(self):
        self.cfg["vars"]["partition-size:super"] = hex(4 * MiB)
        self._dry_fails()

    def test_dry_run_fails_critical_locked(self):
        self.cfg["device_info"] = "(bootloader) \tDevice critical unlocked: false"
        self._dry_fails()


if __name__ == "__main__":
    unittest.main()
