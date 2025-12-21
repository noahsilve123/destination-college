#!/usr/bin/env python3
"""Extract text from tax PDFs using pdfplumber (pdfminer) with optional Tesseract OCR fallback."""

import argparse
import json
import logging
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, List, Optional, Type


REPO_ROOT = Path(__file__).resolve().parent.parent
NODE_EXTRACT_SCRIPT = REPO_ROOT / "scripts" / "run-extract-simple.js"


@dataclass
class _OcrEngine:
    image_to_string: Callable[..., str]
    TesseractError: Type[BaseException]
    TesseractNotFoundError: Type[BaseException]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "source",
        type=Path,
        help="PDF document (for example, Form 1040)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Write extracted text to a file instead of stdout",
    )
    parser.add_argument(
        "-n",
        "--max-pages",
        type=int,
        metavar="N",
        help="Limit extraction to the first N pages",
    )
    parser.add_argument(
        "--ocr",
        dest="ocr",
        action="store_true",
        help="Run OCR when the native text layer is missing or small",
    )
    parser.add_argument(
        "--no-ocr",
        dest="ocr",
        action="store_false",
        help="Skip OCR fallback and rely on the native text layer only",
    )
    parser.set_defaults(ocr=True)
    parser.add_argument(
        "--min-text-chars",
        type=int,
        default=80,
        help="Minimum characters on a page before OCR is skipped",
    )
    parser.add_argument(
        "--ocr-lang",
        default="eng",
        help="Language code to pass to Tesseract when OCR runs",
    )
    parser.add_argument(
        "--ocr-dpi",
        type=int,
        default=220,
        help="Rasterization DPI used when generating OCR images",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable INFO logging to watch progress",
    )
    parser.add_argument(
        "--doc-type",
        default="1040",
        choices=["1040", "W-2", "1099", "SSN Letter", "Other"],
        help="Document type hint for the heuristic parser",
    )
    parser.add_argument(
        "--summary-file",
        type=Path,
        help="Write the structured summary (fields + NLP insights) to this file",
    )
    parser.add_argument(
        "--no-summary",
        action="store_true",
        help="Suppress the JSON summary output on stderr",
    )
    parser.add_argument(
        "--skip-node",
        action="store_true",
        help="Skip the Node.js heuristic parser (text-only output)",
    )
    parser.add_argument(
        "--node-bin",
        default="node",
        help="Node executable path for running the heuristic parser",
    )
    parser.add_argument(
        "--nlp-model",
        default="en_core_web_sm",
        help="spaCy model used for entity detection summary",
    )
    return parser


def _load_ocr_engine() -> _OcrEngine:
    try:
        from pytesseract import TesseractError, TesseractNotFoundError, image_to_string
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "pytesseract is not installed; run `pip install -r requirements.txt`"
        ) from exc
    return _OcrEngine(
        image_to_string=image_to_string,
        TesseractError=TesseractError,
        TesseractNotFoundError=TesseractNotFoundError,
    )


def _run_ocr(page: Any, engine: _OcrEngine, lang: str, dpi: int) -> str:
    image = page.to_image(resolution=dpi).original
    text = ""
    try:
        text = engine.image_to_string(image.convert("RGB"), lang=lang)
    except engine.TesseractNotFoundError as exc:
        raise RuntimeError(
            "Tesseract binary not found; install the Tesseract OCR runtime"
        ) from exc
    except engine.TesseractError as exc:
        raise RuntimeError(f"OCR failure: {exc}") from exc
    return text or ""


def _load_spacy_model(model_name: str):
    # spaCy 3.x and pydantic v1 are not forward-compatible with Python 3.14+ yet.
    # Bail out early to avoid noisy tracebacks when running in newer interpreters.
    if sys.version_info >= (3, 14):
        logging.warning("spaCy is skipped on Python %s.%s; summaries disabled.", sys.version_info.major, sys.version_info.minor)
        return None
    try:
        import spacy
    except ModuleNotFoundError:
        logging.warning("spaCy is not installed; NLP summaries will be skipped.")
        return None
    try:
        return spacy.load(model_name)
    except OSError as exc:
        logging.warning(
            "spaCy model '%s' is unavailable: %s\nRun 'python -m spacy download %s' to install it.",
            model_name,
            exc,
            model_name,
        )
        return None


def _unique_values(values: List[str], limit: int):
    seen = set()
    unique = []
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
        if len(unique) >= limit:
            break
    return unique


def summarize_entities(text: str, nlp_model):
    if not nlp_model:
        return {
            "persons": [],
            "dates": [],
            "gpe": [],
            "organizations": [],
            "money": [],
        }
    trimmed = text[:20000]
    doc = nlp_model(trimmed)
    entities = {"persons": [], "dates": [], "gpe": [], "organizations": [], "money": []}
    for ent in doc.ents:
        label = ent.label_
        if label == "PERSON":
            entities["persons"].append(ent.text)
        elif label == "DATE":
            entities["dates"].append(ent.text)
        elif label == "GPE":
            entities["gpe"].append(ent.text)
        elif label == "ORG":
            entities["organizations"].append(ent.text)
        elif label == "MONEY":
            entities["money"].append(ent.text)
    return {key: _unique_values(values, limit=6) for key, values in entities.items()}


