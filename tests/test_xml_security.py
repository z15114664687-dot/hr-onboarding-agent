from io import BytesIO
import zipfile

import pytest

from app.services.candidate_import import _read_xlsx_rows


def test_candidate_import_rejects_xml_entity_expansion() -> None:
    worksheet = b"""<?xml version="1.0"?>
<!DOCTYPE worksheet [<!ENTITY synthetic "expanded-value">]>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData><row><c t="inlineStr"><is><t>&synthetic;</t></is></c></row></sheetData>
</worksheet>
"""
    payload = BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)

    with pytest.raises(ValueError, match="有效的 .xlsx"):
        _read_xlsx_rows(payload.getvalue())
