"""ext4read - read an Android ext4 image on any OS (pure Python, read-only). Step 1 of the Windows GApps bake.

Lists every file with what a rebuild must keep exactly: path, type, mode, uid, gid, size, SELinux label,
file capabilities, symlink target. Can also extract file data, and write the two lists the Android image
tools take back in (fs_config and file_contexts).

Covers what Android ROM images use: extents, file types, htree directories (read linearly), in-inode and
block xattrs, fast and slow symlinks, hard links. Refuses images with features it does not handle
(64bit descriptors are handled; inline_data and encryption are refused) instead of guessing.

Usage:
  py ext4read.py list    IMAGE              -> one line per entry (tab separated)
  py ext4read.py extract IMAGE OUTDIR       -> files + OUTDIR.fs_config + OUTDIR.file_contexts
"""
import os
import stat
import struct
import sys

EXT4_MAGIC = 0xEF53
SPARSE_MAGIC = 0xED26FF3A
INCOMPAT_FILETYPE = 0x2
INCOMPAT_EXTENTS = 0x40
INCOMPAT_64BIT = 0x80
INCOMPAT_FLEX_BG = 0x200
INCOMPAT_INLINE_DATA = 0x8000
INCOMPAT_ENCRYPT = 0x10000
INCOMPAT_OK = INCOMPAT_FILETYPE | INCOMPAT_EXTENTS | INCOMPAT_64BIT | INCOMPAT_FLEX_BG | 0x400 | 0x1000
# 0x400 = mmp is not used by Android; 0x1000 = large_dir (only bigger dir blocks, read linearly is fine)
EXTENTS_FL = 0x80000
XATTR_MAGIC = 0xEA020000
XATTR_PREFIX = {1: "user.", 2: "system.posix_acl_access", 3: "system.posix_acl_default", 4: "trusted.",
                6: "security.", 7: "system.", 8: "system.richacl"}


class Ext4Error(Exception):
    pass


class Inode:
    __slots__ = ("ino", "mode", "uid", "gid", "size", "flags", "block", "xattrs", "links")


