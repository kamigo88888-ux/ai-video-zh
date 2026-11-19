"""Verify the Chinese overlay is present exactly on the English caption's frames.
usage: python3 tests/check_sync.py input.mp4 output.mp4"""
import sys
import numpy as np
sys.path.insert(0, '.')
from app.video import probe, iter_frames
src, out = sys.argv[1:3]
info = probe(src)
# Chinese regions (below captions) from the test video layout: (first, last, y0, y1)
CAPS = [(0, 74, 330, 420), (75, 149, 1530, 1700), (240, 299, 330, 420)]
a = {i: f for i, f in iter_frames(src, info)}
b = {i: f for i, f in iter_frames(out, info)}
ok = True
for s, e, y0, y1 in CAPS:
    for f in (s - 1, s, e, e + 1):
        if f < 0 or f >= len(a):
            continue
        d = np.abs(a[f][y0:y1].astype(int) - b[f][y0:y1].astype(int)).mean()
        expect = s <= f <= e
        # captions can be adjacent: the neighbour's Chinese may share the region
        good = (d > 8) == expect or any(s2 <= f <= e2 and (y0, y1) == (yy0, yy1) for s2, e2, yy0, yy1 in CAPS if (s2, e2) != (s, e))
        ok &= good
        print(f"caption {s}-{e} frame {f}: diff={d:5.1f} expect_overlay={expect} {'OK' if good else 'FAIL'}")
print("SYNC OK" if ok else "SYNC FAIL")
