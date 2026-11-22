"""lpsuper: partition table build/read-back, layout, piece splitting, sparse piece files."""
import os
import struct
import unittest

import helpers  # noqa: F401  (sets sys.path)
from helpers import make_workdir, remove, read_sparse_chunks
import lpsuper

MiB = 1024 * 1024
SUPER = 64 * MiB


class LayoutTests(unittest.TestCase):
    def test_layout_is_aligned_and_in_order(self):
        layout = lpsuper.plan_layout([("system", 3 * MiB + 4096), ("vendor", 8192), ("product", MiB)], SUPER)
        self.assertEqual([n for n, _, _ in layout], ["system", "vendor", "product"])
        first = lpsuper.first_logical_byte()
        self.assertEqual(layout[0][1], first)
        for name, off, size in layout:
            self.assertEqual(off % lpsuper.ALIGN, 0, name)
        for (_, o1, s1), (_, o2, _) in zip(layout, layout[1:]):
            self.assertGreaterEqual(o2, o1 + s1)

    def test_first_logical_byte_after_metadata(self):
        end = lpsuper.RESERVED + 2 * lpsuper.GEOMETRY_SIZE + 2 * lpsuper.METADATA_SLOTS * lpsuper.METADATA_MAX
        self.assertGreaterEqual(lpsuper.first_logical_byte(), end)
        self.assertEqual(lpsuper.first_logical_byte() % lpsuper.ALIGN, 0)

    def test_overflow_raises(self):
        with self.assertRaises(ValueError) as cm:
            lpsuper.plan_layout([("system", 40 * MiB), ("vendor", 30 * MiB)], SUPER)
        self.assertIn("too big", str(cm.exception))

    def test_exact_fit_ok(self):
        first = lpsuper.first_logical_byte()
        layout = lpsuper.plan_layout([("system", SUPER - first)], SUPER)
        self.assertEqual(layout[0][1] + layout[0][2], SUPER)

    def test_unaligned_size_raises(self):
        with self.assertRaises(ValueError):
            lpsuper.plan_layout([("system", 4097)], SUPER)


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.layout = lpsuper.plan_layout(
            [("system", 3 * MiB), ("system_ext", 2 * MiB), ("vendor", MiB + 8192)], SUPER)

    def test_round_trip_slot_a(self):
        region = lpsuper.metadata_region(self.layout, SUPER, "a", "qti_dynamic_partitions")
        self.assertEqual(len(region), lpsuper.first_logical_byte())
        info = lpsuper.parse_metadata_region(region)
        self.assertEqual(info["super_size"], SUPER)
        self.assertEqual(info["copies"], 2 * lpsuper.METADATA_SLOTS)
        self.assertEqual(info["groups"], ["default", "qti_dynamic_partitions_a", "qti_dynamic_partitions_b"])
        got = info["partitions"]
        self.assertEqual(len(got), 2 * len(self.layout))
        for (name, off, size), (pname, grp, start, psize) in zip(self.layout, got):
            self.assertEqual((pname, grp, start, psize), (name + "_a", "qti_dynamic_partitions_a", off, size))
        for (name, _, _), (pname, grp, start, psize) in zip(self.layout, got[len(self.layout):]):
            self.assertEqual((pname, grp, psize), (name + "_b", "qti_dynamic_partitions_b", 0))

    def test_round_trip_slot_b(self):
        info = lpsuper.parse_metadata_region(lpsuper.metadata_region(self.layout, SUPER, "b", "g"))
        self.assertEqual(info["partitions"][0][:2], ("system_b", "g_b"))
        self.assertEqual(info["partitions"][len(self.layout)][:2], ("system_a", "g_a"))

    def test_metadata_is_padded_and_deterministic(self):
        m1 = lpsuper.build_metadata(self.layout, SUPER, "a", "g")
        m2 = lpsuper.build_metadata(self.layout, SUPER, "a", "g")
        self.assertEqual(len(m1), lpsuper.METADATA_MAX)
        self.assertEqual(m1, m2)

    def test_corruption_detected(self):
        region = bytearray(lpsuper.metadata_region(self.layout, SUPER, "a", "g"))
        base = lpsuper.RESERVED + 2 * lpsuper.GEOMETRY_SIZE
        for where, msg in ((lpsuper.RESERVED + 45, "geometry"),          # geometry body
                           (lpsuper.RESERVED + lpsuper.GEOMETRY_SIZE + 45, "backup geometry"),
                           (base + 300, "tables checksum"),               # first metadata copy tables
                           (base + 5 * lpsuper.METADATA_MAX + 20, "header checksum")):  # last copy header
            bad = bytearray(region)
            bad[where] ^= 0xFF
            with self.assertRaises(ValueError, msg=msg) as cm:
                lpsuper.parse_metadata_region(bytes(bad))
            self.assertIn(msg.split()[0], str(cm.exception))

    def test_name_too_long(self):
        layout = lpsuper.plan_layout([("x" * 40, MiB)], SUPER)
        with self.assertRaises(ValueError):
            lpsuper.build_metadata(layout, SUPER, "a", "g")