class Ext4Image:
    def __init__(self, path):
        self.path = path
        self.f = open(path, "rb")
        if struct.unpack("<I", self.f.read(4))[0] == SPARSE_MAGIC:
            raise Ext4Error(f"{path} is a sparse image - unsparse it first (simg2img)")
        self.f.seek(1024)
        sb = self.f.read(1024)
        if struct.unpack_from("<H", sb, 56)[0] != EXT4_MAGIC:
            raise Ext4Error(f"{path} is not ext4")
        self.block_size = 1024 << struct.unpack_from("<I", sb, 24)[0]
        self.first_data_block = struct.unpack_from("<I", sb, 20)[0]
        self.blocks_per_group = struct.unpack_from("<I", sb, 32)[0]
        self.inodes_per_group = struct.unpack_from("<I", sb, 40)[0]
        self.inode_size = struct.unpack_from("<H", sb, 88)[0]
        self.incompat = struct.unpack_from("<I", sb, 96)[0]
        bad = self.incompat & ~INCOMPAT_OK
        if bad:
            raise Ext4Error(f"{path}: unsupported ext4 features {bad:#x} (inline_data/encrypt/other) - refusing")
        self.is64 = bool(self.incompat & INCOMPAT_64BIT)
        self.desc_size = struct.unpack_from("<H", sb, 254)[0] if self.is64 else 32
        inodes_count = struct.unpack_from("<I", sb, 0)[0]
        self.groups = (inodes_count + self.inodes_per_group - 1) // self.inodes_per_group
        gdt = self._read_block(self.first_data_block + 1, (self.groups * self.desc_size + self.block_size - 1)
                               // self.block_size)
        self.inode_tables = []
        for g in range(self.groups):
            d = gdt[g * self.desc_size:(g + 1) * self.desc_size]
            lo = struct.unpack_from("<I", d, 8)[0]
            hi = struct.unpack_from("<I", d, 0x28)[0] if self.is64 and self.desc_size >= 64 else 0
            self.inode_tables.append(lo | (hi << 32))

    def close(self):
        self.f.close()

    # ---- low level ----
    def _read_block(self, blk, count=1):
        self.f.seek(blk * self.block_size)
        data = self.f.read(count * self.block_size)
        if len(data) != count * self.block_size:
            raise Ext4Error(f"short read at block {blk}")
        return data

    def inode(self, ino):
        g, idx = divmod(ino - 1, self.inodes_per_group)
        self.f.seek(self.inode_tables[g] * self.block_size + idx * self.inode_size)
        raw = self.f.read(self.inode_size)
        n = Inode()
        n.ino = ino
        n.mode = struct.unpack_from("<H", raw, 0)[0]
        n.uid = struct.unpack_from("<H", raw, 2)[0] | (struct.unpack_from("<H", raw, 120)[0] << 16)
        n.gid = struct.unpack_from("<H", raw, 24)[0] | (struct.unpack_from("<H", raw, 122)[0] << 16)
        n.size = struct.unpack_from("<I", raw, 4)[0] | (struct.unpack_from("<I", raw, 108)[0] << 32)
        n.links = struct.unpack_from("<H", raw, 26)[0]
        n.flags = struct.unpack_from("<I", raw, 32)[0]
        n.block = raw[40:100]
        acl = struct.unpack_from("<I", raw, 104)[0] | (struct.unpack_from("<H", raw, 118)[0] << 32)
        n.xattrs = {}
        if self.inode_size > 128:
            extra = struct.unpack_from("<H", raw, 128)[0]
            start = 128 + extra
            if start + 4 <= len(raw) and struct.unpack_from("<I", raw, start)[0] == XATTR_MAGIC:
                body = raw[start + 4:]
                self._parse_xattrs(body, 0, body, n.xattrs)
        if acl:
            blk = self._read_block(acl)
            if struct.unpack_from("<I", blk, 0)[0] != XATTR_MAGIC:
                raise Ext4Error(f"inode {ino}: bad xattr block {acl}")
            self._parse_xattrs(blk, 32, blk, n.xattrs)
        return n

    @staticmethod
    def _parse_xattrs(buf, off, value_base, out):
        while off + 4 <= len(buf):
            if buf[off:off + 4] == b"\0\0\0\0":
                break   # end of list = 4 zero bytes (what follows may be garbage; the kernel stops here too)
            if off + 16 > len(buf):
                break
            name_len, idx, voff, vinum, vsize = struct.unpack_from("<BBHII", buf, off)
            name = buf[off + 16:off + 16 + name_len].decode("latin-1")
            if vinum:
                raise Ext4Error("xattr value stored in a separate inode (ea_inode) - not supported")
            out[XATTR_PREFIX.get(idx, f"?{idx}.") + name] = bytes(value_base[voff:voff + vsize])
            off += (16 + name_len + 3) & ~3

    def _extents(self, node):
        """Yield (logical_block, physical_block, length, initialized) for an extent tree node (60 or block bytes)."""
        magic, entries, _max, depth = struct.unpack_from("<HHHH", node, 0)
        if magic != 0xF30A:
            raise Ext4Error("bad extent header")
        for i in range(entries):
            e = node[12 + i * 12: 24 + i * 12]
            if depth == 0:
                lblk, ln, hi, lo = struct.unpack("<IHHI", e)
                init = ln <= 32768
                yield lblk, lo | (hi << 32), ln if init else ln - 32768, init
            else:
                lblk, lo, hi = struct.unpack_from("<IIH", e)
                yield from self._extents(self._read_block(lo | (hi << 32)))

    def read_data(self, n):
        """Whole contents of a regular file / directory / slow symlink."""
        if not n.flags & EXTENTS_FL:
            raise Ext4Error(f"inode {n.ino}: block-mapped (non-extent) file - not supported")
        out = bytearray(n.size)
        bs = self.block_size
        for lblk, pblk, ln, init in self._extents(n.block):
            start = lblk * bs
            if start >= n.size:
                continue
            want = min(ln * bs, n.size - start)
            if init:
                self.f.seek(pblk * bs)
                out[start:start + want] = self.f.read(want)
            # uninitialized extents read as zeros (already zero)
        return bytes(out)

    def symlink_target(self, n):
        if n.size < 60 and not n.flags & EXTENTS_FL:
            return n.block[:n.size].decode("utf-8", "surrogateescape")
        return self.read_data(n).decode("utf-8", "surrogateescape")

    def readdir(self, n):
        data = self.read_data(n)
        bs, off, out = self.block_size, 0, []
        while off < len(data):
            ino, rec_len, name_len, ftype = struct.unpack_from("<IHBB", data, off)
            if rec_len < 8:
                raise Ext4Error(f"dir inode {n.ino}: bad rec_len at {off}")
            if ino and name_len:
                name = data[off + 8:off + 8 + name_len].decode("utf-8", "surrogateescape")
                if name not in (".", ".."):
                    out.append((name, ino))
            off += rec_len
            if off % bs and off // bs != (off - rec_len) // bs:
                raise Ext4Error(f"dir inode {n.ino}: entry crosses a block")
        return out

    # ---- high level ----
    def walk(self):
        """Yield (path, Inode) for every entry, depth first, parents before children. Root is '/'."""
        root = self.inode(2)
        yield "/", root
        stack = [("", root)]
        while stack:
            path, d = stack.pop()
            for name, ino in sorted(self.readdir(d), reverse=True):
                n = self.inode(ino)
                child = f"{path}/{name}"
                yield child, n
                if stat.S_ISDIR(n.mode):
                    stack.append((child, n))


def kind(mode):
    return {stat.S_IFREG: "f", stat.S_IFDIR: "d", stat.S_IFLNK: "l", stat.S_IFCHR: "c", stat.S_IFBLK: "b",
            stat.S_IFIFO: "p", stat.S_IFSOCK: "s"}.get(stat.S_IFMT(mode), "?")


def label(n):
    v = n.xattrs.get("security.selinux", b"")
    return v.rstrip(b"\0").decode("latin-1")


def caps(n):
    v = n.xattrs.get("security.capability")
    if not v or len(v) < 12:
        return 0
    # vfs_cap_data: magic_etc, permitted lo, inheritable lo, [permitted hi, inheritable hi]
    words = struct.unpack_from("<%dI" % (len(v) // 4), v)
    lo = words[1]
    hi = words[3] if len(words) >= 4 else 0
    return lo | (hi << 32)


def list_image(path, out=sys.stdout):
    img = Ext4Image(path)
    try:
        for p, n in img.walk():
            tgt = img.symlink_target(n) if stat.S_ISLNK(n.mode) else ""
            size = n.size if stat.S_ISREG(n.mode) or stat.S_ISLNK(n.mode) else 0
            out.write(f"{p}\t{kind(n.mode)}\t{stat.S_IMODE(n.mode):o}\t{n.uid}\t{n.gid}\t{size}\t"
                      f"{label(n)}\t{caps(n):#x}\t{tgt}\n")
    finally:
        img.close()


def extract(path, outdir, mount_point):
    """Write files under outdir, plus outdir.fs_config and outdir.file_contexts (the Android image tools'
    input format). mount_point is the partition name, e.g. 'product' (paths in the lists start with it)."""
    img = Ext4Image(path)
    os.makedirs(outdir, exist_ok=True)
    fsc, ctx = [], []
    try:
        for p, n in img.walk():
            rel = p.lstrip("/")
            dest = os.path.join(outdir, *rel.split("/")) if rel else outdir
            name = f"{mount_point}/{rel}" if rel else mount_point
            fsc.append(f"{name} {n.uid} {n.gid} {stat.S_IMODE(n.mode):04o} capabilities={caps(n):#x}")
            esc = "/" + name
            for ch in ".^$*+?()[]{}|":
                esc = esc.replace(ch, "\\" + ch)
            ctx.append(f"{esc} {label(n)}")
            if stat.S_ISDIR(n.mode):
                os.makedirs(dest, exist_ok=True)
            elif stat.S_ISREG(n.mode):
                with open(dest, "wb") as f:
                    f.write(img.read_data(n))
            elif stat.S_ISLNK(n.mode):
                with open(dest + ".symlink", "w", encoding="utf-8", errors="surrogateescape") as f:
                    f.write(img.symlink_target(n))   # Windows can't hold real symlinks reliably; kept as text
    finally:
        img.close()
    with open(outdir.rstrip("\\/") + ".fs_config", "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(fsc) + "\n")
    with open(outdir.rstrip("\\/") + ".file_contexts", "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(ctx) + "\n")


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "list":
        list_image(sys.argv[2])
    elif len(sys.argv) >= 4 and sys.argv[1] == "extract":
        extract(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else
                os.path.splitext(os.path.basename(sys.argv[2]))[0])
    else:
        print(__doc__)
        sys.exit(2)
