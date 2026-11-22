"""Build the Android dynamic-partition ("super") layout and write it as sparse pieces.

The bootloader on the Nothing Phone 3 accepts `fastboot flash super <sparse image>`
even when fastbootd is unreachable. A sparse image can mark regions DONT_CARE, so
we send super as a series of pieces: each piece carries RAW data for one stretch
of the partition and leaves everything else untouched. The LP metadata (the table
that says where system/vendor/... live inside super) goes in its own small piece,
flashed last.

Format references: system/core/fs_mgr/liblp/include/liblp/metadata_format.h and
system/core/libsparse/sparse_format.h in AOSP.
"""
import hashlib
import struct

BLOCK = 4096
SECTOR = 512
ALIGN = 1024 * 1024
METADATA_MAX = 65536
METADATA_SLOTS = 3
RESERVED = 4096
GEOMETRY_SIZE = 4096

GEOMETRY_MAGIC = 0x616C4467
HEADER_MAGIC = 0x414C5030
ATTR_READONLY = 0x1
HEADER_FLAG_VIRTUAL_AB = 0x1

RAW_CHUNK_MAX = 64 * 1024 * 1024
SPARSE_MAGIC = 0xED26FF3A
CHUNK_RAW = 0xCAC1
CHUNK_DONT_CARE = 0xCAC3


def align_up(x, a):
    return (x + a - 1) // a * a


def first_logical_byte():
    meta_end = RESERVED + 2 * GEOMETRY_SIZE + 2 * METADATA_SLOTS * METADATA_MAX
    return align_up(meta_end, ALIGN)


def plan_layout(parts, super_size):
    """parts: list of (name, size_bytes). Returns list of (name, offset, size)."""
    off = first_logical_byte()
    out = []
    for name, size in parts:
        if size % BLOCK:
            raise ValueError(f"{name}: size {size} is not a multiple of {BLOCK}")
        off = align_up(off, ALIGN)
        out.append((name, off, size))
        off += size
    if off > super_size:
        raise ValueError(
            f"images need {off} bytes but super is only {super_size} bytes "
            f"({(off - super_size) / 1048576:.0f} MiB too big)"
        )
    return out


def _name36(s):
    b = s.encode()
    if len(b) >= 36:
        raise ValueError(f"partition name too long: {s}")
    return b.ljust(36, b"\0")