def run_node_extractor(text: str, doc_type: str, node_bin: str, script_path: Path):
    if not script_path.exists():
        return [], {"status": "missing-script", "detail": str(script_path)}
    temp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as tmp:
            tmp.write(text)
            tmp.flush()
            temp_path = Path(tmp.name)
        completed = subprocess.run(
            [node_bin, str(script_path), str(temp_path), doc_type],
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(completed.stdout), {"status": "ok"}
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() or exc.stdout.strip() or str(exc)
        logging.warning("Node heuristic parser failed: %s", detail)
        return [], {"status": "error", "detail": detail, "returncode": exc.returncode}
    except FileNotFoundError as exc:
        logging.warning("Unable to run node heuristics: %s", exc)
        return [], {"status": "missing-node", "detail": str(exc)}
    except json.JSONDecodeError as exc:
        logging.warning("Node heuristic parser emitted invalid JSON: %s", exc)
        return [], {"status": "invalid-json", "detail": str(exc)}
    finally:
        if temp_path and temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def build_summary(
    text: str,
    doc_type: str,
    nlp_model,
    fallback_pages: List[int],
    pages_processed: int,
    source: Path,
    args,
):
    doc_summary = {
        "document": str(source),
        "doc_type": doc_type,
        "pages_processed": pages_processed,
        "ocr_pages": fallback_pages,
        "text_length": len(text),
        "ocr_enabled": args.ocr,
    }
    if args.skip_node:
        fields, node_status = [], {"status": "disabled"}
    else:
        fields, node_status = run_node_extractor(text, doc_type, args.node_bin, NODE_EXTRACT_SCRIPT)
    doc_summary["fields"] = fields
    doc_summary["node_parser"] = node_status
    doc_summary["nlp_summary"] = summarize_entities(text, nlp_model)
    return doc_summary


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.max_pages is not None and args.max_pages <= 0:
        parser.error("--max-pages must be greater than zero")

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="[%(levelname)s] %(message)s",
    )

    source = args.source.expanduser().resolve()
    if not source.exists():
        raise RuntimeError(f"File not found: {source}")
    if not source.is_file():
        raise RuntimeError(f"Expected a file, got: {source}")

    try:
        import pdfplumber
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "pdfplumber is not installed; run `pip install -r requirements.txt`"
        ) from exc

    ocr_engine: Optional[_OcrEngine] = None
    if args.ocr:
        try:
            ocr_engine = _load_ocr_engine()
        except RuntimeError as exc:
            logging.warning("OCR fallback disabled: %s", exc)
            ocr_engine = None

    text_blocks: List[str] = []
    fallback_pages: List[int] = []
    nlp_model = None
    pages_processed = 0

    with pdfplumber.open(source) as pdf:
        total_pages = len(pdf.pages)
        max_pages = min(args.max_pages or total_pages, total_pages)
        logging.info("Processing %d/%d pages", max_pages, total_pages)
        pages_processed = max_pages

        for index, page in enumerate(pdf.pages[:max_pages]):
            page_number = index + 1
            raw_text = (page.extract_text() or "").strip()
            logging.info("Page %d text length %d", page_number, len(raw_text))

            run_ocr = bool(
                ocr_engine
                and len(raw_text) < args.min_text_chars
                and args.ocr
            )

            if run_ocr:
                logging.info("Running OCR on page %d", page_number)
                try:
                    ocr_text = _run_ocr(page, ocr_engine, args.ocr_lang, args.ocr_dpi)
                except RuntimeError as exc:
                    logging.warning("OCR failed for page %d: %s", page_number, exc)
                    text_blocks.append(raw_text)
                    continue
                fallback_pages.append(page_number)
                text_blocks.append(ocr_text or raw_text)
            else:
                text_blocks.append(raw_text)

    final_text = "\n\n".join(text_blocks).strip()
    should_summarize = not args.no_summary or bool(args.summary_file)
    summary_data = None
    if should_summarize:
        nlp_model = _load_spacy_model(args.nlp_model)
        summary_data = build_summary(
            final_text,
            doc_type=args.doc_type,
            nlp_model=nlp_model,
            fallback_pages=fallback_pages,
            pages_processed=pages_processed,
            source=source,
            args=args,
        )

    if args.output:
        target = args.output.expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(final_text, encoding="utf-8")
        logging.info("Wrote extracted text to %s", target)
    else:
        print(final_text)

    if summary_data:
        if args.summary_file:
            summary_target = args.summary_file.expanduser()
            summary_target.parent.mkdir(parents=True, exist_ok=True)
            summary_target.write_text(json.dumps(summary_data, indent=2), encoding="utf-8")
            logging.info("Wrote summary JSON to %s", summary_target)
        if not args.no_summary:
            print(json.dumps(summary_data, indent=2), file=sys.stderr)

    if fallback_pages:
        logging.info("OCR was used on pages: %s", ", ".join(map(str, fallback_pages)))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
