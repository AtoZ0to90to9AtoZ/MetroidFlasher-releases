"""ext4write - build a fresh, read-only Android ext4 image from a file tree (pure Python, any OS).
Step 2 of the Windows GApps bake; the mirror of ext4read.py.

The tree normally comes straight out of a ROM image (Tree.from_image) - nothing is ever extracted to a
Windows folder, so case-sensitive names, symlinks, owners, modes, SELinux labels and file capabilities all
survive exactly. Files can then be removed and added, and write_image() lays out a new image sized to its
content (no stranded free space - every MiB saved is a MiB left in super).

Image layout = what Android ROM images use (crDroid's product/system_ext/system checked 2026-11-21):
4 KiB blocks, 256-byte inodes, extents, ext_attr, dir_index (linear directories), filetype, sparse_super,
large_file, huge_file, dir_nlink, extra_isize. No journal, no flex_bg, no metadata_csum, no uninit_bg -
read-only partitions need none of them. Timestamps are Android's fixed 2009-01-01.

Usage (self-test, rebuild an image unchanged and compare):
  py ext4write.py roundtrip IN.img OUT.img
"""
import hashlib
import os
import stat
import struct
import sys

import ext4read

BS = 4096
INODE_SIZE = 256
EXTRA_ISIZE = 32
BLOCKS_PER_GROUP = 32768
TIME = 1230768000                      # 2009-01-01 00:00:00 UTC, what Android's build tools stamp
FIRST_INO = 11
EXTENTS_FL = 0x80000
COMPAT = 0x8 | 0x20                    # ext_attr, dir_index
INCOMPAT = 0x2 | 0x40                  # filetype, extents
RO_COMPAT = 0x1 | 0x2 | 0x8 | 0x20 | 0x40   # sparse_super, large_file, huge_file, dir_nlink, extra_isize
XATTR_MAGIC = 0xEA020000
FTYPE = {stat.S_IFREG: 1, stat.S_IFDIR: 2, stat.S_IFCHR: 3, stat.S_IFBLK: 4, stat.S_IFIFO: 5,
         stat.S_IFSOCK: 6, stat.S_IFLNK: 7}
PREFIX_INDEX = [("security.", 6), ("user.", 1), ("trusted.", 4), ("system.", 7)]
MAX_EXT_LEN = 32768


class Node:
    """One path. mode includes the file-type bits. data: bytes, or a callable returning bytes, or None."""
    __slots__ = ("path", "mode", "uid", "gid", "xattrs", "data", "size", "target", "link_key", "ino")

    def __init__(self, path, mode, uid=0, gid=0, xattrs=None, data=None, size=0, target=None, link_key=None):
        self.path, self.mode, self.uid, self.gid = path, mode, uid, gid
        self.xattrs = dict(xattrs or {})
        self.data, self.size, self.target, self.link_key, self.ino = data, size, target, link_key, None


class Tree:
    def __init__(self):
        self.nodes = {}                 # path -> Node; "/" is the root
        self.volume_name = b""
        self.last_mounted = b""
        self.uuid = bytes(16)
        self.hash_seed = bytes(16)
        self.def_hash_version = 1       # half_md4
        self.sb_flags = 1               # signed_directory_hash

    # ---- building the tree ----
    @classmethod
    def from_image(cls, path):
        """Every entry of an existing image, with its data read lazily from that image when written."""
        t = cls()
        img = ext4read.Ext4Image(path)
        with open(path, "rb") as f:
            f.seek(1024)
            sb = f.read(1024)
        t.uuid, t.volume_name, t.last_mounted = sb[104:120], sb[120:136], sb[136:200]
        t.hash_seed, t.def_hash_version, t.sb_flags = sb[236:252], sb[252], struct.unpack_from("<I", sb, 352)[0]
        for p, n in img.walk():
            fmt = stat.S_IFMT(n.mode)
            node = Node(p, n.mode, n.uid, n.gid, n.xattrs, link_key=("src", n.ino) if fmt == stat.S_IFREG else None)
            if fmt == stat.S_IFREG:
                node.size = n.size
                node.data = (lambda img=img, n=n: img.read_data(n))
            elif fmt == stat.S_IFLNK:
                node.target = img.symlink_target(n).encode("utf-8", "surrogateescape")
            elif fmt in (stat.S_IFCHR, stat.S_IFBLK):
                raise ext4read.Ext4Error(f"{p}: device nodes are not expected in a ROM image")
            t.nodes[p] = node
        t._img = img                    # kept open for the lazy reads
        return t

    def remove(self, path):
        """Remove a path and everything under it. Returns how many entries went."""
        path = "/" + path.strip("/")
        gone = [p for p in self.nodes if p == path or p.startswith(path + "/")]
        for p in gone:
            del self.nodes[p]
        return len(gone)

    def add(self, node):
        if node.path in self.nodes:
            del self.nodes[node.path]
        parent = node.path.rsplit("/", 1)[0] or "/"
        if parent not in self.nodes:
            raise ValueError(f"{node.path}: parent {parent} does not exist")
        self.nodes[node.path] = node

    def children(self):
        kids = {p: [] for p, n in self.nodes.items() if stat.S_ISDIR(n.mode)}
        for p in self.nodes:
            if p != "/":
                parent = p.rsplit("/", 1)[0] or "/"
                kids[parent].append(p)
        for v in kids.values():
            v.sort()
        return kids