def build_metadata(layout, super_size, slot, group, shared=False):
    """shared=True: the other slot's partitions point at the SAME blocks as this slot's, so either slot
    boots the one system in super (super only has room for one copy)."""
    other = "b" if slot == "a" else "a"
    first = first_logical_byte()
    groups = _name36("default") + struct.pack("<IQ", 0, 0)
    groups += _name36(f"{group}_{slot}") + struct.pack("<IQ", 0, super_size - first)
    groups += _name36(f"{group}_{other}") + struct.pack("<IQ", 0, super_size - first)

    extents = b""
    entries = []
    for i, (name, off, size) in enumerate(layout):
        extents += struct.pack("<QIQI", size // SECTOR, 0, off // SECTOR, 0)
        entries.append(_name36(f"{name}_{slot}") + struct.pack("<IIII", ATTR_READONLY, i, 1, 1))
    for i, (name, _, _) in enumerate(layout):
        if shared:
            entries.append(_name36(f"{name}_{other}") + struct.pack("<IIII", ATTR_READONLY, i, 1, 2))
        else:
            entries.append(_name36(f"{name}_{other}") + struct.pack("<IIII", ATTR_READONLY, len(layout), 0, 2))
    partitions = b"".join(entries)
    blockdev = struct.pack("<QIIQ", first // SECTOR, ALIGN, 0, super_size) + _name36("super") + struct.pack("<I", 0)

    tables = partitions + extents + groups + blockdev
    desc = struct.pack(
        "<12I",
        0, len(entries), 52,
        len(partitions), len(layout), 24,
        len(partitions) + len(extents), 3, 48,
        len(partitions) + len(extents) + len(groups), 1, 64,
    )
    tables_sum = hashlib.sha256(tables).digest()

    def header(csum):
        h = struct.pack("<IHHI", HEADER_MAGIC, 10, 2, 256) + csum
        h += struct.pack("<I", len(tables)) + tables_sum + desc
        h += struct.pack("<I", HEADER_FLAG_VIRTUAL_AB) + b"\0" * 124
        return h

    meta = header(hashlib.sha256(header(b"\0" * 32)).digest()) + tables
    if len(meta) > METADATA_MAX:
        raise ValueError("metadata too large")
    return meta.ljust(METADATA_MAX, b"\0")


def build_geometry():
    def g(csum):
        return struct.pack("<II", GEOMETRY_MAGIC, 52) + csum + struct.pack("<III", METADATA_MAX, METADATA_SLOTS, BLOCK)
    return g(hashlib.sha256(g(b"\0" * 32)).digest()).ljust(GEOMETRY_SIZE, b"\0")


def metadata_region(layout, super_size, slot, group, shared=False):
    geo = build_geometry()
    meta = build_metadata(layout, super_size, slot, group, shared)
    region = b"\0" * RESERVED + geo + geo + meta * (2 * METADATA_SLOTS)
    return region.ljust(first_logical_byte(), b"\0")


def parse_metadata_region(data):
    """Independent re-read of a metadata region. Returns dict; raises on any mismatch."""
    g = data[RESERVED:RESERVED + 52]
    magic, size = struct.unpack_from("<II", g, 0)
    if magic != GEOMETRY_MAGIC or size != 52:
        raise ValueError("bad geometry magic")
    if hashlib.sha256(g[:8] + b"\0" * 32 + g[40:]).digest() != g[8:40]:
        raise ValueError("geometry checksum mismatch")
    if data[RESERVED + GEOMETRY_SIZE:RESERVED + GEOMETRY_SIZE + 52] != g:
        raise ValueError("backup geometry differs")
    mmax, slots, _ = struct.unpack_from("<III", g, 40)
    base = RESERVED + 2 * GEOMETRY_SIZE
    result = None
    for s in range(2 * slots):
        m = data[base + s * mmax: base + (s + 1) * mmax]
        if struct.unpack_from("<I", m, 0)[0] != HEADER_MAGIC:
            raise ValueError(f"metadata copy {s}: bad magic")
        if hashlib.sha256(m[:12] + b"\0" * 32 + m[44:256]).digest() != m[12:44]:
            raise ValueError(f"metadata copy {s}: header checksum mismatch")
        tsz = struct.unpack_from("<I", m, 44)[0]
        tables = m[256:256 + tsz]
        if hashlib.sha256(tables).digest() != m[48:80]:
            raise ValueError(f"metadata copy {s}: tables checksum mismatch")
        if s == 0:
            po, pn, pe, eo, en, ee, go, gn, ge, bo, bn, be = struct.unpack_from("<12I", m, 80)
            exts = [struct.unpack_from("<QIQI", tables, eo + i * ee) for i in range(en)]
            grps = [tables[go + i * ge: go + i * ge + 36].rstrip(b"\0").decode() for i in range(gn)]
            parts = []
            for i in range(pn):
                e = tables[po + i * pe: po + (i + 1) * pe]
                name = e[:36].rstrip(b"\0").decode()
                _, fe, ne, gi = struct.unpack_from("<IIII", e, 36)
                size = sum(exts[fe + k][0] for k in range(ne)) * SECTOR
                start = exts[fe][2] * SECTOR if ne else 0
                parts.append((name, grps[gi], start, size))
            bsize = struct.unpack_from("<QIIQ", tables, bo)[3]
            result = {"partitions": parts, "groups": grps, "super_size": bsize, "copies": 2 * slots}
    return result


def write_sparse_piece(outfile, super_size, dev_off, length, chunks_iter):
    """Write a full-size sparse image of super with RAW data at [dev_off, dev_off+length)."""
    if dev_off % BLOCK or length % BLOCK:
        raise ValueError("piece not block aligned")
    total = super_size // BLOCK
    start = dev_off // BLOCK
    n = length // BLOCK
    chunks = []
    if start:
        chunks.append((CHUNK_DONT_CARE, start))
    chunks.append((CHUNK_RAW, n))
    if start + n < total:
        chunks.append((CHUNK_DONT_CARE, total - start - n))
    with open(outfile, "wb") as f:
        f.write(struct.pack("<IHHHHIIII", SPARSE_MAGIC, 1, 0, 28, 12, BLOCK, total, len(chunks), 0))
        for kind, blks in chunks:
            if kind == CHUNK_DONT_CARE:
                f.write(struct.pack("<HHII", kind, 0, blks, 12))
                continue
            f.write(struct.pack("<HHII", kind, 0, blks, 12 + blks * BLOCK))
            left = blks * BLOCK
            for buf in chunks_iter():
                f.write(buf)
                left -= len(buf)
            if left:
                raise IOError(f"short read while writing piece ({left} bytes missing)")


def file_reader(path, src_off, length, bufsize=16 * 1024 * 1024):
    def gen():
        with open(path, "rb") as f:
            f.seek(src_off)
            left = length
            while left:
                buf = f.read(min(bufsize, left))
                if not buf:
                    raise IOError(f"{path}: unexpected end of file")
                left -= len(buf)
                yield buf
    return gen


def write_sparse_image(outfile, super_size, regions):
    """Write ONE sparse image of the whole super: RAW data for each region, DONT_CARE in between.
    regions: list of (dev_off, length, chunks_iter). fastboot splits it to fit max-download-size itself."""
    regions = sorted(regions, key=lambda r: r[0])
    total = super_size // BLOCK
    chunks = []
    pos = 0
    for dev_off, length, it in regions:
        if dev_off % BLOCK or length % BLOCK:
            raise ValueError("region not block aligned")
        start = dev_off // BLOCK
        if start < pos:
            raise ValueError("regions overlap")
        if start > pos:
            chunks.append((CHUNK_DONT_CARE, start - pos, None))
        chunks.append((CHUNK_RAW, length // BLOCK, it))   # split into <=RAW_CHUNK_MAX pieces when written
        pos = start + length // BLOCK
    if pos > total:
        raise ValueError("regions run past the end of super")
    if pos < total:
        chunks.append((CHUNK_DONT_CARE, total - pos, None))
    # a chunk's total_sz is 32-bit, so one RAW chunk must stay under 4 GiB: split big regions
    step = RAW_CHUNK_MAX // BLOCK
    count = sum(-(-blks // step) if kind == CHUNK_RAW else 1 for kind, blks, _ in chunks)
    with open(outfile, "wb") as f:
        f.write(struct.pack("<IHHHHIIII", SPARSE_MAGIC, 1, 0, 28, 12, BLOCK, total, count, 0))
        for kind, blks, it in chunks:
            if kind == CHUNK_DONT_CARE:
                f.write(struct.pack("<HHII", kind, 0, blks, 12))
                continue
            stream = iter(it())
            pending = b""
            done = 0
            while done < blks:
                n = min(step, blks - done)
                f.write(struct.pack("<HHII", kind, 0, n, 12 + n * BLOCK))
                need = n * BLOCK
                while need:
                    if not pending:
                        pending = next(stream, b"")
                        if not pending:
                            raise IOError(f"short read while writing super image ({need} bytes missing)")
                    take = pending[:need]
                    f.write(take)
                    pending = pending[len(take):]
                    need -= len(take)
                done += n
            if pending or next(stream, b""):
                raise IOError("region source is longer than its partition")


def read_sparse_raw_chunks(path):
    """Yield (dev_off, length, file_off) for every RAW chunk of a sparse image (used to verify it)."""
    with open(path, "rb") as f:
        magic, _, _, fhs, chs, bs, total, nchunks, _ = struct.unpack("<IHHHHIIII", f.read(28))
        if magic != SPARSE_MAGIC or bs != BLOCK:
            raise ValueError("not a sparse image with 4 KiB blocks")
        f.seek(fhs)
        pos = 0
        for _ in range(nchunks):
            kind, _, blks, tsz = struct.unpack("<HHII", f.read(chs))
            if kind == CHUNK_RAW:
                yield pos * BLOCK, blks * BLOCK, f.tell()
                f.seek(blks * BLOCK, 1)
            elif kind != CHUNK_DONT_CARE:
                raise ValueError(f"unexpected chunk type {kind:#x}")
            pos += blks
        if pos != total:
            raise ValueError(f"sparse image covers {pos} blocks, header says {total}")


def sparse_region_sha256(path, chunks, off, size, bufsize=16 * 1024 * 1024):
    """sha256 of device bytes [off, off+size) of a sparse image; chunks = sorted read_sparse_raw_chunks().
    Raises if any part of the range is not covered by RAW data."""
    h = hashlib.sha256()
    pos = off
    with open(path, "rb") as f:
        for c_off, c_len, f_off in chunks:
            if c_off + c_len <= pos or c_off >= off + size:
                continue
            if c_off > pos:
                raise ValueError(f"gap in the image at {pos:#x}")
            f.seek(f_off + (pos - c_off))
            left = min(c_off + c_len, off + size) - pos
            while left:
                b = f.read(min(bufsize, left))
                if not b:
                    raise IOError("short read")
                h.update(b)
                left -= len(b)
                pos += len(b)
    if pos != off + size:
        raise ValueError(f"image ends early at {pos:#x}")
    return h.hexdigest()


def plan_pieces(layout, piece_max):
    """Split each partition into RAW pieces of at most piece_max bytes."""
    piece_max -= piece_max % BLOCK
    out = []
    for name, off, size in layout:
        pos = 0
        while pos < size:
            n = min(piece_max, size - pos)
            out.append((name, off + pos, pos, n))
            pos += n
    return out
