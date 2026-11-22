"""metroid_flasher.Image: raw and Android-sparse images read as raw byte ranges."""
import os
import random
import struct
import unittest

from helpers import BLK, make_workdir, remove, pattern, write_sparse
import metroid_flasher as mf


def read_all(img, off, n, bufsize=16 * 1024 * 1024):
    return b"".join(img.reader(off, n, bufsize)())


class ImageTests(unittest.TestCase):
    def setUp(self):
        self.dir = make_workdir("img")

    def tearDown(self):
        remove(self.dir)

    def sparse(self, chunks, name="s.img"):
        path = os.path.join(self.dir, name)
        return path, write_sparse(path, chunks)

    def test_raw_image(self):
        path = os.path.join(self.dir, "r.img")
        blob = pattern(5 * BLK, 1)
        with open(path, "wb") as f:
            f.write(blob)
        img = mf.Image("r", path)
        self.assertFalse(img.sparse)
        self.assertEqual(img.size, len(blob))
        self.assertEqual(read_all(img, 4096, 8192), blob[4096:12288])

    def test_all_chunk_kinds(self):
        path, expect = self.sparse([
            ("raw", pattern(2 * BLK, 2)),
            ("fill", b"\x01\x02\x03\x04", 3),
            ("crc",),
            ("dontcare", 2),
            ("raw", pattern(BLK, 3)),
            ("fill", b"\x00\x00\x00\x00", 1),
        ])
        img = mf.Image("s", path)
        self.assertTrue(img.sparse)
        self.assertEqual(img.size, len(expect))
        self.assertEqual(img.size, 9 * BLK)
        self.assertEqual(read_all(img, 0, img.size), expect)

    def test_block_aligned_slices(self):
        path, expect = self.sparse([("raw", pattern(3 * BLK, 4)), ("fill", b"\xaa\xbb\xcc\xdd", 4),
                                    ("dontcare", 1), ("raw", pattern(2 * BLK, 5))])
        img = mf.Image("s", path)
        for off in range(0, img.size, BLK):
            for n in (BLK, 3 * BLK, img.size - off):
                if off + n <= img.size:
                    self.assertEqual(read_all(img, off, n), expect[off:off + n], (off, n))

    def test_arbitrary_slices_and_buffer_sizes(self):
        """Unaligned offsets/lengths and odd buffer sizes, including inside FILL chunks."""
        path, expect = self.sparse([("raw", pattern(2 * BLK, 6)), ("fill", b"\x11\x22\x33\x44", 3),
                                    ("crc",), ("dontcare", 1), ("raw", pattern(BLK, 7)),
                                    ("fill", b"\xf0\xe1\xd2\xc3", 2)])
        img = mf.Image("s", path)
        rnd = random.Random(1234)
        cases = [(1, 5), (2 * BLK - 3, 10), (2 * BLK + 1, 7), (2 * BLK + 2, BLK), (3 * BLK - 1, 2 * BLK)]
        cases += [(o, rnd.randrange(1, img.size - o + 1)) for o in (rnd.randrange(img.size) for _ in range(150))]
        for off, n in cases:
            for bufsize in (1, 3, 1000, 4096, 1 << 20):
                if bufsize < 64 and n > 2000:
                    continue
                self.assertEqual(read_all(img, off, n, bufsize), expect[off:off + n], (off, n, bufsize))

    def test_reader_length_matches_request(self):
        path, expect = self.sparse([("fill", b"\x01\x02\x03\x04", 2), ("raw", pattern(BLK, 8))])
        img = mf.Image("s", path)
        got = read_all(img, 100, 5000, bufsize=777)
        self.assertEqual(len(got), 5000)
        self.assertEqual(got, expect[100:5100])

    def test_unknown_chunk_type(self):
        path = os.path.join(self.dir, "bad.img")
        with open(path, "wb") as f:
            f.write(struct.pack("<IHHHHIIII", 0xED26FF3A, 1, 0, 28, 12, BLK, 1, 1, 0))
            f.write(struct.pack("<HHII", 0xCAC9, 0, 1, 12))
        with self.assertRaises(mf.FlashError):
            mf.Image("bad", path)

    def test_tiny_file_is_raw(self):
        path = os.path.join(self.dir, "tiny.img")
        with open(path, "wb") as f:
            f.write(b"\x3a\xff\x26\xed")  # sparse magic but no full header
        img = mf.Image("tiny", path)
        self.assertFalse(img.sparse)
        self.assertEqual(img.size, 4)


class HumanTests(unittest.TestCase):
    def test_human(self):
        self.assertEqual(mf.human(512), "512 B")
        self.assertEqual(mf.human(2048), "2.0 KB")
        self.assertEqual(mf.human(3 * 1024 ** 3), "3.0 GB")
        self.assertEqual(mf.human(5000 * 1024 ** 3), "5000.0 GB")


if __name__ == "__main__":
    unittest.main()
