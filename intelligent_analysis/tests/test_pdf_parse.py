from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject

from intelligent_analysis.knowledge.parse import read_document


def test_read_pdf_extracts_text(tmp_path: Path):
    pdf = tmp_path / "notice.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    page = writer.pages[0]

    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_ref = writer._add_object(font)
    resources = page[NameObject("/Resources")].get_object()
    resources[NameObject("/Font")] = DictionaryObject({NameObject("/F1"): font_ref})

    content = DecodedStreamObject()
    content[NameObject("/Length")] = NumberObject(0)
    content.set_data(b"BT /F1 12 Tf 72 720 Td (Anhui scenic notice) Tj ET")
    content_ref = writer._add_object(content)
    page[NameObject("/Contents")] = content_ref

    with pdf.open("wb") as file:
        writer.write(file)

    text = read_document(pdf)

    assert "Anhui scenic notice" in text
