## Destination College FAFSA Helper

This Next.js application powers the FAFSA prep and scholarship planning pages for Destination College. The FAFSA helper now runs entirely in the browser using a Web Worker (PDF.js + Tesseract) to read PDFs and apply heuristic extraction rules. No external Go service or other backend is required for the main workflow.

## Prerequisites

- Node.js 20+
- (Optional) Python 3.11/3.10 with spaCy and Tesseract only if you want to run the CLI extraction script for debugging (see below).

## Local development

1. Install dependencies

   ```bash
   npm install
   ```

2. Run the frontend

   ```bash
   npm run dev
   ```

Open [http://localhost:3000](http://localhost:3000) to use the site. Dropped PDFs/images are parsed locally in the browser worker and never leave the tab.

- `npm run build:worker` runs automatically before `dev`, `build`, or `start` thanks to the `pre*` scripts. Run it manually if you edit `app/workers/extractor.worker.ts` so the public bundle stays in sync.

## Optional reminder queue

- Set `REDIS_URL` to a reachable Redis instance to enable `/api/reminders`.
- Run `node worker/reminderWorker.js` locally to process queued reminder jobs (currently logs to console; wire to your email/SMS provider in production).

## PWA/offline

- A service worker (`public/sw.js`) caches the planner page for offline viewing. The manifest is served from `/manifest.webmanifest`.
- Add `public/icon-192.png` and `public/icon-512.png` to improve the install prompt on mobile.

## Testing the in-browser extractor

- Run `npm run dev` and drop a sample PDF into the FAFSA helper. Progress updates come from the worker thread.
- If the spinner stays at 0 %, open the browser console. Worker errors are posted there and will also surface in the UI.

## Manual PDF extraction (pdfminer + pdfplumber)

If you just need to dump text from a tax form for local testing (without opening the browser), run the Python pipeline based on pdfplumber/pdfminer, Tesseract OCR, and the existing heuristic parser.

1. Install the Python dependencies and the spaCy model that powers the NLP summary (use Python 3.11 or 3.10; spaCy/pydantic do not yet support 3.14):

   ```bash
   python -m pip install -r requirements.txt
   python -m spacy download en_core_web_sm
   ```

2. Extract text, emit a JSON summary, and capture the raw text for downstream parsing:

   ```bash
   python scripts/extract-tax.py path/to/Form1040.pdf --doc-type 1040 --summary-file tmp_summary.json > tmp_pdf_text.txt
   ```

   - By default the script also prints a JSON summary (fields, NLP entities, OCR pages, etc.) to stderr; the `--summary-file` flag writes the same payload to a file.
   - Set `--no-summary` to suppress the extra JSON output, or `--skip-node` if you do not have Node available for the heuristic parser (`--node-bin` lets you point at a custom Node binary).
   - `--max-pages 5`, `--min-text-chars 120`, `--no-ocr`, and `--ocr-dpi` give you control over how much of the document is scanned.
   - Tesseract OCR fires when the native text layer is sparse; install [Tesseract](https://github.com/tesseract-ocr/tesseract) on your system and ensure the binary is on your PATH.

3. (Optional) If you already have extracted text, you can still run the heuristic analysis directly:

   ```bash
   node scripts/run-extract-simple.js tmp_pdf_text.txt 1040
   ```

The pipeline prints progress when `--verbose` is set and logs which pages required OCR or when the Node helper fails.

## Environment variables

- `REDIS_URL` – enables reminder queue processing for `/api/reminders`.
- `NEXT_PUBLIC_SITE_URL` – canonical site URL for building absolute links.

## Local development quickstart

```bash
npm install
npm run dev
```

If you need reminders locally, start a Redis instance and run `node worker/reminderWorker.js` in another terminal.

## Tests and quality gates

```bash
npm run lint
npm run test:e2e          # Playwright (includes unit-style tests)
npm run a11y:axe          # axe-core scans for key routes
npm run a11y              # pa11y-ci across primary pages
```

## QA checklist (manual)

- Scholarships planner: build list, save item, generate share link, export calendar (ICS downloads), reminders error message when REDIS_URL missing.
- FAFSA helper: upload PDF, run “Scan document”, verify extracted values and contrast of status pills.
- Programs/resources pages: heading order and focus outlines visible when tabbing.
- Offline/PWA: load `/resources/scholarships` then toggle offline and confirm cached content renders; service worker registered via `public/sw.js`.

## Reminder queue

- Set `REDIS_URL` to a reachable Redis instance.
- Run `node worker/reminderWorker.js` to process jobs (currently logs; wire to email/SMS provider for production).

## PWA / offline notes

- `public/sw.js` caches the planner page and assets for offline viewing.
- `app/manifest.ts` defines icons/metadata; add `public/icon-192.png` and `public/icon-512.png` for install prompts.
