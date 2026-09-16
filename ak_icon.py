#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""方舟风应用图标：像素绘制 + 内存 PNG，供 hhu_gui 的窗口图标与托盘图标使用。

设计遵循 ak-ui 设计契约：海蓝面层（河海校色）、黄色行动信号、绿色在线信号，
工业几何（圆角底板 + 右上斜切角），零外部资源文件。
"""
import zlib
import struct


def _ak_icon_rgba(size: int) -> bytes:
    """渲染 size×size 的 RGBA 像素数据（行优先）。"""
    S = 4 if size <= 64 else 1
    W = size * S
    dark = (13, 77, 130, 255)      # 深海蓝（下）
    dark_hi = (22, 100, 164, 255)   # 海蓝（上，面层亮部）
    yellow = (255, 204, 0, 255)
    green = (30, 200, 100, 255)
    transparent = (0, 0, 0, 0)
    cut = W * 0.26          # 右上斜切大小
    rad = W * 0.18          # 圆角半径
    edge = max(1, int(W * 0.055))   # 左侧黄色信号条宽
    data = bytearray(W * W * 4)

    def put(x, y, rgba):
        i = (y * W + x) * 4
        data[i:i + 4] = bytes(rgba)

    for y in range(W):
        for x in range(W):
            # 右上斜切：直线从 (W-cut, 0) 到 (W, cut)
            if x > W - cut + y:
                put(x, y, transparent)
                continue
            # 圆角（四角）
            corner = False
            for cx, cy in ((rad, rad), (W - rad, rad), (rad, W - rad), (W - rad, W - rad)):
                if not (cut and cx == W - rad and cy == rad):  # 右上角已被斜切覆盖
                    dx, dy = x - cx, y - cy
                    if (dx < 0 and dy < 0 and (x < rad or y < rad)):
                        if (x < rad and y < rad and dx * dx + dy * dy > rad * rad):
                            corner = True
            # 通用圆角：距角圆心超出半径则透明
            for cx, cy in ((rad, rad), (W - rad, W - rad), (rad, W - rad)):
                if (x < rad and y < rad) or (x >= W - rad and y >= W - rad) or (x < rad and y >= W - rad):
                    if (x - cx) ** 2 + (y - cy) ** 2 > rad * rad:
                        corner = True
            if corner:
                put(x, y, transparent)
                continue
            # 左侧黄色信号条
            if x < edge:
                put(x, y, yellow)
                continue
            # 底板（上亮下暗的面层次感）
            shade = dark_hi if y < W * 0.45 else dark
            put(x, y, shade)

    # 黄色 H（居中）
    hw = W * 0.30          # H 总宽
    hh = W * 0.50          # H 总高
    stroke = max(3, int(W * 0.13))
    x0 = int(W / 2 - hw / 2)
    x1 = int(W / 2 + hw / 2)
    y0 = int(W / 2 - hh / 2)
    y1 = int(W / 2 + hh / 2)
    ymid0, ymid1 = int(W / 2 - stroke / 2), int(W / 2 + stroke / 2)
    for y in range(y0, y1):
        for x in range(x0, x1):
            in_left_bar = x < x0 + stroke
            in_right_bar = x >= x1 - stroke
            in_cross = ymid0 <= y < ymid1
            if (in_left_bar or in_right_bar or in_cross) and data[(y * W + x) * 4 + 3]:
                put(x, y, yellow)  # 只画在底板不透明处，自动被圆角/斜切裁剪

    # 右下绿色在线点
    g_r = W * 0.085
    gx, gy = W * 0.74, W * 0.72
    for y in range(int(gy - g_r) - 1, int(gy + g_r) + 2):
        for x in range(int(gx - g_r) - 1, int(gx + g_r) + 2):
            if (x - gx) ** 2 + (y - gy) ** 2 <= g_r * g_r:
                put(x, y, green)

    # 超采样缩小
    out = bytearray(size * size * 4)
    for oy in range(size):
        for ox in range(size):
            rs = gs = bs = aas = 0
            for sy in range(S):
                for sx in range(S):
                    i = ((oy * S + sy) * W + (ox * S + sx)) * 4
                    a = data[i + 3]
                    rs += data[i] * a
                    gs += data[i + 1] * a
                    bs += data[i + 2] * a
                    aas += a
            j = (oy * size + ox) * 4
            if aas:
                out[j:j + 4] = bytes((round(rs / aas), round(gs / aas), round(bs / aas), round(aas / (S * S))))
            else:
                out[j:j + 4] = b"\x00\x00\x00\x00"
    return bytes(out)


def png_from_rgba(size: int, raw: bytes) -> bytes:
    """RGBA 像素打包为 PNG 字节（纯 zlib，无外部依赖）。"""
    def chunk(typ: bytes, payload: bytes) -> bytes:
        head = struct.pack(">I", len(payload)) + typ + payload
        return head + struct.pack(">I", zlib.crc32(typ + payload) & 0xFFFFFFFF)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    rows = b"".join(b"\x00" + raw[y * size * 4:(y + 1) * size * 4] for y in range(size))
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b"")


def ak_icon_png(size: int = 48) -> bytes:
    return png_from_rgba(size, _ak_icon_rgba(size))


def ico_from_pngs(images) -> bytes:
    """把若干 (尺寸, PNG 字节) 打包成 .ico。

    Vista+ 支持 .ico 里直接放 PNG（32 位带 alpha），打包多尺寸后
    系统会按屏幕 DPI 自动挑最合适的一张，托盘图标不再糊。
    """
    head = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for size, png in images:
        wh = 0 if size >= 256 else size          # 256 在 ICO 里记作 0
        entries += struct.pack("<BBBBHHII", wh, wh, 0, 0, 1, 32, len(png), offset)
        offset += len(png)
        blobs += png
    return head + entries + blobs


def ak_icon_ico(sizes=(16, 20, 24, 32, 48)) -> bytes:
    """多尺寸方舟风 .ico（供系统托盘使用）。"""
    return ico_from_pngs([(s, ak_icon_png(s)) for s in sizes])


if __name__ == "__main__":
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else "icon_preview.png"
    sz = int(sys.argv[2]) if len(sys.argv) > 2 else 128
    open(out, "wb").write(ak_icon_png(sz))
    print(f"已生成 {out} ({sz}x{sz})")
