from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from io import BytesIO
from typing import Any

from defusedxml import ElementTree as ET
from defusedxml.common import DefusedXmlException
from pydantic import ValidationError

from app.adapters.feishu_contact import batch_get_open_ids
from app.schemas.onboarding import CandidateCaseCreate
from app.services.org_catalog import BUSINESS_LINE_POSITIONS, EMPLOYMENT_TYPE_OPTIONS

IMPORT_COLUMNS = (
    "候选人姓名",
    "预计入职日期",
    "条线",
    "岗位",
    "城市",
    "入职类型",
    "职级",
    "候选人手机号",
    "候选人邮箱",
    "候选人 open_id",
    "HRBP open_id",
    "负责人 open_id",
)
REQUIRED_IMPORT_COLUMNS = ("候选人姓名", "预计入职日期", "条线", "岗位")
XLSX_NS = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
RELS_NS = {"rel": "http://schemas.openxmlformats.org/package/2006/relationships"}


@dataclass
class ParsedCandidateRow:
    row_number: int
    payload: CandidateCaseCreate
    warnings: list[str] = field(default_factory=list)


@dataclass
class CandidateImportParseResult:
    rows: list[ParsedCandidateRow]
    errors: list[dict[str, Any]]


async def parse_candidate_import_xlsx(file_bytes: bytes) -> CandidateImportParseResult:
    """Parse fixed-column candidate import XLSX data and resolve missing candidate open_ids."""

    raw_rows = _read_xlsx_rows(file_bytes)
    if not raw_rows:
        raise ValueError("Excel 文件为空")
    header = [_cell_text(value) for value in raw_rows[0]]
    missing = [column for column in REQUIRED_IMPORT_COLUMNS if column not in header]
    if missing:
        raise ValueError(f"Excel 缺少必填列：{', '.join(missing)}")

    index = {column: header.index(column) for column in header if column}
    parsed: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for row_number, row in enumerate(raw_rows[1:], start=2):
        values = {column: _cell_value(row, index.get(column)) for column in IMPORT_COLUMNS}
        if not any(str(value or "").strip() for value in values.values()):
            continue
        try:
            parsed.append({"row_number": row_number, "values": values, "warnings": _validate_row(values)})
        except ValueError as exc:
            errors.append({"row": row_number, "message": str(exc)})

    lookup = await _resolve_missing_open_ids(parsed)
    rows: list[ParsedCandidateRow] = []
    for item in parsed:
        values = item["values"]
        warnings = list(item["warnings"])
        open_id = _cell_text(values.get("候选人 open_id"))
        if not open_id:
            open_id = lookup.get(_cell_text(values.get("候选人邮箱")).lower()) or lookup.get(_cell_text(values.get("候选人手机号")).lower())
            if open_id:
                warnings.append("已通过手机号/邮箱匹配候选人 open_id")
            elif _cell_text(values.get("候选人手机号")) or _cell_text(values.get("候选人邮箱")):
                warnings.append("未从飞书通讯录匹配到候选人 open_id，机器人暂不能主动识别该候选人")
        try:
            payload = CandidateCaseCreate(
                candidate_name=_cell_text(values.get("候选人姓名")),
                expected_onboard_date=_parse_date(values.get("预计入职日期")),
                department=_cell_text(values.get("条线")) or None,
                position=_cell_text(values.get("岗位")) or None,
                city=_cell_text(values.get("城市")) or None,
                employment_type=_cell_text(values.get("入职类型")) or None,
                job_level=_cell_text(values.get("职级")) or None,
                mobile=_cell_text(values.get("候选人手机号")) or None,
                email=_cell_text(values.get("候选人邮箱")) or None,
                feishu_open_id=open_id or None,
                hrbp_open_id=_cell_text(values.get("HRBP open_id")) or None,
                manager_open_id=_cell_text(values.get("负责人 open_id")) or None,
            )
        except (ValueError, ValidationError) as exc:
            errors.append({"row": item["row_number"], "message": str(exc)})
            continue
        rows.append(ParsedCandidateRow(row_number=item["row_number"], payload=payload, warnings=warnings))
    return CandidateImportParseResult(rows=rows, errors=errors)


