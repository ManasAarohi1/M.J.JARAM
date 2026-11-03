"""Sol's RNG button spots for any Roblox client size (boxed 800x600, small screens, ultrawide).

Roblox lays these menus out so a point captured as a fraction of one window size misses at another
shape. sol_points.npz holds measured centres of the buttons Auto SC + BR clicks, on a 32 px grid of
client sizes (made outside this repo); this module only interpolates between them.
"""
import os
import sys
from functools import lru_cache
from typing import Optional, Tuple

import numpy as np

LABELS = {
    "mainInterfaceInventory": "Inventory button (left bar)",
    "inventoryItemsTab": "Inventory: Items tab",
    "inventorySearchContent": "Inventory: search box",
    "inventoryInvItem[1,1]": "Inventory: first slot",
    "inventoryUseAmount": "Inventory: amount box",
    "inventoryUseButton": "Inventory: Use button",
    "inventoryInvClose": "Inventory: close (X)",
}

_TABLE = None


def _table():
    """(centres in pixels [w, h, button, xy], {name: index}, (w0, h0, step)); loaded once."""
    global _TABLE
    if _TABLE is None:
        here = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        with np.load(os.path.join(here, "sol_points.npz"), allow_pickle=False) as z:
            _TABLE = (z["centres2"].astype(np.float64) / 2.0,     # stored in half pixels
                      {str(n): i for i, n in enumerate(z["names"])},
                      tuple(int(v) for v in z["origin"]))
    return _TABLE


@lru_cache(maxsize=256)
def point(name: str, width: int, height: int) -> Optional[Tuple[float, float]]:
    """Centre of `name` in client pixels for a width x height client, or None if unknown / too small."""
    W, H = int(width), int(height)
    centres, index, (w0, h0, step) = _table()
    k = index.get(name)
    if k is None or W < w0 or H < h0:
        return None
    # bilinear between the four nearest measured sizes (linear past the largest one)
    i = min((W - w0) // step, centres.shape[0] - 2)
    j = min((H - h0) // step, centres.shape[1] - 2)
    tx, ty = (W - (w0 + i * step)) / step, (H - (h0 + j * step)) / step
    c = centres[i:i + 2, j:j + 2, k]
    x, y = (c[0, 0] * (1 - tx) * (1 - ty) + c[1, 0] * tx * (1 - ty)
            + c[0, 1] * (1 - tx) * ty + c[1, 1] * tx * ty)
    return float(x), float(y)


def fraction(name: str, width: int, height: int) -> Optional[Tuple[float, float]]:
    p = point(name, width, height)
    return None if p is None else (p[0] / width, p[1] / height)


if __name__ == "__main__":
    # Known centres at boxed, 1080p windowed, square and portrait clients.
    expect = {
        (784, 561): {"mainInterfaceInventory": (20.5, 262.5), "inventoryUseButton": (278.5, 298.5),
                     "inventoryInvItem[1,1]": (347.0, 257.0), "inventorySearchContent": (459.0, 211.0)},
        (1920, 1009): {"mainInterfaceInventory": (34.0, 478.0), "inventoryUseButton": (683.0, 544.5),
                       "inventoryInvItem[1,1]": (849.5, 442.5), "inventorySearchContent": (1122.0, 332.0)},
        (1000, 1000): {"mainInterfaceInventory": (22.5, 480.5), "inventoryUseButton": (356.0, 522.5),
                       "inventoryInvItem[1,1]": (443.0, 469.0), "inventorySearchContent": (585.0, 411.5)},
        (600, 900): {"mainInterfaceInventory": (17.5, 430.5), "inventoryUseButton": (214.0, 464.0),
                     "inventoryInvItem[1,1]": (266.0, 432.0), "inventorySearchContent": (351.5, 397.5)},
    }
    for (W, H), names in expect.items():
        for n, (ex, ey) in names.items():
            x, y = point(n, W, H)
            assert abs(x - ex) <= 2.0 and abs(y - ey) <= 2.0, (W, H, n, (x, y), (ex, ey))
    assert set(LABELS) == set(_table()[1]), "LABELS and sol_points.npz disagree"
    assert point("nope", 800, 600) is None and point("inventoryUseButton", 0, 600) is None
    assert point("inventoryUseButton", 200, 150) is None          # too small to use
    assert point("inventoryUseButton", 5120, 2880) is not None     # past the table: extrapolated
    print("sol_layout ok")
