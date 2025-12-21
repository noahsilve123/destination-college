/// <reference lib="webworker" />

import { extractFieldsFromText, type DocumentType } from '../lib/extractionRules'
import * as pdfjsLib from 'pdfjs-dist'

// Use CDN for worker to avoid Next.js webpack complexity
pdfjsLib.GlobalWorkerOptions.workerSrc = `https://unpkg.com/pdfjs-dist@${pdfjsLib.version}/build/pdf.worker.min.js`

self.onmessage = async (e: MessageEvent) => {
  const { file, docType } = e.data as { file: File; docType: DocumentType }

  try {
    const buffer = await file.arrayBuffer()
    let text = ''

    // Simple PDF text extraction (no OCR)
    const pdf = await pdfjsLib.getDocument({ data: buffer }).promise
    const maxPages = Math.min(pdf.numPages, 5)

    for (let i = 1; i <= maxPages; i++) {
      const page = await pdf.getPage(i)
      const content = await page.getTextContent()
      const pageText = content.items.map((item) => (item as { str: string }).str).join(' ')
      text += pageText + '\n'
    }

    if (text.trim().length === 0) {
      throw new Error('No selectable text found. Scanned images are not supported in this simplified extractor.')
    }

    // Heuristic analysis using existing regex rules
    const fields = extractFieldsFromText(text, docType)

    self.postMessage({ type: 'complete', result: { text, fields } })

  } catch (error) {
    self.postMessage({ type: 'error', error: (error as Error).message })
  }
}