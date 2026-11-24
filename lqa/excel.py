"""Read saved Excel cell values into a searchable, row-addressable glossary."""
import csv
import io
import sys
from contextlib import closing
from pathlib import Path

# The portable app and the system-Python launcher use the same pure-Python wheels.
readers = Path(__file__).resolve().parents[1] / "vendor" / "excel"
sys.path[:0] = [str(wheel) for wheel in sorted(readers.glob("*.whl"))]

import openpyxl
import xlrd


def cell_text(value, number_format=None):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int) and number_format and not number_format.strip("0"):
        # Numeric string IDs are often displayed in Excel with a mask such as 00000.
        return ("-" if value < 0 else "") + str(abs(value)).zfill(len(number_format))
    return str(value)


def xlsx_rows(source):
    workbook = io.BytesIO(source) if isinstance(source, bytes) else source
    with closing(openpyxl.load_workbook(workbook, read_only=True, data_only=True, keep_links=False)) as book:
        for sheet in book.worksheets:
            # Some exporters declare an entire sheet (or only A1) as the used area.
            sheet.reset_dimensions()
            for number, row in enumerate(sheet.rows, 1):
                yield sheet.title, number, [cell_text(cell.value, cell.number_format) for cell in row]


def xls_rows(source):
    location = {"file_contents": source} if isinstance(source, bytes) else {"filename": str(source)}
    with xlrd.open_workbook(**location, on_demand=True, ragged_rows=True, formatting_info=True) as book:
        for name in book.sheet_names():
            sheet = book.sheet_by_name(name)
            for index in range(sheet.nrows):
                values = []
                for cell in sheet.row(index):
                    value, fmt = cell.value, None
                    if cell.ctype == xlrd.XL_CELL_NUMBER:
                        style = book.format_map.get(book.xf_list[cell.xf_index].format_key)
                        fmt = style.format_str if style else None
                    elif cell.ctype == xlrd.XL_CELL_DATE:
                        date = xlrd.xldate_as_datetime(value, book.datemode)
                        value = date.time() if 0 <= value < 1 else date
                    elif cell.ctype == xlrd.XL_CELL_BOOLEAN:
                        value = bool(value)
                    elif cell.ctype == xlrd.XL_CELL_ERROR:
                        value = xlrd.error_text_from_code.get(value, "#ERROR!")
                    values.append(cell_text(value, fmt))
                yield name, index + 1, values
            book.unload_sheet(name)


def workbook_rows(source, extension):
    try:
        yield from (xls_rows(source) if extension == ".xls" else xlsx_rows(source))
    except Exception as exc:
        raise ValueError("无法读取 Excel 工作簿，请确认文件能正常打开且未加密。") from exc


def write_workbook_index(source, extension, output):
    writer = csv.writer(output, dialect="excel-tab")
    writer.writerow(["工作表", "Excel 行号", "单元格（从 A 列起按顺序排列）"])
    has_content = False
    for sheet, number, cells in workbook_rows(source, extension):
        while cells and cells[-1] == "":
            cells.pop()
        if cells:
            writer.writerow([sheet, number, *cells])
            has_content = True
    if not has_content:
        raise ValueError("Excel 工作簿没有可读取的单元格内容。若词条来自公式，请先在 Excel 中计算并保存。")
