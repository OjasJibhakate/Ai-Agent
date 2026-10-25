import numpy as np

from ragnarok.skills.screen.capture import WindowsScreen


def find_cat():
    frame = WindowsScreen((0, 900, 1920, 1040)).grab(max_width=4000)
    rgb = frame.rgb.astype(int)
    fur = (
        (abs(rgb[..., 0] - 0xF2) < 6)
        & (abs(rgb[..., 1] - 0xA6) < 6)
        & (abs(rgb[..., 2] - 0x5A) < 6)
    )
    ys, xs = np.nonzero(fur)
    return (int(xs.mean()), int(ys.mean()) + 900) if len(xs) > 30 else None
