"""_png.py: a small PNG reader and writer built on the standard library.

Used by ui_measure.py to read screenshot dimensions without decoding, to decode
small images (crops, thumbnails, test fixtures) to RGB, to encode PNGs, and to
produce a blurred "squint" image. Decoding is pure Python, so it is meant for
small images; decode() refuses anything above max_pixels. Full-page screenshots
are only ever header-read.
"""
import struct
import zlib

SIGNATURE = b"\x89PNG\r\n\x1a\n"
CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


def read_header(data):
    """Return {width, height, bit_depth, color_type, interlace} from the IHDR chunk."""
    if len(data) < 33 or data[:8] != SIGNATURE or data[12:16] != b"IHDR":
        raise ValueError("not a PNG")
    width, height, depth, color, _comp, _filt, interlace = struct.unpack(">IIBBBBB", data[16:29])
    return {"width": width, "height": height, "bit_depth": depth, "color_type": color, "interlace": interlace}


def read_size(data):
    """Return (width, height) without decoding any pixels."""
    header = read_header(data)
    return header["width"], header["height"]


def _chunks(data):
    pos = 8
    while pos + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        yield kind, body
        pos += 12 + length
        if kind == b"IEND":
            break


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def _unfilter(raw, width_bytes, height, bpp):
    rows = []
    prev = bytearray(width_bytes)
    pos = 0
    for _ in range(height):
        kind = raw[pos]
        line = bytearray(raw[pos + 1:pos + 1 + width_bytes])
        pos += 1 + width_bytes
        if kind == 1:
            for i in range(bpp, width_bytes):
                line[i] = (line[i] + line[i - bpp]) & 255
        elif kind == 2:
            for i in range(width_bytes):
                line[i] = (line[i] + prev[i]) & 255
        elif kind == 3:
            for i in range(width_bytes):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 255
        elif kind == 4:
            for i in range(width_bytes):
                left = line[i - bpp] if i >= bpp else 0
                upleft = prev[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + _paeth(left, prev[i], upleft)) & 255
        elif kind != 0:
            raise ValueError("unknown PNG filter %d" % kind)
        rows.append(line)
        prev = line
    return rows


def decode(data, max_pixels=2000000):
    """Decode a non-interlaced 8 or 16 bit PNG to (width, height, rgb) where rgb is a bytearray.

    Alpha is dropped (screenshots are opaque). Palette, gray, gray+alpha, RGB and RGBA are supported.
    """
    header = read_header(data)
    width, height = header["width"], header["height"]
    if width * height > max_pixels:
        raise ValueError("image too large to decode in pure Python (%dx%d)" % (width, height))
    if header["interlace"]:
        raise ValueError("interlaced PNG is not supported")
    depth, color = header["bit_depth"], header["color_type"]
    if depth not in (8, 16) or color not in CHANNELS:
        raise ValueError("unsupported PNG format (bit depth %d, color type %d)" % (depth, color))
    palette = b""
    idat = []
    for kind, body in _chunks(data):
        if kind == b"PLTE":
            palette = body
        elif kind == b"IDAT":
            idat.append(body)
    channels = CHANNELS[color]
    step = depth // 8
    bpp = channels * step
    width_bytes = width * bpp
    raw = zlib.decompress(b"".join(idat))
    if len(raw) < (width_bytes + 1) * height:
        raise ValueError("truncated PNG data")
    rows = _unfilter(raw, width_bytes, height, bpp)
    out = bytearray(width * height * 3)
    o = 0
    for line in rows:
        for x in range(width):
            base = x * bpp
            if color == 3:
                idx = line[base] * 3
                out[o:o + 3] = palette[idx:idx + 3]
            elif color in (0, 4):
                v = line[base]
                out[o] = out[o + 1] = out[o + 2] = v
            else:
                out[o] = line[base]
                out[o + 1] = line[base + step]
                out[o + 2] = line[base + 2 * step]
            o += 3
    return width, height, out


def _chunk(kind, body):
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)


