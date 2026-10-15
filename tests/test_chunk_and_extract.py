from app.rag.chunk import ChunkConfig, chunk_blocks, estimate_tokens
from app.rag.extract import ExtractedBlock, extract_document
from tests.helpers import docx_bytes, pdf_bytes


def test_heading_and_paragraph_splits_keep_sections_apart():
    blocks = [
        ExtractedBlock("heading", "Refunds", ["Refunds"], heading_level=1),
        ExtractedBlock("paragraph", "Refunds are available for 30 days.", ["Refunds"]),
        ExtractedBlock("heading", "Shipping", ["Shipping"], heading_level=1),
        ExtractedBlock("paragraph", "Standard shipping takes five business days.", ["Shipping"]),
    ]
    chunks = chunk_blocks(blocks, ChunkConfig(min_tokens=5, max_tokens=40, overlap_ratio=0.1))
    assert [chunk.heading_path for chunk in chunks] == ["Refunds", "Shipping"]
    assert "Refunds are available" in chunks[0].text
    assert "shipping" not in chunks[0].text.lower()
    assert chunks[0].embed_text.startswith("Refunds")


def test_paragraph_split_and_overlap_follow_config():
    paragraph = "Policy detail sentence. " * 30
    blocks = [ExtractedBlock("paragraph", paragraph, ["Policies"], page=2)]
    config = ChunkConfig(min_tokens=20, max_tokens=40, overlap_ratio=0.25)
    chunks = chunk_blocks(blocks, config)
    assert len(chunks) >= 2
    assert all(estimate_tokens(chunk.text) <= config.max_tokens + 5 for chunk in chunks)
    overlap = chunks[0].text[-10:]
    assert overlap in chunks[1].embed_text
    assert not chunks[1].text.startswith(overlap)
    assert chunks[0].page_from == 2


def test_docx_preserves_heading_order_and_table_rows():
    data = docx_bytes(
        [
            ("Heading 1", "Plans"),
            ("Normal", "Choose a plan before the renewal date."),
            ("Heading 2", "Limits"),
            ("Normal", "The starter plan includes three seats."),
        ],
        tables=[[["Plan", "Price"], ["Pro", "20"]]],
    )
    extracted = extract_document("plans.docx", data)
    text = "\n".join(block.text for block in extracted.blocks)
    assert text.index("Choose a plan") < text.index("starter plan")
    assert "Plan | Price" in text
    assert "Pro | 20" in text
    limits = next(block for block in extracted.blocks if "starter plan" in block.text)
    assert limits.heading_path == ["Plans", "Limits"]


def test_pdf_keeps_page_numbers_and_warns_on_empty_page():
    data = pdf_bytes(
        [
            [("Refund policy", 22), ("Customers may request a refund within 30 days.", 11)],
            [],
        ]
    )
    extracted = extract_document("policy.pdf", data)
    assert extracted.page_count == 2
    assert any("Page 2" in warning for warning in extracted.warnings)
    pages = {block.page for block in extracted.blocks if block.kind != "heading"}
    assert pages == {1}
    assert any("refund within 30 days" in block.text for block in extracted.blocks)
