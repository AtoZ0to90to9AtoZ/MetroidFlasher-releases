"""listimg - print every file/symlink in an ext4 image as: <prefix><path> <size> <mode> <uid>  (uses debugfs)."""
import subprocess
import sys


def walk(img, path, pre):
    out = subprocess.run(["debugfs", "-R", "ls -p " + (path or "/"), img], capture_output=True, text=True).stdout
    for line in out.splitlines():
        f = line.strip("/").split("/")
        if len(f) < 5 or f[4] in (".", ".."):
            continue
        mode, child = int(f[1], 8), f"{path}/{f[4]}"
        if mode & 0o170000 == 0o040000:
            walk(img, child, pre)
        else:
            print(f"{pre}{child} {f[5] if len(f) > 5 and f[5] else 0} {oct(mode & 0o7777)[2:]} {f[2]}")


if __name__ == "__main__":
    walk(sys.argv[1], "", sys.argv[2])
