#!/usr/bin/env python3
"""
Fail if any unscoped Qt/QGIS enum is used, since those break under PyQt6 / Qt6.

The scoped form (e.g. Qt.AlignmentFlag.AlignLeft, QgsWkbTypes.GeometryType.
PolygonGeometry) has the enum class in between and is fine; the bare form
(Qt.AlignLeft) is not. Matches inside comments and string literals are ignored,
and getattr(Module, 'Name') fallbacks are allowed.

Run:  python tools/check_qt6_enums.py
Exit: 0 clean, 1 if any unscoped enum is found.
"""

import io
import os
import re
import sys
import tokenize

# Each regex matches the UNSCOPED form only. Extend when a new pattern appears.
UNSCOPED_ENUM_PATTERNS = [
    r"\bQt\.Align(Left|Right|HCenter|VCenter|Center|Top|Bottom|Justify)\b",
    r"\bQt\.(WaitCursor|ArrowCursor|PointingHandCursor|CrossCursor|BusyCursor)\b",
    r"\bQt\.(DashLine|DotLine|SolidLine|DashDotLine|NoPen)\b",
    r"\bQt\.(LeftButton|RightButton|MidButton|MiddleButton|NoButton)\b",
    r"\bQt\.(Horizontal|Vertical)\b",
    r"\bQt\.(Checked|Unchecked|PartiallyChecked)\b",
    r"\bQt\.(KeepAspectRatio|IgnoreAspectRatio|KeepAspectRatioByExpanding)\b",
    r"\bQt\.(SmoothTransformation|FastTransformation)\b",
    r"\bQt\.(Left|Right|Top|Bottom)DockWidgetArea\b",
    r"\bQt\.Key_\w+\b",
    r"\bQt\.(UserRole|DisplayRole|EditRole|DecorationRole)\b",
    r"\bQt\.(ScrollBarAlwaysOff|ScrollBarAlwaysOn|ScrollBarAsNeeded)\b",
    r"\bQt\.(ElideLeft|ElideRight|ElideMiddle|ElideNone)\b",
    r"\bQt\.(RichText|PlainText|AutoText)\b",
    r"\bQt\.WA_\w+\b",
    r"\bQFrame\.(NoFrame|Box|Panel|StyledPanel|HLine|VLine|Sunken|Raised|Plain)\b",
    r"\bQFont\.(Thin|Light|Normal|Medium|DemiBold|Bold|ExtraBold|Black)\b",
    r"\bQMessageBox\.(Yes|No|Ok|Cancel|Abort|Retry|Ignore|Close|Save|Discard)\b",
    r"\bQMessageBox\.(Warning|Information|Critical|Question|NoIcon)\b",
    r"\bQDialogButtonBox\.(Ok|Cancel|Yes|No|Apply|Close|Save|Reset)\b",
    r"\bQEvent\.(Enter|Leave|MouseMove|MouseButtonPress|MouseButtonRelease|KeyPress)\b",
    r"\bQPainter\.(Antialiasing|SmoothPixmapTransform|TextAntialiasing)\b",
    r"\bQGraphicsView\.(ScrollHandDrag|NoDrag|RubberBandDrag|AnchorUnderMouse|AnchorViewCenter|NoAnchor)\b",
    r"\bQSizePolicy\.(Expanding|Fixed|Minimum|Maximum|Preferred|MinimumExpanding|Ignored)\b",
    r"\bQHeaderView\.(Stretch|ResizeToContents|Fixed|Interactive)\b",
    r"\bQAbstractItemView\.(NoEditTriggers|SingleSelection|NoSelection|SelectRows)\b",
    r"\bQgsWkbTypes\.(Point|Line|Polygon|Unknown|Null)Geometry\b",
    r"\bQgsVertexMarker\.ICON_\w+\b",
    r"\bQgsPalLayerSettings\.(OverPoint|AroundPoint|OnLine|AboveLine|BelowLine|Line|Curved|Horizontal|Free)\b",
]

# Directories not scanned (not shipped, or no Qt usage).
SKIP_DIRS = {'.git', '__pycache__', 'tests', 'tools', '.github'}


def code_lines(path):
    """File lines with comments and string literals blanked out, so enum
    matches inside them are not false positives."""
    with open(path, encoding='utf-8') as fh:
        src = fh.read()
    blanked = [list(line) for line in src.splitlines()]
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                (sr, sc), (er, ec) = tok.start, tok.end
                for r in range(sr, er + 1):
                    idx = r - 1
                    if not (0 <= idx < len(blanked)):
                        continue
                    c0 = sc if r == sr else 0
                    c1 = ec if r == er else len(blanked[idx])
                    for c in range(c0, min(c1, len(blanked[idx]))):
                        blanked[idx][c] = ' '
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass
    return [''.join(cs) for cs in blanked]


def scan(files):
    regexes = [re.compile(p) for p in UNSCOPED_ENUM_PATTERNS]
    hits = []
    for path in files:
        for i, line in enumerate(code_lines(path), 1):
            if 'getattr(' in line:
                continue
            for rx in regexes:
                m = rx.search(line)
                if m:
                    hits.append(f'{os.path.relpath(path)}:{i}  {m.group(0)}')
    return hits


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    files = []
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for fn in sorted(fns):
            if fn.endswith('.py'):
                files.append(os.path.join(dp, fn))
    hits = scan(sorted(files))
    if hits:
        print(f'FAIL: {len(hits)} unscoped Qt/QGIS enum(s):')
        for h in hits:
            print('  ' + h)
        return 1
    print(f'OK: no unscoped Qt/QGIS enums in {len(files)} files')
    return 0


if __name__ == '__main__':
    sys.exit(main())
