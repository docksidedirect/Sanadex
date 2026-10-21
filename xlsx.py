# -*- coding: utf-8 -*-
"""Sanadex.wanis.online - Excel (.xlsx) reader/writer with no dependencies.

openpyxl is great but this product must run on a bare Python install, so the
few OOXML parts needed for plain sheets are written/read directly with zipfile
and ElementTree.  Styling stays simple on purpose: one bold header row, money
format, frozen header row, autofilter and sensible column widths.
"""
import io
import re
import xml.etree.ElementTree as ET
import zipfile
from xml.sax.saxutils import escape

NS_MAIN = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
MONEY_FORMAT = '#,##0.00'

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>"""

_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""

_WORKBOOK_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""

_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<numFmts count="1"><numFmt numFmtId="164" formatCode="#,##0.00"/></numFmts>
<fonts count="2">
<font><sz val="11"/><name val="Calibri"/><family val="2"/></font>
<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/><family val="2"/></font>
</fonts>
<fills count="3">
<fill><patternFill patternType="none"/></fill>
<fill><patternFill patternType="gray125"/></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FF14181F"/><bgColor indexed="64"/></patternFill></fill>
</fills>
<borders count="2">
<border><left/><right/><top/><bottom/><diagonal/></border>
<border><left/><right/><top/><bottom style="thin"><color rgb="FFD8D2C4"/></bottom><diagonal/></border>
</borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="3">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>
<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
</cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""


def column_letter(index):
    """1 -> A, 27 -> AA."""
    letters = ''
    while index > 0:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters or 'A'


def _cell_xml(row_num, col_num, value):
    ref = f'{column_letter(col_num)}{row_num}'
    if value is None or value == '':
        return ''
    if isinstance(value, bool):
        value = 1 if value else 0
    if isinstance(value, (int, float)):
        return f'<c r="{ref}" s="2"><v>{value}</v></c>'
    text = escape(str(value))
    return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


def write_xlsx(rows, sheet_name='Sheet1', money_columns=None, widths=None, freeze=True):
    """Build an .xlsx file from a list of rows (first row = header)."""
    rows = [list(r) for r in rows]
    money_columns = set(money_columns or [])
    ncols = max((len(r) for r in rows), default=1)
    if not widths:
        widths = []
        for c in range(ncols):
            longest = 8
            for r in rows[:400]:
                if c < len(r) and r[c] is not None:
                    longest = max(longest, min(52, len(str(r[c])) + 2))
            widths.append(longest)

    sheet_rows = []
    for r_idx, row in enumerate(rows, start=1):
        cells = []
        for c_idx, value in enumerate(row, start=1):
            if r_idx == 1:
                text = escape(str(value if value is not None else ''))
                ref = f'{column_letter(c_idx)}1'
                cells.append(f'<c r="{ref}" t="inlineStr" s="1"><is><t>{text}</t></is></c>')
            elif c_idx in money_columns and isinstance(value, (int, float)):
                ref = f'{column_letter(c_idx)}{r_idx}'
                cells.append(f'<c r="{ref}" s="2"><v>{value}</v></c>')
            else:
                cells.append(_cell_xml(r_idx, c_idx, value))
        sheet_rows.append(f'<row r="{r_idx}">{"".join(cells)}</row>')

    cols_xml = ''.join(f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>'
                       for i, w in enumerate(widths, start=1))
    pane = ('<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
            if freeze and len(rows) > 1 else '')
    dim = f'A1:{column_letter(ncols)}{max(1, len(rows))}'
    autofilter = f'<autoFilter ref="{dim}"/>' if len(rows) > 1 else ''
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<worksheet xmlns="{NS_MAIN}">'
        f'<dimension ref="{dim}"/>'
        f'<sheetViews><sheetView workbookViewId="0">{pane}</sheetView></sheetViews>'
        '<sheetFormatPr defaultRowHeight="15"/>'
        f'<cols>{cols_xml}</cols>'
        f'<sheetData>{"".join(sheet_rows)}</sheetData>'
        f'{autofilter}'
        '</worksheet>'
    )
    safe_name = re.sub(r'[\\/*?:\[\]]', ' ', str(sheet_name or 'Sheet1'))[:31] or 'Sheet1'
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<workbook xmlns="{NS_MAIN}" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{escape(safe_name)}" sheetId="1" r:id="rId1"/></sheets>'
        '</workbook>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('[Content_Types].xml', _CONTENT_TYPES)
        zf.writestr('_rels/.rels', _ROOT_RELS)
        zf.writestr('xl/workbook.xml', workbook)
        zf.writestr('xl/_rels/workbook.xml.rels', _WORKBOOK_RELS)
        zf.writestr('xl/styles.xml', _STYLES)
        zf.writestr('xl/worksheets/sheet1.xml', sheet)
    return buf.getvalue()


def _text_of(node):
    if node is None:
        return ''
    return ''.join(node.itertext())


def read_xlsx(data):
    """Parse the first worksheet of an .xlsx file into list-of-lists."""
    if isinstance(data, bytes):
        stream = io.BytesIO(data)
    else:
        stream = data
    with zipfile.ZipFile(stream) as zf:
        names = zf.namelist()
        shared = []
        if 'xl/sharedStrings.xml' in names:
            root = ET.fromstring(zf.read('xl/sharedStrings.xml'))
            for si in root.findall(f'{{{NS_MAIN}}}si'):
                shared.append(_text_of(si))
        sheet_name = 'xl/worksheets/sheet1.xml'
        if sheet_name not in names:
            candidates = sorted(n for n in names if n.startswith('xl/worksheets/sheet'))
            if not candidates:
                return []
            sheet_name = candidates[0]
        root = ET.fromstring(zf.read(sheet_name))
        rows = []
        for row in root.iter(f'{{{NS_MAIN}}}row'):
            values = {}
            for cell in row.findall(f'{{{NS_MAIN}}}c'):
                ref = cell.get('r') or ''
                match = re.match(r'([A-Z]+)(\d+)', ref)
                col = 0
                if match:
                    for ch in match.group(1):
                        col = col * 26 + (ord(ch) - 64)
                ctype = cell.get('t')
                value_node = cell.find(f'{{{NS_MAIN}}}v')
                if ctype == 'inlineStr':
                    value = _text_of(cell.find(f'{{{NS_MAIN}}}is'))
                elif ctype == 's' and value_node is not None:
                    idx = int(value_node.text or 0)
                    value = shared[idx] if 0 <= idx < len(shared) else ''
                elif ctype == 'str':
                    value = _text_of(cell.find(f'{{{NS_MAIN}}}v'))
                elif value_node is not None and value_node.text not in (None, ''):
                    raw = value_node.text
                    try:
                        num = float(raw)
                        value = int(num) if num.is_integer() else num
                    except ValueError:
                        value = raw
                else:
                    value = ''
                if col:
                    values[col] = value
            if values:
                width = max(values)
                rows.append([values.get(i + 1, '') for i in range(width)])
            else:
                rows.append([])
    return rows


def read_csv(data):
    import csv
    if isinstance(data, bytes):
        text = data.decode('utf-8-sig', 'replace')
    else:
        text = data
    return [row for row in csv.reader(io.StringIO(text))]


def sniff_rows(filename, data):
    """Read an uploaded spreadsheet by extension (xlsx, csv/txt)."""
    name = (filename or '').lower()
    if name.endswith(('.xlsx', '.xlsm')):
        return read_xlsx(data)
    return read_csv(data)
