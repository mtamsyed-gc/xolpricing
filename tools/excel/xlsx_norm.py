"""Post-save normalisation of the workbooks openpyxl writes (Control template, synthetic Control, Results template).

- Workbook relationships relative to xl/ ("worksheets/sheet8.xml"), as Excel writes them. openpyxl writes absolute
  targets ("/xl/worksheets/sheet8.xml"); Alteryx's Overwrite Sheet output then stores the sheets it rewrites at
  "xl//xl/worksheets/...", where neither Excel nor openpyxl finds them (Designer 2026.2, 2026-10-14; probe in
  docs/CHANGELOG.md 0.4).
- Fixed created/modified properties and zip timestamps: the same build gives the same bytes.
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

STAMP = "2026-01-01T00:00:00Z"


def normalise(path: Path) -> Path:
    tmp = path.with_suffix(".tmp")
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for i in zin.infolist():
            data = zin.read(i.filename)
            if i.filename == "xl/_rels/workbook.xml.rels":
                data = data.replace(b'Target="/xl/', b'Target="')
            elif i.filename == "docProps/core.xml":
                data = re.sub(rb"(<dcterms:(?:created|modified)[^>]*>)[^<]*(<)", rb"\g<1>" + STAMP.encode() + rb"\2", data)
            zi = zipfile.ZipInfo(i.filename, date_time=(2026, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zout.writestr(zi, data)
    tmp.replace(path)
    return path
