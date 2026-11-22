"""cmpimg - compare two ext4 images entry by entry: paths, type/mode, owner, xattrs (SELinux, caps), data.
    py cmpimg.py A.img B.img      -> exit 0 when identical in content (layout/size may differ)"""
import hashlib
import stat
import sys

import ext4read


def digest(img, n):
    return hashlib.sha256(img.read_data(n)).hexdigest()


def compare(pa, pb, show=25):
    a, b = ext4read.Ext4Image(pa), ext4read.Ext4Image(pb)
    la, lb = dict(a.walk()), dict(b.walk())
    diffs = []
    for p in sorted(set(la) - set(lb)):
        diffs.append(f"only in A: {p}")
    for p in sorted(set(lb) - set(la)):
        diffs.append(f"only in B: {p}")
    for p in sorted(set(la) & set(lb)):
        x, y = la[p], lb[p]
        if (x.mode, x.uid, x.gid) != (y.mode, y.uid, y.gid):
            diffs.append(f"meta {p}: {x.mode:o} {x.uid}:{x.gid} vs {y.mode:o} {y.uid}:{y.gid}")
        if x.xattrs != y.xattrs:
            diffs.append(f"xattrs {p}: {x.xattrs} vs {y.xattrs}")
        if stat.S_ISREG(x.mode) and (x.size != y.size or digest(a, x) != digest(b, y)):
            diffs.append(f"data {p}: {x.size} vs {y.size} bytes")
        if stat.S_ISLNK(x.mode) and a.symlink_target(x) != b.symlink_target(y):
            diffs.append(f"link {p}")
    for d in diffs[:show]:
        print("  " + d)
    print(f"{len(la)} vs {len(lb)} entries, {len(diffs)} differences")
    return not diffs


if __name__ == "__main__":
    sys.exit(0 if compare(sys.argv[1], sys.argv[2]) else 1)
