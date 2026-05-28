from __future__ import annotations

import argparse
import csv
import json
import re
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a clickable XLSX workbook from CSV sheets.")
    parser.add_argument("--sheet", action="append", required=True, help="SheetName=path.csv")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)

    sheets = [_parse_sheet_spec(spec) for spec in args.sheet]
    summary = build_workbook(sheets, args.output)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def build_workbook(sheets: list[tuple[str, str | Path]], output: str | Path) -> dict:
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    normalized_sheets = [(safe_sheet_name(name), _read_rows(path)) for name, path in sheets]
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _content_types(len(normalized_sheets)))
        z.writestr("_rels/.rels", _root_rels())
        z.writestr("docProps/app.xml", _app_xml(len(normalized_sheets)))
        z.writestr("docProps/core.xml", _core_xml())
        z.writestr("xl/workbook.xml", _workbook_xml([name for name, _ in normalized_sheets]))
        z.writestr("xl/_rels/workbook.xml.rels", _workbook_rels(len(normalized_sheets)))
        z.writestr("xl/styles.xml", _styles_xml())
        for idx, (name, rows) in enumerate(normalized_sheets, 1):
            z.writestr(f"xl/worksheets/sheet{idx}.xml", _worksheet_xml(rows))
    return {"output": str(output), "sheets": len(normalized_sheets)}


def safe_sheet_name(value: str) -> str:
    return re.sub(r"[\\/*?:\[\]]", " ", value).strip()[:31] or "Sheet"


def _parse_sheet_spec(spec: str) -> tuple[str, Path]:
    if "=" not in spec:
        raise ValueError(f"Invalid sheet spec: {spec}")
    name, path = spec.split("=", 1)
    return name, Path(path)


def _read_rows(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _worksheet_xml(rows: list[dict[str, str]]) -> str:
    headers = list(rows[0].keys()) if rows else ["message"]
    data_rows = rows if rows else [{"message": "No rows"}]
    cols = [_col_xml(i + 1, _column_width(header)) for i, header in enumerate(headers)]
    sheet_rows = [_row_xml(1, headers, style="header")]
    for row_idx, row in enumerate(data_rows, 2):
        sheet_rows.append(_row_xml(row_idx, [row.get(header, "") for header in headers], headers=headers))
    dimension = f"A1:{_col_name(len(headers))}{len(data_rows) + 1}"
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<dimension ref="{dimension}"/>'
        '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" '
        'activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
        f"<cols>{''.join(cols)}</cols>"
        f"<sheetData>{''.join(sheet_rows)}</sheetData>"
        '<autoFilter ref="' + dimension + '"/>'
        "</worksheet>"
    )


def _row_xml(row_idx: int, values: list[str], *, headers: list[str] | None = None, style: str = "") -> str:
    cells = []
    for col_idx, value in enumerate(values, 1):
        header = headers[col_idx - 1] if headers else ""
        ref = f"{_col_name(col_idx)}{row_idx}"
        if style == "header":
            cells.append(_inline_cell(ref, str(value), style_id=1))
        elif _is_link_column(header) and _normalize_url(value):
            cells.append(_hyperlink_formula_cell(ref, _normalize_url(value)))
        else:
            cells.append(_inline_cell(ref, str(value or ""), style_id=0))
    return f'<row r="{row_idx}">{"".join(cells)}</row>'


def _inline_cell(ref: str, value: str, *, style_id: int) -> str:
    style = f' s="{style_id}"' if style_id else ""
    return f'<c r="{ref}" t="inlineStr"{style}><is><t>{escape(value)}</t></is></c>'


def _hyperlink_formula_cell(ref: str, url: str) -> str:
    formula_url = url.replace('"', '""')
    formula = f'HYPERLINK("{formula_url}","{formula_url}")'
    return f'<c r="{ref}" s="2"><f>{escape(formula)}</f><v></v></c>'


def _is_link_column(header: str) -> bool:
    normalized = header.casefold()
    return normalized.endswith("_url") or "url" in normalized


def _normalize_url(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw.startswith(("http://", "https://")):
        return raw
    if raw.startswith("//"):
        return f"https:{raw}"
    if re.match(r"^[A-Za-z0-9.-]+\.[A-Za-z]{2,}(/.*)?$", raw):
        return f"https://{raw}"
    return raw


def _column_width(header: str) -> float:
    return 34.0 if _is_link_column(header) else max(10.0, min(28.0, len(header) * 1.2 + 4))


def _col_xml(index: int, width: float) -> str:
    return f'<col min="{index}" max="{index}" width="{width:.1f}" customWidth="1"/>'


def _col_name(index: int) -> str:
    name = ""
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def _content_types(sheet_count: int) -> str:
    sheets = "".join(
        f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for i in range(1, sheet_count + 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        '<Override PartName="/docProps/core.xml" '
        'ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        '<Override PartName="/docProps/app.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
        f"{sheets}</Types>"
    )


def _root_rels() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
        '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
        "</Relationships>"
    )


def _workbook_xml(sheet_names: list[str]) -> str:
    sheets = "".join(
        f'<sheet name="{escape(name)}" sheetId="{idx}" r:id="rId{idx}"/>'
        for idx, name in enumerate(sheet_names, 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<sheets>{sheets}</sheets>"
        "</workbook>"
    )


def _workbook_rels(sheet_count: int) -> str:
    rels = "".join(
        f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
        for i in range(1, sheet_count + 1)
    )
    rels += (
        f'<Relationship Id="rId{sheet_count + 1}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f"{rels}</Relationships>"
    )


def _styles_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="3">'
        '<font><sz val="11"/><color theme="1"/><name val="Calibri"/></font>'
        '<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>'
        '<font><u/><sz val="11"/><color rgb="FF0563C1"/><name val="Calibri"/></font>'
        '</fonts>'
        '<fills count="3"><fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FF1F4E78"/><bgColor indexed="64"/></patternFill></fill>'
        '</fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="3">'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>'
        '<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
        '</cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        '</styleSheet>'
    )


def _app_xml(sheet_count: int) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
        'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
        f"<Application>Amazon GSPN Official Website Finder</Application><Worksheets>{sheet_count}</Worksheets>"
        "</Properties>"
    )


def _core_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:dcmitype="http://purl.org/dc/dcmitype/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        "<dc:creator>Amazon GSPN Official Website Finder</dc:creator>"
        "<dc:title>Official Website Results</dc:title>"
        "</cp:coreProperties>"
    )


if __name__ == "__main__":
    raise SystemExit(main())
