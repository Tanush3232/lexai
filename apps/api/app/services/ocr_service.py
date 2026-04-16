"""
OCR service for legal act PDFs.
Extracts text using pdfplumber; falls back to Gemini vision for scanned PDFs.
"""
import asyncio
import base64
import io
from typing import Optional

import pdfplumber

from app.core.logging import get_logger

logger = get_logger("ocr_service")

TEXT_THRESHOLD = 100  # avg chars per page to consider text-layer present


async def extract_text_from_pdf(pdf_bytes: bytes) -> dict:
    """
    Extract text from a PDF.

    Returns dict:
      - text: str (full extracted text)
      - total_pages: int
      - has_text_layer: bool
    """
    loop = asyncio.get_running_loop()

    # Step 1: Try pdfplumber text extraction
    plumber_result = await loop.run_in_executor(None, _extract_with_pdfplumber, pdf_bytes)

    total_pages = plumber_result["total_pages"]
    page_texts = plumber_result["page_texts"]

    if total_pages == 0:
        return {"text": "", "total_pages": 0, "has_text_layer": False}

    # Step 2: Check if text layer is adequate
    total_chars = sum(len(t) for t in page_texts)
    avg_chars = total_chars / total_pages if total_pages > 0 else 0

    if avg_chars >= TEXT_THRESHOLD:
        full_text = "\n\n".join(page_texts)
        logger.info("ocr.text_layer_found", pages=total_pages, avg_chars=round(avg_chars))
        return {
            "text": full_text,
            "total_pages": total_pages,
            "has_text_layer": True,
        }

    # Step 3: Scanned PDF — use Gemini vision OCR
    logger.info("ocr.gemini_fallback", pages=total_pages, avg_chars=round(avg_chars))
    try:
        gemini_text = await _ocr_with_gemini(pdf_bytes, total_pages)
        return {
            "text": gemini_text,
            "total_pages": total_pages,
            "has_text_layer": False,
        }
    except Exception as e:
        logger.error("ocr.gemini_failed", error=str(e), exc_info=True)
        # Fall back to whatever pdfplumber got
        return {
            "text": "\n\n".join(page_texts),
            "total_pages": total_pages,
            "has_text_layer": False,
        }


def _extract_with_pdfplumber(pdf_bytes: bytes) -> dict:
    """Synchronous pdfplumber extraction."""
    page_texts = []
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            total_pages = len(pdf.pages)
            for page in pdf.pages:
                text = page.extract_text() or ""
                page_texts.append(text)
    except Exception as e:
        logger.error("ocr.pdfplumber_failed", error=str(e))
        return {"total_pages": 0, "page_texts": []}

    return {"total_pages": total_pages, "page_texts": page_texts}


async def _ocr_with_gemini(pdf_bytes: bytes, total_pages: int) -> str:
    """
    Use Gemini vision to OCR each page of a scanned PDF.
    Converts pages to images via pdf2image, then sends to Gemini.
    """
    from pdf2image import convert_from_bytes
    from app.ai.gemini_client import get_flash_model
    from app.core.config import settings
    import google.generativeai as genai

    loop = asyncio.get_running_loop()

    # Convert PDF pages to images
    images = await loop.run_in_executor(
        None,
        lambda: convert_from_bytes(pdf_bytes, dpi=200, fmt="png"),
    )

    ocr_prompt = (
        "You are an OCR engine. Extract all text from this legal document page "
        "exactly as it appears. Return only the extracted text, no commentary."
    )

    all_texts = []
    model = get_flash_model()

    for i, img in enumerate(images):
        try:
            # Convert PIL image to bytes
            img_buffer = io.BytesIO()
            img.save(img_buffer, format="PNG")
            img_bytes = img_buffer.getvalue()

            # Send to Gemini with image
            response = await loop.run_in_executor(
                None,
                lambda ib=img_bytes: model.generate_content(
                    [
                        ocr_prompt,
                        {"mime_type": "image/png", "data": ib},
                    ],
                    request_options={"timeout": 60},
                ),
            )
            page_text = response.text.strip()
            all_texts.append(page_text)
            logger.debug("ocr.gemini_page_done", page=i + 1, chars=len(page_text))
        except Exception as e:
            logger.warning("ocr.gemini_page_failed", page=i + 1, error=str(e))
            all_texts.append(f"[OCR FAILED FOR PAGE {i + 1}]")

    return "\n\n".join(all_texts)
