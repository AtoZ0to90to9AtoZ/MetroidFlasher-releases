"""Tests for the 1.0 pieces: ext4write (image builder), the GApps catalog, own root image, keep-data rule.
Fast: everything runs on small synthetic images (no phone, no ROM files needed)."""
import hashlib
import json
import os
import stat
import struct
import sys
import tempfile
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import ext4read   # noqa: E402
import ext4write  # noqa: E402
import gapps      # noqa: E402
import metroid_flasher as mf  # noqa: E402

LABEL = {"security.selinux": b"u:object_r:system_file:s0\0"}


def small_tree():
    t = ext4write.Tree()
    t.volume_name, t.last_mounted = b"product", b"/product"
    d = stat.S_IFDIR | 0o755
    for p in ("/", "/lost+found", "/app", "/app/Test", "/bin", "/etc"):
        t.nodes[p] = ext4write.Node(p, d, 0, 0, LABEL)
    t.nodes["/bin"].gid = 2000
    t.add(ext4write.Node("/app/Test/Test.apk", stat.S_IFREG | 0o644, 0, 0, LABEL, size=10000, data=b"x" * 10000))
    t.add(ext4write.Node("/etc/empty", stat.S_IFREG | 0o600, 0, 0, LABEL, size=0, data=b""))
    caps = {"security.selinux": b"u:object_r:system_file:s0\0",
            "security.capability": struct.pack("<5I", 0x02000002, 0x3000, 0, 0, 0)}
    t.add(ext4write.Node("/bin/tool", stat.S_IFREG | 0o750, 0, 2000, caps, size=5, data=b"#!sh\n"))
    t.add(ext4write.Node("/bin/short", stat.S_IFLNK | 0o777, 0, 0, LABEL, target=b"tool"))
    t.add(ext4write.Node("/bin/long", stat.S_IFLNK | 0o777, 0, 0, LABEL, target=b"/x" * 50))
    for i in range(300):     # a directory bigger than one block
        t.add(ext4write.Node(f"/etc/f{i:03d}-{'n' * 30}", stat.S_IFREG | 0o644, 0, 0, LABEL, size=3, data=b"abc"))
    return t


def read_back(path):
    img = ext4read.Ext4Image(path)
    out = {}
    for p, n in img.walk():
        data = img.read_data(n) if stat.S_ISREG(n.mode) else None
        tgt = img.symlink_target(n) if stat.S_ISLNK(n.mode) else None
        out[p] = (n.mode, n.uid, n.gid, n.xattrs, data, tgt)
    img.close()
    return out


