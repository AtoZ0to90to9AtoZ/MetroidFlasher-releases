"""Shared helpers for the MetroidFlasher tests (stdlib only, no phone)."""
import os
import shutil
import struct
import sys
import tempfile

TESTS = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.dirname(TESTS)
WORK = os.path.join(TOOL, "work")
FAKE_DIR = os.path.join(TESTS, "fake_fastboot")
FAKE_FASTBOOT = os.path.join(FAKE_DIR, "fastboot.cmd")
NOTHING = os.path.dirname(os.path.dirname(TOOL))  # ...\NothingPhone3

if TOOL not in sys.path:
    sys.path.insert(0, TOOL)

BLK = 4096


def make_workdir(tag):
    """A fresh temp folder inside Tools\\MetroidFlasher\\work (deleted by the caller)."""
    os.makedirs(WORK, exist_ok=True)
    return tempfile.mkdtemp(prefix=f"test_{tag}_", dir=WORK)


def remove(path):
    shutil.rmtree(path, ignore_errors=True)


def pattern(n, seed):
    """n bytes of a recognisable, non-repeating-per-block pattern."""
    out = bytearray(n)
    for i in range(0, n, 4):
        out[i:i + 4] = struct.pack("<I", (i // 4 * 2654435761 + seed * 40503) & 0xFFFFFFFF)[: n - i]
    return bytes(out)


def write_sparse(path, chunks, blk=BLK):
    """Write an Android sparse image, independent of the tool's own writer.

    chunks: list of ("raw", bytes) | ("fill", 4-byte pattern, blocks) | ("dontcare", blocks) | ("crc",)
    Returns the expanded raw bytes the image stands for.
    """
    body = b""
    raw = bytearray()
    total = 0
    n = 0
    for c in chunks:
        if c[0] == "raw":
            data = c[1]
            assert len(data) % blk == 0
            body += struct.pack("<HHII", 0xCAC1, 0, len(data) // blk, 12 + len(data)) + data
            raw += data
            total += len(data) // blk
        elif c[0] == "fill":
            body += struct.pack("<HHII", 0xCAC2, 0, c[2], 16) + c[1]
            raw += c[1] * (c[2] * blk // 4)
            total += c[2]
        elif c[0] == "dontcare":
            body += struct.pack("<HHII", 0xCAC3, 0, c[1], 12)
            raw += bytes(c[1] * blk)
            total += c[1]
        elif c[0] == "crc":
            body += struct.pack("<HHII", 0xCAC4, 0, 0, 16) + b"\xde\xad\xbe\xef"
        else:
            raise ValueError(c)
        n += 1
    with open(path, "wb") as f:
        f.write(struct.pack("<IHHHHIIII", 0xED26FF3A, 1, 0, 28, 12, blk, total, n, 0))
        f.write(body)
    return bytes(raw)


def read_sparse_chunks(path):
    """Return (header tuple, [(kind, blocks, payload_len)]) for a sparse file."""
    with open(path, "rb") as f:
        hdr = struct.unpack("<IHHHHIIII", f.read(28))
        out = []
        for _ in range(hdr[7]):
            kind, _, blocks, tsz = struct.unpack("<HHII", f.read(12))
            f.seek(tsz - 12, 1)
            out.append((kind, blocks, tsz - 12))
        rest = f.read()
    return hdr, out, len(rest)
