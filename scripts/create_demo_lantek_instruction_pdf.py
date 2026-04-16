from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader, PdfWriter


PAGE_WIDTH = 1240
PAGE_HEIGHT = 1754
FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
]

SECTIONS = [
    {
        "job_name": "Job1",
        "layout": "1-1/3",
        "date": "2026-01-05",
        "time": "11:36",
        "material": "SM355A",
        "thickness": 12.0,
        "plate_width": 2438.0,
        "plate_length": 6096.0,
        "slab_width": 2438.0,
        "slab_length": 6096.0,
        "hours": 4.01,
        "source_wip_id": 0,
        "output_wip_id": 0,
        "output_width": 0.0,
        "output_length": 0.0,
        "part_summary": "부품 정보 요약 총 종 개 부품 :   1  4",
        "part_rows": [
            ("4 / 219", "2438.00mm*6096.00mm", "BASE-PLATE"),
        ],
        "footer_note": "발생 재공품 없음",
    },
    {
        "job_name": "Job2",
        "layout": "2-2/3",
        "date": "2026-01-05",
        "time": "11:37",
        "material": "SM355A",
        "thickness": 12.0,
        "plate_width": 950.0,
        "plate_length": 2530.0,
        "slab_width": 950.0,
        "slab_length": 1690.0,
        "hours": 0.16,
        "source_wip_id": 28,
        "output_wip_id": 103,
        "output_width": 950.0,
        "output_length": 1690.0,
        "part_summary": "부품 정보 요약 총 종 개 부품 :   1  49",
        "part_rows": [
            ("49 / 1", "950.00mm*2530.00mm", "CVSF-JOB2"),
        ],
        "footer_note": "발생 재공품 WIP 103 / 950.00mm*1690.00mm",
    },
    {
        "job_name": "Job3",
        "layout": "3-3/3",
        "date": "2026-01-05",
        "time": "11:38",
        "material": "SS275",
        "thickness": 20.0,
        "plate_width": 570.0,
        "plate_length": 2450.0,
        "slab_width": 1190.0,
        "slab_length": 570.0,
        "hours": 0.07,
        "source_wip_id": 99,
        "output_wip_id": 104,
        "output_width": 1190.0,
        "output_length": 570.0,
        "part_summary": "부품 정보 요약 총 종 개 부품 :   1  25",
        "part_rows": [
            ("25 / 1", "570.00mm*2450.00mm", "CVSF-JOB3"),
        ],
        "footer_note": "발생 재공품 WIP 104 / 1190.00mm*570.00mm",
    },
]


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in FONT_CANDIDATES:
        path = Path(candidate)
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def _draw_page(section: dict, page_number: int, total_pages: int) -> Image.Image:
    image = Image.new("RGB", (PAGE_WIDTH, PAGE_HEIGHT), "white")
    draw = ImageDraw.Draw(image)

    title_font = _load_font(48)
    heading_font = _load_font(28)
    body_font = _load_font(24)
    small_font = _load_font(20)

    draw.text((70, 60), "보고서", fill="#1e1e1e", font=title_font)
    draw.text(
        (210, 70),
        f"{page_number} / {total_pages}    {section['date']}  {section['time']}",
        fill="#646b78",
        font=small_font,
    )

    y = 170
    draw.text((70, y), "타이포그래피 세부정보 :", fill="#4f5563", font=small_font)
    y += 40
    draw.text((70, y), "排版", fill="#4f5563", font=small_font)
    y += 65

    draw.text((70, y), section["part_summary"], fill="#121826", font=heading_font)
    y += 55
    draw.text(
        (70, y),
        f"슬랩 사이즈 :  {section['slab_width']:.2f}mm*{section['slab_length']:.2f}mm",
        fill="#121826",
        font=body_font,
    )
    y += 42
    draw.text(
        (70, y),
        f"판재 크기 :  {section['plate_width']:.2f}mm*{section['plate_length']:.2f}mm",
        fill="#121826",
        font=body_font,
    )
    y += 42
    draw.text(
        (70, y),
        (
            f"단일 가공 시간 시간 : {section['hours']:.2f}   가공 횟수 : 1   "
            f"판재 두께 : {section['thickness']:.2f}mm   판재 재질 : {section['material']}"
        ),
        fill="#121826",
        font=body_font,
    )
    y += 54
    draw.text((70, y), f"레이아웃 {section['layout']}", fill="#1f3b77", font=heading_font)
    y += 28
    draw.line((70, y, PAGE_WIDTH - 70, y), fill="#d5dbe6", width=2)
    y += 36

    draw.text((70, y), f"작업: {section['job_name']}", fill="#1f3b77", font=body_font)
    y += 38
    draw.text(
        (70, y),
        f"대응 재공품 ID: {section['source_wip_id']} / 발생 재공품 ID: {section['output_wip_id']}",
        fill="#121826",
        font=body_font,
    )
    y += 36
    if section["output_wip_id"]:
        draw.text(
            (70, y),
            f"발생 재공품 크기: {section['output_width']:.2f}mm*{section['output_length']:.2f}mm",
            fill="#121826",
            font=body_font,
        )
        y += 50
    else:
        y += 20

    table_top = y + 20
    draw.rounded_rectangle((70, table_top, PAGE_WIDTH - 70, table_top + 320), radius=12, outline="#d5dbe6", width=2)
    draw.rectangle((70, table_top, PAGE_WIDTH - 70, table_top + 60), fill="#f2f5fa")
    headers = ["부품 개수", "부품 사이즈", "부품 이름"]
    x_positions = [100, 420, 860]
    for header, x in zip(headers, x_positions):
        draw.text((x, table_top + 16), header, fill="#5c6575", font=body_font)

    row_y = table_top + 90
    for row in section["part_rows"]:
        for value, x in zip(row, x_positions):
            draw.text((x, row_y), value, fill="#121826", font=body_font)
        row_y += 50

    footer_y = table_top + 360
    draw.rounded_rectangle((70, footer_y, PAGE_WIDTH - 70, footer_y + 90), radius=14, fill="#f7f9fc")
    draw.text((90, footer_y + 18), section["footer_note"], fill="#c73f3f" if section["output_wip_id"] else "#5c6575", font=heading_font)

    return image


