"""Run every MetroidFlasher test. No phone needed; the real fastboot is never started.

    C:\\Python315\\python.exe -I run_tests.py          all tests
    C:\\Python315\\python.exe -I run_tests.py -q       quieter
    C:\\Python315\\python.exe -I run_tests.py --fast   skip the slower end-to-end and real-image runs
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def main():
    fast = "--fast" in sys.argv
    verbosity = 1 if "-q" in sys.argv else 2
    names = ["test_lpsuper", "test_image", "test_classify"]
    if not fast:
        names += ["test_flash_e2e", "test_real_dryrun"]
    suite = unittest.defaultTestLoader.loadTestsFromNames(names)
    result = unittest.TextTestRunner(verbosity=verbosity).run(suite)
    print()
    print("ALL TESTS PASSED" if result.wasSuccessful() else "SOME TESTS FAILED")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
