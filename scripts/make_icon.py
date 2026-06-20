"""Generate a modern, bold app icon (.ico) for Teacher Hub.

Design goals:
- High contrast so it reads at 16×16 in the taskbar.
- Vibrant indigo→violet gradient square with strong rounded corners.
- Bold white graduation cap silhouette as the dominant element.
- Amber tassel as the accent.
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parent.parent / "app" / "resources" / "icons" / "app.ico"
OUT.parent.mkdir(parents=True, exist_ok=True)

SIZE = 512

# Vibrant indigo → violet gradient
GRAD_TOP = (99, 102, 241)    # #6366F1 indigo-500
GRAD_BOT = (139, 92, 246)    # #8B5CF6 violet-500
AMBER = (251, 191, 36)       # #FBBF24
AMBER_DARK = (217, 119, 6)   # #D97706
WHITE = (255, 255, 255)
SHADOW = (30, 27, 75, 90)    # dark indigo shadow


def _vertical_gradient(size: int, top_color, bottom_color) -> Image.Image:
    """Create a vertical linear gradient image."""
    grad = Image.new("RGB", (1, size), color=0)
    for y in range(size):
        t = y / (size - 1)
        r = int(top_color[0] * (1 - t) + bottom_color[0] * t)
        g = int(top_color[1] * (1 - t) + bottom_color[1] * t)
        b = int(top_color[2] * (1 - t) + bottom_color[2] * t)
        grad.putpixel((0, y), (r, g, b))
    return grad.resize((size, size), Image.NEAREST)


def make_image(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))

    # ---- 1) Rounded gradient background ----
    radius = int(size * 0.24)
    bg_grad = _vertical_gradient(size, GRAD_TOP, GRAD_BOT).convert("RGBA")
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, size - 1, size - 1), radius=radius, fill=255
    )
    img.paste(bg_grad, (0, 0), mask)

    d = ImageDraw.Draw(img)

    # ---- 2) Diagonal gloss highlight (subtle) ----
    gloss = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gd = ImageDraw.Draw(gloss)
    gd.polygon(
        [
            (0, 0),
            (size, 0),
            (size, int(size * 0.25)),
            (0, int(size * 0.55)),
        ],
        fill=(255, 255, 255, 28),
    )
    gloss_mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(gloss_mask).rounded_rectangle(
        (0, 0, size - 1, size - 1), radius=radius, fill=255
    )
    img.paste(gloss, (0, 0), gloss_mask)
    d = ImageDraw.Draw(img)

    # ---- 3) Graduation cap — big, bold, centered ----
    cx = size // 2
    # Mortarboard (square rotated 45°, drawn as a diamond)
    board_half = int(size * 0.38)
    board_cy = int(size * 0.42)
    board_h = int(size * 0.11)
    # Shadow under board
    shadow = [
        (cx + 6, board_cy - board_h - 4),
        (cx + board_half + 6, board_cy + 4),
        (cx + 6, board_cy + board_h + 4),
        (cx - board_half + 6, board_cy + 4),
    ]
    shadow_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(shadow_layer).polygon(shadow, fill=(30, 27, 75, 100))
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=size * 0.01))
    img = Image.alpha_composite(img, shadow_layer)
    d = ImageDraw.Draw(img)

    board = [
        (cx, board_cy - board_h),
        (cx + board_half, board_cy),
        (cx, board_cy + board_h),
        (cx - board_half, board_cy),
    ]
    d.polygon(board, fill=WHITE)

    # Cap body (trapezoid beneath the board)
    body_top_w = int(size * 0.36)
    body_bot_w = int(size * 0.28)
    body_top_y = board_cy + int(board_h * 0.6)
    body_bot_y = int(size * 0.72)
    body = [
        (cx - body_top_w // 2, body_top_y),
        (cx + body_top_w // 2, body_top_y),
        (cx + body_bot_w // 2, body_bot_y),
        (cx - body_bot_w // 2, body_bot_y),
    ]
    d.polygon(body, fill=WHITE)

    # Button on top of board (center stud)
    stud_r = int(size * 0.025)
    d.ellipse(
        (cx - stud_r, board_cy - board_h - stud_r,
         cx + stud_r, board_cy - board_h + stud_r),
        fill=AMBER,
    )

    # ---- 4) Tassel — golden amber hanging from the right edge of the board ----
    tassel_start_x = cx + int(board_half * 0.55)
    tassel_start_y = board_cy + int(board_h * 0.3)
    tassel_end_x = tassel_start_x + int(size * 0.04)
    tassel_end_y = int(size * 0.80)

    # Tassel cord
    d.line(
        [(tassel_start_x, tassel_start_y), (tassel_end_x, tassel_end_y)],
        fill=AMBER,
        width=max(3, int(size * 0.014)),
    )
    # Tassel knob (circle)
    kr = int(size * 0.035)
    d.ellipse(
        (tassel_end_x - kr, tassel_end_y - kr,
         tassel_end_x + kr, tassel_end_y + kr),
        fill=AMBER,
        outline=AMBER_DARK,
        width=max(2, int(size * 0.006)),
    )
    # Tassel strings (three short downward lines for texture)
    for dx in (-int(size * 0.02), 0, int(size * 0.02)):
        d.line(
            [(tassel_end_x + dx, tassel_end_y + kr),
             (tassel_end_x + dx + int(dx * 0.2), tassel_end_y + kr + int(size * 0.04))],
            fill=AMBER_DARK,
            width=max(2, int(size * 0.008)),
        )

    return img


def main():
    base = make_image(SIZE)
    # ICO sizes Windows uses
    sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
    images = [base.resize(s, Image.LANCZOS) for s in sizes]
    images[0].save(OUT, format="ICO", sizes=sizes, append_images=images[1:])
    # Also save a PNG preview
    base.save(OUT.with_suffix(".png"), format="PNG")
    print(f"Icon saved: {OUT}")
    print(f"PNG preview: {OUT.with_suffix('.png')}")


if __name__ == "__main__":
    main()