class PieceTests(unittest.TestCase):
    def test_split(self):
        layout = [("system", 1 * MiB, 5 * MiB + 4096), ("vendor", 8 * MiB, MiB)]
        pieces = lpsuper.plan_pieces(layout, 2 * MiB)
        self.assertEqual(pieces, [
            ("system", 1 * MiB, 0, 2 * MiB),
            ("system", 3 * MiB, 2 * MiB, 2 * MiB),
            ("system", 5 * MiB, 4 * MiB, MiB + 4096),
            ("vendor", 8 * MiB, 0, MiB),
        ])

    def test_split_covers_every_byte(self):
        layout = lpsuper.plan_layout([("system", 7 * MiB + 12288), ("odm", 4096)], SUPER)
        pieces = lpsuper.plan_pieces(layout, MiB + 100)  # not block aligned: rounded down
        for name, off, size in layout:
            mine = [p for p in pieces if p[0] == name]
            self.assertEqual(sum(p[3] for p in mine), size)
            pos = 0
            for _, dev, src, n in mine:
                self.assertEqual(src, pos)
                self.assertEqual(dev, off + pos)
                self.assertEqual(n % lpsuper.BLOCK, 0)
                self.assertLessEqual(n, MiB)
                pos += n


class SparsePieceTests(unittest.TestCase):
    def setUp(self):
        self.dir = make_workdir("lp")

    def tearDown(self):
        remove(self.dir)

    def test_middle_piece(self):
        out = os.path.join(self.dir, "p.img")
        data = bytes(range(256)) * 32  # 8 KiB
        lpsuper.write_sparse_piece(out, SUPER, 2 * MiB, len(data), lambda: [data[:4096], data[4096:]])
        hdr, chunks, rest = read_sparse_chunks(out)
        self.assertEqual(hdr[0], lpsuper.SPARSE_MAGIC)
        self.assertEqual(hdr[5], 4096)
        self.assertEqual(hdr[6], SUPER // 4096)
        self.assertEqual(chunks, [(0xCAC3, 2 * MiB // 4096, 0), (0xCAC1, 2, 8192),
                                  (0xCAC3, (SUPER - 2 * MiB - 8192) // 4096, 0)])
        self.assertEqual(rest, 0)
        with open(out, "rb") as f:
            f.seek(28 + 12 + 12)
            self.assertEqual(f.read(8192), data)

    def test_piece_at_start_and_end(self):
        out = os.path.join(self.dir, "p.img")
        lpsuper.write_sparse_piece(out, SUPER, 0, 4096, lambda: [bytes(4096)])
        _, chunks, _ = read_sparse_chunks(out)
        self.assertEqual([c[0] for c in chunks], [0xCAC1, 0xCAC3])
        lpsuper.write_sparse_piece(out, SUPER, SUPER - 4096, 4096, lambda: [bytes(4096)])
        _, chunks, _ = read_sparse_chunks(out)
        self.assertEqual([c[0] for c in chunks], [0xCAC3, 0xCAC1])

    def test_short_read_raises(self):
        out = os.path.join(self.dir, "p.img")
        with self.assertRaises(IOError):
            lpsuper.write_sparse_piece(out, SUPER, 0, 8192, lambda: [bytes(4096)])

    def test_unaligned_raises(self):
        with self.assertRaises(ValueError):
            lpsuper.write_sparse_piece(os.path.join(self.dir, "p.img"), SUPER, 100, 4096, lambda: [])

    def test_file_reader(self):
        src = os.path.join(self.dir, "src.bin")
        blob = os.urandom(10000)
        with open(src, "wb") as f:
            f.write(blob)
        got = b"".join(lpsuper.file_reader(src, 123, 9000, bufsize=1000)())
        self.assertEqual(got, blob[123:9123])
        with self.assertRaises(IOError):
            b"".join(lpsuper.file_reader(src, 9000, 2000)())


if __name__ == "__main__":
    unittest.main()