def _validate_row(values: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    for column in REQUIRED_IMPORT_COLUMNS:
        if not _cell_text(values.get(column)):
            raise ValueError(f"{column} 不能为空")
    line = _cell_text(values.get("条线"))
    position = _cell_text(values.get("岗位"))
    if line not in BUSINESS_LINE_POSITIONS:
        raise ValueError(f"条线必须是：{', '.join(BUSINESS_LINE_POSITIONS)}")
    if position not in BUSINESS_LINE_POSITIONS[line]:
        raise ValueError(f"岗位必须属于 {line}，当前填写：{position}")
    employment_type = _cell_text(values.get("入职类型"))
    if employment_type and employment_type not in EMPLOYMENT_TYPE_OPTIONS:
        raise ValueError(f"入职类型必须是：{', '.join(EMPLOYMENT_TYPE_OPTIONS)}")
    if not _cell_text(values.get("候选人 open_id")) and not _cell_text(values.get("候选人手机号")) and not _cell_text(values.get("候选人邮箱")):
        warnings.append("候选人 open_id、手机号、邮箱均为空，机器人暂不能主动识别该候选人")
    return warnings


async def _resolve_missing_open_ids(parsed: list[dict[str, Any]]) -> dict[str, str]:
    emails: list[str] = []
    mobiles: list[str] = []
    for item in parsed:
        values = item["values"]
        if _cell_text(values.get("候选人 open_id")):
            continue
        email = _cell_text(values.get("候选人邮箱"))
        mobile = _cell_text(values.get("候选人手机号"))
        if email:
            emails.append(email)
        if mobile:
            mobiles.append(mobile)
    try:
        return await batch_get_open_ids(emails=emails, mobiles=mobiles)
    except Exception:
        return {}


def _read_xlsx_rows(file_bytes: bytes) -> list[list[Any]]:
    try:
        with zipfile.ZipFile(BytesIO(file_bytes)) as workbook:
            shared_strings = _shared_strings(workbook)
            sheet_path = _first_sheet_path(workbook)
            xml = workbook.read(sheet_path)
        root = ET.fromstring(xml)
    except (KeyError, zipfile.BadZipFile, DefusedXmlException) as exc:
        raise ValueError("请上传有效的 .xlsx 文件") from exc

    rows: list[list[Any]] = []
    for row in root.findall(".//main:sheetData/main:row", XLSX_NS):
        values: dict[int, Any] = {}
        for cell in row.findall("main:c", XLSX_NS):
            ref = cell.attrib.get("r", "")
            col = _column_index(ref)
            if col is None:
                continue
            values[col] = _parse_cell(cell, shared_strings)
        if values:
            max_col = max(values)
            rows.append([values.get(index, "") for index in range(max_col + 1)])
    return rows


def _shared_strings(workbook: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in workbook.namelist():
        return []
    root = ET.fromstring(workbook.read("xl/sharedStrings.xml"))
    strings: list[str] = []
    for item in root.findall("main:si", XLSX_NS):
        text = "".join(node.text or "" for node in item.findall(".//main:t", XLSX_NS))
        strings.append(text)
    return strings


def _first_sheet_path(workbook: zipfile.ZipFile) -> str:
    if "xl/workbook.xml" not in workbook.namelist():
        return "xl/worksheets/sheet1.xml"
    root = ET.fromstring(workbook.read("xl/workbook.xml"))
    sheet = root.find(".//main:sheets/main:sheet", XLSX_NS)
    if sheet is None:
        return "xl/worksheets/sheet1.xml"
    rel_id = sheet.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
    if not rel_id or "xl/_rels/workbook.xml.rels" not in workbook.namelist():
        return "xl/worksheets/sheet1.xml"
    rels = ET.fromstring(workbook.read("xl/_rels/workbook.xml.rels"))
    for rel in rels.findall("rel:Relationship", RELS_NS):
        if rel.attrib.get("Id") != rel_id:
            continue
        target = rel.attrib.get("Target", "worksheets/sheet1.xml")
        if target.startswith("/"):
            return target.lstrip("/")
        return f"xl/{target}".replace("xl/../", "")
    return "xl/worksheets/sheet1.xml"


def _parse_cell(cell: ET.Element, shared_strings: list[str]) -> Any:
    cell_type = cell.attrib.get("t", "")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(".//main:t", XLSX_NS))
    value_node = cell.find("main:v", XLSX_NS)
    value = value_node.text if value_node is not None else ""
    if cell_type == "s":
        try:
            return shared_strings[int(value)]
        except (ValueError, IndexError):
            return ""
    return value or ""


def _column_index(cell_ref: str) -> int | None:
    match = re.match(r"([A-Z]+)", cell_ref)
    if not match:
        return None
    index = 0
    for char in match.group(1):
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index - 1


def _cell_value(row: list[Any], index: int | None) -> Any:
    if index is None or index >= len(row):
        return ""
    return row[index]


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _parse_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    text = _cell_text(value)
    if not text:
        raise ValueError("预计入职日期不能为空")
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return date(1899, 12, 30) + timedelta(days=int(float(text)))
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise ValueError("预计入职日期请使用 yyyy-mm-dd 格式")