# ---------------------------------------------------------------- xattrs
def _xname(name):
    for pre, idx in PREFIX_INDEX:
        if name.startswith(pre):
            return idx, name[len(pre):].encode()
    raise ValueError(f"xattr {name}: unsupported prefix")


def _entry_hash(name, value):
    h = 0
    for c in name:
        h = ((h << 5) ^ (h >> 27) ^ c) & 0xFFFFFFFF
    padded = value + b"\0" * (-len(value) % 4)
    for (w,) in struct.iter_unpack("<I", padded):
        h = ((h << 16) ^ (h >> 16) ^ w) & 0xFFFFFFFF
    return h


def _pack_xattrs(xattrs, room, value_end, entries_start):
    """Entries (followed by 4 zero bytes) at the front, values packed from value_end down.
    Offsets are relative to entries_start's base as the caller defines. Returns (entries, values, hashes) or None."""
    items = sorted((_xname(k) + (v,) for k, v in xattrs.items()), key=lambda x: (x[0], len(x[1]), x[1]))
    ent = bytearray()
    vals = []
    vpos = value_end
    hashes = []
    for idx, name, value in items:
        vsize = (len(value) + 3) & ~3
        vpos -= vsize
        h = _entry_hash(name, value)
        hashes.append(h)
        e = struct.pack("<BBHIII", len(name), idx, vpos, 0, len(value), h) + name
        ent += e + b"\0" * (-len(e) % 4)
        vals.append((vpos, value))
    if entries_start + len(ent) + 4 > vpos:
        return None
    return bytes(ent), vals, hashes


def inode_xattr_area(xattrs):
    """The in-inode xattr area (bytes 160..255), or None if the xattrs don't fit there."""
    room = INODE_SIZE - 128 - EXTRA_ISIZE           # 96 bytes: magic + entries + values
    body = room - 4
    packed = _pack_xattrs(xattrs, body, body, 0)
    if packed is None:
        return None
    ent, vals, _ = packed
    area = bytearray(room)
    struct.pack_into("<I", area, 0, XATTR_MAGIC)
    area[4:4 + len(ent)] = ent
    for off, v in vals:
        area[4 + off:4 + off + len(v)] = v
    return bytes(area)


def xattr_block(xattrs):
    packed = _pack_xattrs(xattrs, BS, BS, 32)
    if packed is None:
        raise ValueError("xattrs too large for one block")
    ent, vals, hashes = packed
    blk = bytearray(BS)
    h = 0
    for eh in hashes:
        h = ((h << 16) ^ (h >> 16) ^ eh) & 0xFFFFFFFF
    struct.pack_into("<IIII", blk, 0, XATTR_MAGIC, 1, 1, h)   # magic, refcount (patched later), blocks, hash
    blk[32:32 + len(ent)] = ent
    for off, v in vals:
        blk[off:off + len(v)] = v
    return blk


