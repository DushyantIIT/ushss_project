"""
app/excel.py
────────────
Zero-dependency, pure-Python generator for valid OpenXML (.xlsx) Excel spreadsheets.
Compatible with Microsoft Excel, Google Sheets, Apple Numbers, and LibreOffice Calc.
"""

import io
import re
import zipfile
from xml.sax.saxutils import escape as xml_escape


def sanitize_filename_part(text: str) -> str:
    """Sanitize a text component for inclusion in a filename."""
    if not text:
        return "N-A"
    clean = re.sub(r"[^\w\s-]", "", str(text)).strip()
    return re.sub(r"[-\s]+", "_", clean)


def generate_attendance_filename(session_info: dict) -> str:
    """
    Generate an informative filename containing programme, batch, subject, date, and time.
    e.g. Attendance_MA_English_Batch_2023_Advanced_Literature_2026-10-10_09-00.xlsx
    """
    prog = sanitize_filename_part(session_info.get("programme") or "Class")
    batch = sanitize_filename_part(session_info.get("batch") or "")
    subj = sanitize_filename_part(session_info.get("subject") or "General")
    date_str = sanitize_filename_part(str(session_info.get("date") or "Date")[:10])
    time_str = sanitize_filename_part(str(session_info.get("start_time") or "")[:5].replace(":", "-"))

    parts = ["Attendance", prog]
    if batch:
        parts.append(f"Batch_{batch}")
    parts.extend([subj, date_str])
    if time_str and time_str != "N-A":
        parts.append(time_str)

    return f"{'_'.join(parts)}.xlsx"


def build_attendance_xlsx(session_info: dict, present_list: list, absent_list: list) -> bytes:
    """
    Builds an authentic .xlsx workbook containing the session details,
    summary statistics, and complete list of present and absent students.
    """
    buf = io.BytesIO()
    rows = []

    def add_row(r_idx: int, cells: list):
        c_xml = []
        for col_letter, val, is_bold in cells:
            s_attr = ' s="1"' if is_bold else ''
            safe_val = xml_escape(str(val) if val is not None else "")
            c_xml.append(f'<c r="{col_letter}{r_idx}" t="inlineStr"{s_attr}><is><t>{safe_val}</t></is></c>')
        rows.append(f'<row r="{r_idx}">{" ".join(c_xml)}</row>')

    # 1. Official Header
    add_row(1, [("A", "UNIVERSITY SCHOOL OF HUMANITIES & SOCIAL SCIENCES (USHSS)", True)])
    add_row(2, [("A", "Guru Gobind Singh Indraprastha University, Sector 16C, Dwarka, New Delhi", False)])
    add_row(3, [("A", "OFFICIAL CLASS ATTENDANCE REPORT", True)])
    add_row(4, [])

    # 2. Class & Session Metadata
    subject = session_info.get("subject") or "N/A"
    date_val = str(session_info.get("date") or "N/A")[:10]
    prog = session_info.get("programme") or "N/A"
    batch = session_info.get("batch") or "N/A"
    sec = session_info.get("section")
    class_str = f"{prog} — Batch {batch}" + (f" (Sec {sec})" if sec else "")
    start_t = str(session_info.get("start_time") or "")[:5]
    end_t = str(session_info.get("end_time") or "")[:5]
    time_slot = f"{start_t} to {end_t}" if start_t and end_t else "Standard Session"
    faculty_name = session_info.get("faculty_name") or "Faculty Instructor"
    room = session_info.get("room") or "TBA"

    add_row(5, [
        ("A", "Course / Subject:", True), ("B", subject, False),
        ("D", "Date:", True), ("E", date_val, False)
    ])
    add_row(6, [
        ("A", "Programme & Batch:", True), ("B", class_str, False),
        ("D", "Time Slot:", True), ("E", time_slot, False)
    ])
    add_row(7, [
        ("A", "Faculty Member:", True), ("B", faculty_name, False),
        ("D", "Room / Venue:", True), ("E", room, False)
    ])

    total_enrolled = len(present_list) + len(absent_list)
    present_count = len(present_list)
    absent_count = len(absent_list)
    pct = round((present_count / total_enrolled * 100), 1) if total_enrolled else 0.0

    add_row(8, [
        ("A", "Total Enrolled:", True), ("B", total_enrolled, False),
        ("C", "Total Present:", True), ("D", present_count, False),
        ("E", "Total Absent:", True), ("F", absent_count, False),
        ("G", "Attendance Rate:", True), ("H", f"{pct}%", True)
    ])
    add_row(9, [])

    # 3. Table Column Headers
    add_row(10, [
        ("A", "S.No.", True),
        ("B", "Enrollment / Roll No.", True),
        ("C", "Student Full Name", True),
        ("D", "Attendance Status", True),
        ("E", "Time Marked", True),
        ("F", "Verification Details", True),
    ])

    curr_row = 11
    sno = 1

    # Section A: Present Students
    for s in present_list:
        add_row(curr_row, [
            ("A", sno, False),
            ("B", s.get("roll") or s.get("enrollment_no") or "", False),
            ("C", s.get("name") or s.get("full_name") or "", False),
            ("D", "PRESENT", True),
            ("E", s.get("marked_at") or "Marked Present", False),
            ("F", s.get("notes") or s.get("verification") or "Verified", False),
        ])
        curr_row += 1
        sno += 1

    # Section B: Absent Students
    for s in absent_list:
        add_row(curr_row, [
            ("A", sno, False),
            ("B", s.get("roll") or s.get("enrollment_no") or "", False),
            ("C", s.get("name") or s.get("full_name") or "", False),
            ("D", "ABSENT", True),
            ("E", "—", False),
            ("F", "Did not mark attendance", False),
        ])
        curr_row += 1
        sno += 1

    sheet_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>
    {" ".join(rows)}
  </sheetData>
</worksheet>"""

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>""")
        z.writestr("_rels/.rels", """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>""")
        z.writestr("xl/_rels/workbook.xml.rels", """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>""")
        z.writestr("xl/workbook.xml", """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="Attendance" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>""")
        z.writestr("xl/styles.xml", """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="2">
    <font><sz val="11"/><name val="Calibri"/></font>
    <font><b/><sz val="11"/><name val="Calibri"/></font>
  </fonts>
  <fills count="2">
    <fill><patternFill patternType="none"/></fill>
    <fill><patternFill patternType="gray125"/></fill>
  </fills>
  <borders count="1">
    <border><left/><right/><top/><bottom/><diagonal/></border>
  </borders>
  <cellStyleXfs count="1">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0"/>
  </cellStyleXfs>
  <cellXfs count="2">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
    <xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>
  </cellXfs>
</styleSheet>""")
        z.writestr("xl/worksheets/sheet1.xml", sheet_xml)

    return buf.getvalue()
