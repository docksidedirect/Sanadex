# -*- coding: utf-8 -*-
"""Sanadex.wanis.online - Code 128 barcode rendering (standard library only).

Labels are printed from the server so they work offline and from any browser.
The 107 symbol patterns below are the ISO/IEC 15417 widths (module counts).
"""
import html

# 6 widths per symbol (bar, space, bar, space, bar, space) except stop = 7.
PATTERNS = [
    (2, 1, 2, 2, 2, 2), (2, 2, 2, 1, 2, 2), (2, 2, 2, 2, 2, 1), (1, 2, 1, 2, 2, 3),
    (1, 2, 1, 3, 2, 2), (1, 3, 1, 2, 2, 2), (1, 2, 2, 2, 1, 3), (1, 2, 2, 3, 1, 2),
    (1, 3, 2, 2, 1, 2), (2, 2, 1, 2, 1, 3), (2, 2, 1, 3, 1, 2), (2, 3, 1, 2, 1, 2),
    (1, 1, 2, 2, 3, 2), (1, 2, 2, 1, 3, 2), (1, 2, 2, 2, 3, 1), (1, 1, 3, 2, 2, 2),
    (1, 2, 3, 1, 2, 2), (1, 2, 3, 2, 2, 1), (2, 2, 3, 2, 1, 1), (2, 2, 1, 1, 3, 2),
    (2, 2, 1, 2, 3, 1), (2, 1, 3, 2, 1, 2), (2, 2, 3, 1, 1, 2), (3, 1, 2, 1, 3, 1),
    (3, 1, 1, 2, 2, 2), (3, 2, 1, 1, 2, 2), (3, 2, 1, 2, 2, 1), (3, 1, 2, 2, 1, 2),
    (3, 2, 2, 1, 1, 2), (3, 2, 2, 2, 1, 1), (2, 1, 2, 1, 2, 3), (2, 1, 2, 3, 2, 1),
    (2, 3, 2, 1, 2, 1), (1, 1, 1, 3, 2, 3), (1, 3, 1, 1, 2, 3), (1, 3, 1, 3, 2, 1),
    (1, 1, 2, 3, 1, 3), (1, 3, 2, 1, 1, 3), (1, 3, 2, 3, 1, 1), (2, 1, 1, 3, 1, 3),
    (2, 3, 1, 1, 1, 3), (2, 3, 1, 3, 1, 1), (1, 1, 2, 1, 3, 3), (1, 1, 2, 3, 3, 1),
    (1, 3, 2, 1, 3, 1), (1, 1, 3, 1, 2, 3), (1, 1, 3, 3, 2, 1), (1, 3, 3, 1, 2, 1),
    (3, 1, 3, 1, 2, 1), (2, 1, 1, 3, 3, 1), (2, 3, 1, 1, 3, 1), (2, 1, 3, 1, 1, 3),
    (2, 1, 3, 3, 1, 1), (2, 1, 3, 1, 3, 1), (3, 1, 1, 1, 2, 3), (3, 1, 1, 3, 2, 1),
    (3, 3, 1, 1, 2, 1), (3, 1, 2, 1, 1, 3), (3, 1, 2, 3, 1, 1), (3, 3, 2, 1, 1, 1),
    (3, 1, 4, 1, 1, 1), (2, 2, 1, 4, 1, 1), (4, 3, 1, 1, 1, 1), (1, 1, 1, 2, 2, 4),
    (1, 1, 1, 4, 2, 2), (1, 2, 1, 1, 2, 4), (1, 2, 1, 4, 2, 1), (1, 4, 1, 1, 2, 2),
    (1, 4, 1, 2, 2, 1), (1, 1, 2, 2, 1, 4), (1, 1, 2, 4, 1, 2), (1, 2, 2, 1, 1, 4),
    (1, 2, 2, 4, 1, 1), (1, 4, 2, 1, 1, 2), (1, 4, 2, 2, 1, 1), (2, 4, 1, 2, 1, 1),
    (2, 2, 1, 1, 1, 4), (4, 1, 3, 1, 1, 1), (2, 4, 1, 1, 1, 2), (1, 3, 4, 1, 1, 1),
    (1, 1, 1, 2, 4, 2), (1, 2, 1, 1, 4, 2), (1, 2, 1, 2, 4, 1), (1, 1, 4, 2, 1, 2),
    (1, 2, 4, 1, 1, 2), (1, 2, 4, 2, 1, 1), (4, 1, 1, 2, 1, 2), (4, 2, 1, 1, 1, 2),
    (4, 2, 1, 2, 1, 1), (2, 1, 2, 1, 4, 1), (2, 1, 4, 1, 2, 1), (4, 1, 2, 1, 2, 1),
    (1, 1, 1, 1, 4, 3), (1, 1, 1, 3, 4, 1), (1, 3, 1, 1, 4, 1), (1, 1, 4, 1, 1, 3),
    (1, 1, 4, 3, 1, 1), (4, 1, 1, 1, 1, 3), (4, 1, 1, 3, 1, 1), (1, 1, 3, 1, 4, 1),
    (1, 1, 4, 1, 3, 1), (3, 1, 1, 1, 4, 1), (4, 1, 1, 1, 3, 1), (2, 1, 1, 4, 1, 2),
    (2, 1, 1, 2, 1, 4), (2, 1, 1, 2, 3, 2), (2, 3, 3, 1, 1, 1, 2),
]

START_B, START_C, STOP = 104, 105, 106


def encode(data):
    """Return the list of Code128 symbol values for ``data`` (or [] if impossible)."""
    clean = ''.join(ch for ch in str(data or '') if 32 <= ord(ch) <= 126)[:48]
    if not clean:
        return []
    numeric = clean.isdigit() and len(clean) % 2 == 0 and len(clean) >= 4
    if numeric:
        codes = [START_C] + [int(clean[i:i + 2]) for i in range(0, len(clean), 2)]
    else:
        codes = [START_B] + [ord(ch) - 32 for ch in clean]
    checksum = codes[0]
    for weight, value in enumerate(codes[1:], start=1):
        checksum += weight * value
    return codes + [checksum % 103, STOP]


def svg(data, height=46, module_width=1.9, quiet=10, show_text=True, class_name='bc'):
    """Render a scannable Code 128 barcode as inline SVG."""
    codes = encode(data)
    if not codes:
        return f'<span class="bc-empty">{html.escape(str(data or ""))}</span>'
    total_modules = sum(sum(PATTERNS[c]) for c in codes)
    width = (total_modules + quiet * 2) * module_width
    parts = []
    x = quiet * module_width
    for code in codes:
        dark = True
        for w in PATTERNS[code]:
            seg = w * module_width
            if dark:
                parts.append(f'<rect x="{x:.2f}" y="0" width="{seg:.2f}" height="{height}"/>')
            x += seg
            dark = not dark
    text_h = 16 if show_text else 0
    label = html.escape(''.join(ch for ch in str(data) if 32 <= ord(ch) <= 126))
    text_svg = ''
    if show_text:
        text_svg = (f'<text x="{width / 2:.2f}" y="{height + 12}" text-anchor="middle" '
                    f'font-family="ui-monospace,Consolas,monospace" font-size="11">{label}</text>')
    bars = ''.join(parts)
    return (f'<svg class="{class_name}" xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="0 0 {width:.2f} {height + text_h}" width="{width:.2f}" '
            f'height="{height + text_h}" role="img" aria-label="{label}">'
            f'<g fill="#000">{bars}</g>{text_svg}</svg>')