def _escape_pdf_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _build_text_stream(section: dict) -> str:
    lines = [
        f"REPORT PAGE {section['job_name']}",
        "PART SUMMARY",
        f"JOB NAME : {section['job_name']}",
        f"SOURCE WIP ID : {section['source_wip_id']}",
        f"OUTPUT WIP ID : {section['output_wip_id']}",
        f"SLAB SIZE : {section['slab_width']:.2f}mm*{section['slab_length']:.2f}mm",
        f"PLATE SIZE : {section['plate_width']:.2f}mm*{section['plate_length']:.2f}mm",
        f"CUTTING TIME HOURS : {section['hours']:.2f}",
        f"THICKNESS : {section['thickness']:.2f}mm",
        f"MATERIAL : {section['material']}",
        f"LAYOUT {section['layout']}",
    ]
    if section["output_wip_id"]:
        lines.append(
            f"OUTPUT SIZE : {section['output_width']:.2f}mm*{section['output_length']:.2f}mm"
        )

    commands = ["BT", "/F1 14 Tf", "18 TL", "40 1680 Td"]
    for line in lines:
        commands.append(f"({_escape_pdf_text(line)}) Tj")
        commands.append("T*")
    commands.append("ET")
    return "\n".join(commands)


def _build_text_layer_pdf() -> bytes:
    object_defs: list[str] = []
    page_ids: list[int] = []
    next_id = 3

    for section in SECTIONS:
        page_id = next_id
        content_id = next_id + 1
        next_id += 2
        page_ids.append(page_id)
        stream = _build_text_stream(section).encode("latin-1")
        object_defs.append(
            f"{page_id} 0 obj\n"
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
            f"/Resources << /Font << /F1 100 0 R >> >> /Contents {content_id} 0 R >>\n"
            "endobj\n"
        )
        object_defs.append(
            f"{content_id} 0 obj\n<< /Length {len(stream)} >>\nstream\n"
            f"{stream.decode('latin-1')}\nendstream\nendobj\n"
        )

    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    header_objects = [
        "1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        f"2 0 obj\n<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>\nendobj\n",
    ]
    font_object = "100 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"

    object_map = {1: header_objects[0], 2: header_objects[1], 100: font_object}
    object_number = 3
    for obj in object_defs:
        while object_number in object_map:
            object_number += 1
        first_line = obj.split(" ", 1)[0]
        object_map[int(first_line)] = obj

    ordered_ids = sorted(object_map.keys())
    pdf_parts = [b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"]
    offsets = {0: 0}
    for object_id in ordered_ids:
        offsets[object_id] = sum(len(part) for part in pdf_parts)
        pdf_parts.append(object_map[object_id].encode("latin-1"))

    xref_offset = sum(len(part) for part in pdf_parts)
    max_id = max(ordered_ids)
    xref_lines = [f"0 {max_id + 1}", "0000000000 65535 f "]
    for object_id in range(1, max_id + 1):
        offset = offsets.get(object_id, 0)
        in_use = "n" if object_id in offsets else "f"
        xref_lines.append(f"{offset:010d} 00000 {in_use} ")

    trailer = (
        "xref\n"
        + "\n".join(xref_lines)
        + "\ntrailer\n"
        + f"<< /Size {max_id + 1} /Root 1 0 R >>\n"
        + f"startxref\n{xref_offset}\n%%EOF\n"
    )
    pdf_parts.append(trailer.encode("latin-1"))
    return b"".join(pdf_parts)


def build_pdf_bytes() -> bytes:
    images = [_draw_page(section, idx, len(SECTIONS)).convert("RGB") for idx, section in enumerate(SECTIONS, start=1)]

    image_buffer = BytesIO()
    images[0].save(image_buffer, format="PDF", save_all=True, append_images=images[1:])
    image_buffer.seek(0)

    text_reader = PdfReader(BytesIO(_build_text_layer_pdf()))
    image_reader = PdfReader(image_buffer)
    writer = PdfWriter()

    for text_page, image_page in zip(text_reader.pages, image_reader.pages):
        text_page.merge_page(image_page)
        writer.add_page(text_page)

    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def main() -> None:
    output_path = Path(__file__).resolve().parents[1] / "demo_data" / "demo_lantek_instruction.pdf"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(build_pdf_bytes())
    print(output_path)


if __name__ == "__main__":
    main()