def _filter_row(kind, line, prev, bpp):
    if kind == 0:
        return bytes(line)
    out = bytearray(len(line))
    for i in range(len(line)):
        left = line[i - bpp] if i >= bpp else 0
        up = prev[i]
        upleft = prev[i - bpp] if i >= bpp else 0
        if kind == 1:
            pred = left
        elif kind == 2:
            pred = up
        elif kind == 3:
            pred = (left + up) >> 1
        else:
            pred = _paeth(left, up, upleft)
        out[i] = (line[i] - pred) & 255
    return bytes(out)


def encode(width, height, rgb, filters=None, level=6):
    """Encode RGB bytes (3 per pixel) as a PNG. filters is an optional list of per-row filter types 0-4."""
    if len(rgb) != width * height * 3:
        raise ValueError("pixel buffer does not match dimensions")
    stride = width * 3
    scan = bytearray()
    prev = bytes(stride)
    for y in range(height):
        line = bytes(rgb[y * stride:(y + 1) * stride])
        kind = filters[y % len(filters)] if filters else 0
        scan.append(kind)
        scan += _filter_row(kind, line, prev, 3)
        prev = line
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return SIGNATURE + _chunk(b"IHDR", header) + _chunk(b"IDAT", zlib.compress(bytes(scan), level)) + _chunk(b"IEND", b"")


def pixel(rgb, width, x, y):
    i = (y * width + x) * 3
    return rgb[i], rgb[i + 1], rgb[i + 2]


def crop(rgb, width, height, x, y, w, h):
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(width, x + w), min(height, y + h)
    out = bytearray()
    for row in range(y0, y1):
        out += rgb[(row * width + x0) * 3:(row * width + x1) * 3]
    return max(0, x1 - x0), max(0, y1 - y0), out


def downscale(rgb, width, height, factor):
    """Box-average downscale by an integer factor."""
    nw, nh = max(1, width // factor), max(1, height // factor)
    out = bytearray(nw * nh * 3)
    for ny in range(nh):
        for nx in range(nw):
            sums = [0, 0, 0]
            count = 0
            for dy in range(factor):
                for dx in range(factor):
                    x, y = nx * factor + dx, ny * factor + dy
                    if x < width and y < height:
                        i = (y * width + x) * 3
                        sums[0] += rgb[i]
                        sums[1] += rgb[i + 1]
                        sums[2] += rgb[i + 2]
                        count += 1
            o = (ny * nw + nx) * 3
            for c in range(3):
                out[o + c] = sums[c] // max(1, count)
    return nw, nh, out


def box_blur(rgb, width, height, radius):
    """Separable box blur using running sums. radius is in pixels."""
    if radius <= 0:
        return bytearray(rgb)
    tmp = bytearray(len(rgb))
    out = bytearray(len(rgb))
    for c in range(3):
        for y in range(height):
            base = y * width
            acc = 0
            count = 0
            for x in range(min(radius, width - 1) + 1):
                acc += rgb[(base + x) * 3 + c]
                count += 1
            for x in range(width):
                tmp[(base + x) * 3 + c] = acc // count
                add, drop = x + radius + 1, x - radius
                if add < width:
                    acc += rgb[(base + add) * 3 + c]
                    count += 1
                if drop >= 0:
                    acc -= rgb[(base + drop) * 3 + c]
                    count -= 1
        for x in range(width):
            acc = 0
            count = 0
            for y in range(min(radius, height - 1) + 1):
                acc += tmp[(y * width + x) * 3 + c]
                count += 1
            for y in range(height):
                out[(y * width + x) * 3 + c] = acc // count
                add, drop = y + radius + 1, y - radius
                if add < height:
                    acc += tmp[(add * width + x) * 3 + c]
                    count += 1
                if drop >= 0:
                    acc -= tmp[(drop * width + x) * 3 + c]
                    count -= 1
    return out


def squint(png_bytes, radius=2):
    """Blur a small PNG so only composition and hierarchy remain. Returns PNG bytes."""
    width, height, rgb = decode(png_bytes)
    return encode(width, height, box_blur(rgb, width, height, radius))