# ---------------------------------------------------------------- directories
def dir_blocks(entries):
    """entries: [(name_bytes, ino, ftype)] incl. . and .. -> list of 4 KiB blocks, linear format."""
    blocks, cur, used = [], [], 0
    for name, ino, ft in entries:
        need = (8 + len(name) + 3) & ~3
        if used + need > BS:
            blocks.append(cur)
            cur, used = [], 0
        cur.append((name, ino, ft, need))
        used += need
    blocks.append(cur)
    out = []
    for blk in blocks:
        b = bytearray(BS)
        off = 0
        for i, (name, ino, ft, need) in enumerate(blk):
            rec = need if i < len(blk) - 1 else BS - off
            struct.pack_into("<IHBB", b, off, ino, rec, len(name), ft)
            b[off + 8:off + 8 + len(name)] = name
            off += rec
        out.append(bytes(b))
    return out


# ---------------------------------------------------------------- layout
def _sparse_group(g):
    if g in (0, 1):
        return True
    for p in (3, 5, 7):
        x = p
        while x < g:
            x *= p
        if x == g:
            return True
    return False


class Layout:
    def __init__(self, data_blocks, n_inodes):
        """Smallest layout holding data_blocks data blocks and n_inodes inodes."""
        total = data_blocks + 64
        while True:
            groups = (total + BLOCKS_PER_GROUP - 1) // BLOCKS_PER_GROUP
            ipg = -(-max(n_inodes + 16, FIRST_INO + 1) // groups)
            ipg = (ipg + 15) // 16 * 16                     # whole inode-table blocks
            itb = ipg * INODE_SIZE // BS
            gdt = -(-(groups * 32) // BS)
            meta = sum((1 + gdt if _sparse_group(g) else 0) + 2 + itb for g in range(groups))
            need = data_blocks + meta
            if need <= total:
                # the last group must hold at least its own metadata plus one block
                last_start = (groups - 1) * BLOCKS_PER_GROUP
                last_meta = (1 + gdt if _sparse_group(groups - 1) else 0) + 2 + itb
                total = max(need, last_start + last_meta + 1)
                if (total + BLOCKS_PER_GROUP - 1) // BLOCKS_PER_GROUP == groups:
                    break
            total = need
        self.blocks, self.groups, self.ipg, self.itb, self.gdt = total, groups, ipg, itb, gdt
        self.inodes = ipg * groups
        self.group_meta = []                                # (start, bitmap, ibitmap, itable, first data block)
        for g in range(groups):
            s = g * BLOCKS_PER_GROUP
            b = s + (1 + gdt if _sparse_group(g) else 0)
            self.group_meta.append((s, b, b + 1, b + 2, b + 2 + itb))
        self.free = []                                      # data runs (start, length) in order
        for g in range(groups):
            first = self.group_meta[g][4]
            end = min((g + 1) * BLOCKS_PER_GROUP, total)
            if end > first:
                self.free.append([first, end - first])

    def alloc(self, n):
        """n blocks as a list of (start, length) runs, taken in order."""
        runs = []
        while n:
            if not self.free:
                raise RuntimeError("layout ran out of blocks (sizing bug)")
            r = self.free[0]
            take = min(n, r[1])
            runs.append((r[0], take))
            r[0] += take
            r[1] -= take
            n -= take
            if not r[1]:
                self.free.pop(0)
        return runs


def _extents(runs):
    out, lblk = [], 0
    for start, ln in runs:
        while ln:
            take = min(ln, MAX_EXT_LEN)
            out.append((lblk, start, take))
            lblk += take
            start += take
            ln -= take
    return out


def _ext_node(entries, depth, maxe):
    b = struct.pack("<HHHHI", 0xF30A, len(entries), maxe, depth, 0)
    for e in entries:
        if depth == 0:
            lblk, pblk, ln = e
            b += struct.pack("<IHHI", lblk, ln, pblk >> 32, pblk & 0xFFFFFFFF)
        else:
            lblk, leaf = e
            b += struct.pack("<IIHH", lblk, leaf & 0xFFFFFFFF, leaf >> 32, 0)
    return b


# ---------------------------------------------------------------- writer
def write_image(tree, out_path, log=print):
    nodes = tree.nodes
    kids = tree.children()
    # 1. inode numbers: root 2, lost+found 11 if there is one, the rest from 11/12; hard links share one
    order = sorted(nodes)                       # parents sort before children
    nxt = FIRST_INO
    if "/lost+found" in nodes:
        nodes["/lost+found"].ino = FIRST_INO
        nxt += 1
    nodes["/"].ino = 2
    shared = {}
    inodes = {2: nodes["/"]}
    links = {}
    for p in order:
        n = nodes[p]
        if n.ino is None:
            if n.link_key is not None and n.link_key in shared:
                n.ino = shared[n.link_key]
            else:
                n.ino = nxt
                nxt += 1
                if n.link_key is not None:
                    shared[n.link_key] = n.ino
        inodes.setdefault(n.ino, n)
        links[n.ino] = links.get(n.ino, 0) + 1
    for p, n in nodes.items():                  # directory link counts: 2 + subdirectories
        if stat.S_ISDIR(n.mode):
            links[n.ino] = 2 + sum(1 for c in kids[p] if stat.S_ISDIR(nodes[c].mode))
    n_inodes = nxt - 1

    # 2. contents and block counts
    dirdata = {}
    for p, n in nodes.items():
        if stat.S_ISDIR(n.mode):
            parent = p.rsplit("/", 1)[0] or "/"
            ents = [(b".", n.ino, 2), (b"..", nodes[parent].ino, 2)]
            for c in kids[p]:
                cn = nodes[c]
                ents.append((c.rsplit("/", 1)[1].encode("utf-8", "surrogateescape"), cn.ino,
                             FTYPE[stat.S_IFMT(cn.mode)]))
            dirdata[n.ino] = dir_blocks(ents)
    xblocks = {}                                # xattr set -> block index (shared, refcounted)
    xarea = {}
    need = {}
    for ino, n in inodes.items():
        fmt = stat.S_IFMT(n.mode)
        if fmt == stat.S_IFDIR:
            nb = len(dirdata[ino])
        elif fmt == stat.S_IFREG:
            nb = -(-n.size // BS)
        elif fmt == stat.S_IFLNK:
            nb = 0 if len(n.target) < 60 else -(-len(n.target) // BS)
        else:
            nb = 0
        need[ino] = nb
        area = inode_xattr_area(n.xattrs) if n.xattrs else None
        if n.xattrs and area is None:
            key = tuple(sorted(n.xattrs.items()))
            xblocks.setdefault(key, None)
        xarea[ino] = area
    data_blocks = sum(need.values()) + len(xblocks)
    # extent leaf blocks: allocation is contiguous apart from group metadata (< 600 blocks a group), so a
    # file needs at most nb / (32768 - 600) + 2 extents; reserve leaf blocks where that may pass 4
    def max_extents(nb):
        return nb // max(MAX_EXT_LEN - 600, 1) + 2
    data_blocks += sum(-(-max_extents(nb) // 340) for nb in need.values() if nb and max_extents(nb) > 4)
    lay = Layout(data_blocks, n_inodes)
    log(f"  {len(nodes)} entries, {n_inodes} inodes, {data_blocks} data blocks -> "
        f"{lay.blocks} blocks ({lay.blocks * BS / 2**20:.1f} MiB), {lay.groups} groups, {lay.ipg} inodes/group")

    # 3. allocate: xattr blocks first, then each inode's data (and extent leaves) in inode order
    for key in xblocks:
        xblocks[key] = lay.alloc(1)[0][0]
    runs = {}
    leaves = {}
    for ino in sorted(inodes):
        if need[ino]:
            runs[ino] = lay.alloc(need[ino])
            ext = _extents(runs[ino])
            if len(ext) > 4:
                nleaf = -(-len(ext) // 340)
                leaves[ino] = [s + i for s, l in lay.alloc(nleaf) for i in range(l)]
    used_blocks = lay.blocks - sum(r[1] for r in lay.free)

    # 4. write
    refcount = {}
    for ino, n in inodes.items():
        if n.xattrs and xarea[ino] is None:
            k = tuple(sorted(n.xattrs.items()))
            refcount[k] = refcount.get(k, 0) + 1
    with open(out_path, "wb") as f:
        f.truncate(lay.blocks * BS)

        def put(blk, data):
            f.seek(blk * BS)
            f.write(data)

        # data
        for ino in sorted(inodes):
            n = inodes[ino]
            if ino not in runs:
                continue
            fmt = stat.S_IFMT(n.mode)
            if fmt == stat.S_IFDIR:
                content = b"".join(dirdata[ino])
            elif fmt == stat.S_IFREG:
                content = n.data() if callable(n.data) else (n.data or b"")
                if len(content) != n.size:
                    raise RuntimeError(f"{n.path}: expected {n.size} bytes, got {len(content)}")
            else:
                content = n.target
            pos = 0
            for start, ln in runs[ino]:
                chunk = content[pos:pos + ln * BS]
                put(start, chunk)
                pos += ln * BS
        for key, blk in xblocks.items():
            b = xattr_block(dict(key))
            struct.pack_into("<I", b, 4, refcount[key])
            put(blk, bytes(b))

        # inode tables
        itables = [bytearray(lay.itb * BS) for _ in range(lay.groups)]
        dirs_in_group = [0] * lay.groups
        for ino, n in inodes.items():
            g, idx = divmod(ino - 1, lay.ipg)
            raw = bytearray(INODE_SIZE)
            fmt = stat.S_IFMT(n.mode)
            nblocks = need[ino] + len(leaves.get(ino, []))
            size = len(b"".join(dirdata[ino])) if fmt == stat.S_IFDIR else \
                n.size if fmt == stat.S_IFREG else len(n.target) if fmt == stat.S_IFLNK else 0
            acl = 0
            if n.xattrs and xarea[ino] is None:
                acl = xblocks[tuple(sorted(n.xattrs.items()))]
                nblocks += 1
            flags = 0
            iblock = bytes(60)
            if fmt == stat.S_IFLNK and len(n.target) < 60:
                iblock = n.target.ljust(60, b"\0")
            elif fmt in (stat.S_IFREG, stat.S_IFDIR, stat.S_IFLNK):
                flags = EXTENTS_FL
                ext = _extents(runs.get(ino, []))
                if len(ext) <= 4:
                    iblock = _ext_node(ext, 0, 4).ljust(60, b"\0")
                else:
                    idx_entries = []
                    for i, leaf in enumerate(leaves[ino]):
                        part = ext[i * 340:(i + 1) * 340]
                        put(leaf, _ext_node(part, 0, 340).ljust(BS, b"\0"))
                        idx_entries.append((part[0][0], leaf))
                    iblock = _ext_node(idx_entries, 1, 4).ljust(60, b"\0")
            if fmt == stat.S_IFDIR:
                dirs_in_group[g] += 1
            struct.pack_into("<HHIIIIIHHII", raw, 0, n.mode, n.uid & 0xFFFF, size & 0xFFFFFFFF,
                             TIME, TIME, TIME, 0, n.gid & 0xFFFF, links[ino], nblocks * (BS // 512) & 0xFFFFFFFF,
                             flags)
            raw[40:100] = iblock
            struct.pack_into("<IIII", raw, 100, 0, acl & 0xFFFFFFFF, size >> 32, 0)
            struct.pack_into("<HHHH", raw, 116, (nblocks * (BS // 512)) >> 32, acl >> 32, n.uid >> 16, n.gid >> 16)
            struct.pack_into("<HH", raw, 128, EXTRA_ISIZE, 0)
            struct.pack_into("<I", raw, 144, TIME)          # crtime
            if xarea[ino] is not None:
                raw[128 + EXTRA_ISIZE:] = xarea[ino]
            itables[g][idx * INODE_SIZE:(idx + 1) * INODE_SIZE] = raw

        # bitmaps, group descriptors
        used = bytearray(lay.blocks // 8 + 1)               # whole-image block bitmap
        def mark(start, ln):
            for b in range(start, start + ln):
                used[b >> 3] |= 1 << (b & 7)
        for g, (s, b, ib, it, first) in enumerate(lay.group_meta):
            mark(s, first - s)
        for r in runs.values():
            for start, ln in r:
                mark(start, ln)
        for lv in leaves.values():
            for blk in lv:
                mark(blk, 1)
        for blk in xblocks.values():
            mark(blk, 1)
        free_blocks_total = 0
        gdt = bytearray(lay.gdt * BS)
        free_inodes_total = 0
        for g, (s, b, ib, it, first) in enumerate(lay.group_meta):
            end = min(s + BLOCKS_PER_GROUP, lay.blocks)
            bm = bytearray(BS)
            free_b = 0
            for i in range(BLOCKS_PER_GROUP):
                blk = s + i
                if blk >= end or used[blk >> 3] >> (blk & 7) & 1:
                    bm[i >> 3] |= 1 << (i & 7)
                else:
                    free_b += 1
            put(b, bytes(bm))
            im = bytearray(BS)
            used_i = 0
            for i in range(lay.ipg):
                ino = g * lay.ipg + i + 1
                if ino < FIRST_INO or ino in inodes:
                    im[i >> 3] |= 1 << (i & 7)
                    used_i += 1
            for i in range(lay.ipg, BS * 8):
                im[i >> 3] |= 1 << (i & 7)
            put(ib, bytes(im))
            put(it, bytes(itables[g]))
            free_i = lay.ipg - used_i
            struct.pack_into("<IIIHHH", gdt, g * 32, b, ib, it, free_b, free_i, dirs_in_group[g])
            free_blocks_total += free_b
            free_inodes_total += free_i

        # superblock (+ backups with the group descriptors in sparse groups)
        sb = bytearray(1024)
        # 0: inodes, blocks, r_blocks, free_blocks, free_inodes, first_data_block, log_block_size,
        #    log_cluster_size, blocks/group, clusters/group, inodes/group, mtime, wtime
        # 52: mnt_count, max_mnt_count(-1), magic, state(clean), errors(continue), minor_rev
        # 64: lastcheck, checkinterval, creator_os, rev_level(dynamic)   80: def_resuid, def_resgid
        struct.pack_into("<13I", sb, 0, lay.inodes, lay.blocks, 0, free_blocks_total, free_inodes_total, 0, 2, 2,
                         BLOCKS_PER_GROUP, BLOCKS_PER_GROUP, lay.ipg, TIME, TIME)
        struct.pack_into("<HhHHHH", sb, 52, 0, -1, 0xEF53, 1, 1, 0)
        struct.pack_into("<4IHH", sb, 64, TIME, 0, 0, 1, 0, 0)
        struct.pack_into("<IHHIII", sb, 84, FIRST_INO, INODE_SIZE, 0, COMPAT, INCOMPAT, RO_COMPAT)
        sb[104:120] = tree.uuid
        sb[120:136] = tree.volume_name.ljust(16, b"\0")[:16]
        sb[136:200] = tree.last_mounted.ljust(64, b"\0")[:64]
        sb[236:252] = tree.hash_seed
        sb[252] = tree.def_hash_version
        struct.pack_into("<I", sb, 264, TIME)               # mkfs_time
        struct.pack_into("<HHI", sb, 348, EXTRA_ISIZE, EXTRA_ISIZE, tree.sb_flags)
        for g, (s, *_rest) in enumerate(lay.group_meta):
            if not _sparse_group(g):
                continue
            struct.pack_into("<H", sb, 90, g)
            f.seek(s * BS + (1024 if g == 0 else 0))
            f.write(sb)
            put(s + 1, bytes(gdt))
    log(f"  wrote {out_path}: {used_blocks} blocks used, {free_blocks_total} free")
    return lay


# ---------------------------------------------------------------- self-test
def roundtrip(src, dst):
    t = Tree.from_image(src)
    write_image(t, dst)
    t._img.close()
    a = ext4read.Ext4Image(src)
    b = ext4read.Ext4Image(dst)
    diffs = 0
    la = {p: n for p, n in a.walk()}
    lb = {p: n for p, n in b.walk()}
    if set(la) != set(lb):
        print("PATHS DIFFER:", sorted(set(la) ^ set(lb))[:20])
        diffs += 1
    for p in sorted(set(la) & set(lb)):
        x, y = la[p], lb[p]
        if (x.mode, x.uid, x.gid, x.xattrs) != (y.mode, y.uid, y.gid, y.xattrs):
            if diffs < 20:
                print("META DIFFERS:", p, (x.mode, x.uid, x.gid, x.xattrs), (y.mode, y.uid, y.gid, y.xattrs))
            diffs += 1
        if stat.S_ISREG(x.mode):
            if x.size != y.size or hashlib.sha256(a.read_data(x)).digest() != hashlib.sha256(b.read_data(y)).digest():
                print("DATA DIFFERS:", p)
                diffs += 1
        elif stat.S_ISLNK(x.mode) and a.symlink_target(x) != b.symlink_target(y):
            print("LINK DIFFERS:", p)
            diffs += 1
    print(f"roundtrip: {len(la)} entries compared, {diffs} differences",
          f"({os.path.getsize(src) / 2**20:.1f} MiB -> {os.path.getsize(dst) / 2**20:.1f} MiB)")
    return diffs == 0


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "roundtrip":
        sys.exit(0 if roundtrip(sys.argv[2], sys.argv[3]) else 1)
    print(__doc__)
    sys.exit(2)
