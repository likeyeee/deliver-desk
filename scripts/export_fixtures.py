"""Export local synthetic test pages; never requests the real website."""

import ast
import base64
import io
import json
import zipfile
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

root = Path(__file__).resolve().parents[1]
tree = ast.parse((root / "tests/test_browser.py").read_text(encoding="utf-8"))
fixtures = {
    node.targets[0].id: ast.literal_eval(node.value)
    for node in tree.body
    if isinstance(node, ast.Assign)
    and isinstance(node.targets[0], ast.Name)
    and node.targets[0].id.endswith("_HTML")
}
zhaopin_tree = ast.parse((root / "tests/test_zhaopin.py").read_text(encoding="utf-8"))
fixtures.update(
    {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in zhaopin_tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id.endswith("_HTML")
    }
)
resume_text = "示例候选人\n2023—2025 年担任产品经理，负责知识库问答项目的需求分析与效果评估。\n核心技能：Python、SQL。"
fixtures["RESUME_TEXT"] = resume_text
fixtures["RESUME_PROFILE"] = {
    "summary": "有知识库问答项目经验的产品经理",
    "skills": ["Python", "SQL"],
    "experiences": ["2023—2025 年负责知识库问答项目的需求分析与效果评估"],
    "strengths": ["通过知识库问答项目积累需求分析与效果评估经验"],
}
docx = io.BytesIO()
with zipfile.ZipFile(docx, "w", compression=zipfile.ZIP_DEFLATED) as archive:
    archive.writestr(
        "word/document.xml",
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
        + "".join(f"<w:p><w:r><w:t>{line}</w:t></w:r></w:p>" for line in resume_text.splitlines())
        + "</w:body></w:document>",
    )
fixtures["RESUME_DOCX"] = base64.b64encode(docx.getvalue()).decode()
pdf = PdfWriter()
page = pdf.add_blank_page(600, 800)
font = DictionaryObject(
    {
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    }
)
page[NameObject("/Resources")] = DictionaryObject(
    {NameObject("/Font"): DictionaryObject({NameObject("/F1"): pdf._add_object(font)})}
)
stream = DecodedStreamObject()
stream.set_data(b"BT /F1 12 Tf 50 700 Td (Product manager with Python and SQL experience.) Tj ET")
page[NameObject("/Contents")] = pdf._add_object(stream)
pdf_data = io.BytesIO()
pdf.write(pdf_data)
fixtures["RESUME_PDF"] = base64.b64encode(pdf_data.getvalue()).decode()
print(json.dumps(fixtures, ensure_ascii=False))