class Ext4WriteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ext4w-")

    def test_small_tree_reads_back_exactly(self):
        t = small_tree()
        out = os.path.join(self.tmp, "a.img")
        ext4write.write_image(t, out, log=lambda s: None)
        got = read_back(out)
        self.assertEqual(set(got), set(t.nodes))
        for p, n in t.nodes.items():
            mode, uid, gid, xattrs, data, tgt = got[p]
            self.assertEqual((mode, uid, gid), (n.mode, n.uid, n.gid), p)
            self.assertEqual(xattrs, n.xattrs, p)
            if stat.S_ISREG(n.mode):
                self.assertEqual(data, n.data, p)
            if stat.S_ISLNK(n.mode):
                self.assertEqual(tgt.encode(), n.target, p)

    def test_many_extents_use_a_leaf_block(self):
        # force tiny extents so a 100-block file needs 13 of them (> 4 in the inode -> depth-1 tree)
        old = ext4write.MAX_EXT_LEN
        ext4write.MAX_EXT_LEN = 8
        try:
            t = small_tree()
            blob = hashlib.sha256(b"seed").digest() * (100 * 4096 // 32)
            t.add(ext4write.Node("/app/Test/big.bin", stat.S_IFREG | 0o644, 0, 0, LABEL, size=len(blob), data=blob))
            out = os.path.join(self.tmp, "b.img")
            ext4write.write_image(t, out, log=lambda s: None)
        finally:
            ext4write.MAX_EXT_LEN = old
        got = read_back(out)
        self.assertEqual(got["/app/Test/big.bin"][4], blob)

    def test_remove_and_add(self):
        t = small_tree()
        self.assertEqual(t.remove("/app/Test"), 2)
        with self.assertRaises(ValueError):
            t.add(ext4write.Node("/nope/x", stat.S_IFREG | 0o644, size=0, data=b""))
        out = os.path.join(self.tmp, "c.img")
        ext4write.write_image(t, out, log=lambda s: None)
        self.assertNotIn("/app/Test/Test.apk", read_back(out))

    def test_from_image_roundtrip(self):
        a = os.path.join(self.tmp, "src.img")
        ext4write.write_image(small_tree(), a, log=lambda s: None)
        t = ext4write.Tree.from_image(a)
        b = os.path.join(self.tmp, "dst.img")
        ext4write.write_image(t, b, log=lambda s: None)
        t._img.close()
        self.assertEqual(read_back(a), read_back(b))


class CatalogTests(unittest.TestCase):
    def test_crdroid_map(self):
        key, paths = gapps.rom_map({"ro.crdroid.device": "metroid"}, "omni")
        self.assertEqual(key, "crdroid-metroid")
        self.assertIn("/product/app/GsfProxy", paths)
        self.assertIn("/product/app/Canvas", paths)
        _, core = gapps.rom_map({"ro.crdroid.device": "metroid"}, "core")
        self.assertNotIn("/product/app/Canvas", core)

    def test_unknown_rom_refused(self):
        with self.assertRaises(LookupError):
            gapps.rom_map({"ro.build.id": "something"}, "omni")

    def test_missing_zip_refused(self):
        with self.assertRaises(FileNotFoundError):
            gapps.specs_for("basic", {"basic": "x.zip"})       # no SetupWizard add-on
        self.assertEqual(gapps.specs_for("core", {"basic": "b.zip", "setupwizard": "s.zip"}),
                         ["b.zip::AppSet/Core/", "s.zip"])

    def test_play_and_wizard_locked(self):
        self.assertTrue({"GmsCore", "GooglePlayStore", "GoogleServicesFramework", "SetupWizard"} <= gapps.LOCKED)


def boot_img(path, kernel, osv_android=(17, 0), patch=(2026, 10)):
    a, b = osv_android
    y, m = patch
    osv = (((a << 14) | (b << 7)) << 11) | (((y - 2000) << 4) | m)
    with open(path, "wb") as f:
        f.write(b"ANDROID!" + struct.pack("<III", 4096 if kernel else 0, 1000, osv) + bytes(4096))


class RootImageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="root-")
        self.rom_ib = os.path.join(self.tmp, "init_boot.img")
        boot_img(self.rom_ib, kernel=False)

    def test_matching_init_boot_swapped_in(self):
        mine = os.path.join(self.tmp, "magisk.img")
        boot_img(mine, kernel=False)
        new = mf.swap_root_image({"init_boot": self.rom_ib}, mine)
        self.assertEqual(new["init_boot"], os.path.abspath(mine))

    def test_other_build_refused(self):
        mine = os.path.join(self.tmp, "old.img")
        boot_img(mine, kernel=False, patch=(2026, 9))
        with self.assertRaises(mf.FlashError):
            mf.swap_root_image({"init_boot": self.rom_ib}, mine)

    def test_boot_image_needs_a_boot_partition(self):
        mine = os.path.join(self.tmp, "apatch.img")
        boot_img(mine, kernel=True)
        self.assertEqual(mf.boot_header(mine)[0], "boot")
        with self.assertRaises(mf.FlashError):
            mf.swap_root_image({"init_boot": self.rom_ib}, mine)


class KeepDataRuleTests(unittest.TestCase):
    def test_record_changes_with_choice(self):
        tmp = tempfile.mkdtemp(prefix="keep-")
        sysimg = os.path.join(tmp, "system.img")
        open(sysimg, "wb").write(b"x" * 10)
        a = types.SimpleNamespace(gapps="omni", customize_remove="GoogleMaps")
        b = types.SimpleNamespace(gapps="stock", customize_remove="")
        ra = mf.install_record(a, {"system": sysimg})
        self.assertNotEqual(ra, mf.install_record(b, {"system": sysimg}))
        mf.remember_install(tmp, a, {"system": sysimg})
        self.assertEqual(json.load(open(os.path.join(tmp, "last-install.json"))), ra)


if __name__ == "__main__":
    unittest.main()
