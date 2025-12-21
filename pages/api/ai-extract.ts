import { spawn } from 'child_process'
import fs from 'fs'
import os from 'os'
import path from 'path'
import { IncomingForm } from 'formidable'
import type { Fields, File as FormidableFile, Files } from 'formidable'
import type { NextApiRequest, NextApiResponse } from 'next'
import type { ExtractedField } from '../../app/lib/extractionRules'

const REPO_ROOT = path.resolve(process.cwd())
const SCRIPT_PATH = path.join(REPO_ROOT, 'scripts', 'extract-tax.py')

const PYTHON_CANDIDATES = [
  process.env.PYTHON_EXTRACTOR_BIN,
  path.join(REPO_ROOT, '.venv311', 'Scripts', 'python.exe'),
  path.join(REPO_ROOT, '.venv311', 'bin', 'python'),
  'python3',
  'python',
]

function resolvePythonBinary(): string {
  for (const candidate of PYTHON_CANDIDATES) {
    if (!candidate) continue
    if (candidate === 'python' || candidate === 'python3') {
      return candidate
    }
    if (fs.existsSync(candidate)) {
      return candidate
    }
  }
  return 'python'
}

const pythonBinary = resolvePythonBinary()

function parseForm(req: NextApiRequest) {
  return new Promise<{ fields: Fields; files: Files }>((resolve, reject) => {
    const form = new IncomingForm({ multiples: false, keepExtensions: true })
    form.parse(req, (err, fields, files) => {
      if (err) {
        reject(err)
      } else {
        resolve({ fields, files })
      }
    })
  })
}

export const config = {
  api: {
    bodyParser: false,
  },
}

type ApiSuccess = {
  ok: true
  text: string
  fields: ExtractedField[]
  summary: Record<string, unknown>
}

type ApiError = { ok: false; message: string }

export default async function handler(req: NextApiRequest, res: NextApiResponse<ApiSuccess | ApiError>) {
  if (req.method !== 'POST') {
    res.setHeader('Allow', 'POST')
    return res.status(405).json({ ok: false, message: 'POST with PDF payload expected' })
  }

  let parsed: { fields: Fields; files: Files }
  try {
    parsed = await parseForm(req)
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Unable to parse upload'
    return res.status(400).json({ ok: false, message })
  }

  const rawDocType = parsed.fields.docType
  const docTypeEntry = Array.isArray(rawDocType) ? rawDocType[0] : rawDocType
  const docType = typeof docTypeEntry === 'string' && docTypeEntry.trim() ? docTypeEntry : '1040'

  const uploaded = parsed.files.file
  const uploadEntry = (Array.isArray(uploaded) ? uploaded[0] : uploaded) as FormidableFile | undefined
  if (!uploadEntry?.filepath) {
    return res.status(400).json({ ok: false, message: 'PDF file not included in request' })
  }

  const summaryTarget = path.join(os.tmpdir(), `summary-${Date.now()}-${Math.random().toString(16).slice(2)}.json`)

  try {
    const extractor = spawn(
      pythonBinary,
      [SCRIPT_PATH, uploadEntry.filepath, '--doc-type', docType, '--summary-file', summaryTarget, '--no-summary'],
      { cwd: REPO_ROOT }
    )

    let stdout = ''
    let stderr = ''

    extractor.stdout.on('data', (chunk) => {
      stdout += chunk
    })
    extractor.stderr.on('data', (chunk) => {
      stderr += chunk
    })

    const extractionResult = await new Promise<{ stdout: string }>((resolve, reject) => {
      extractor.on('error', reject)
      extractor.on('close', (code) => {
        if (code !== 0) {
          return reject(new Error(stderr || `Python extractor exited with code ${code}`))
        }
        resolve({ stdout })
      })
    })

    const rawSummary = await fs.promises.readFile(summaryTarget, 'utf-8')
    const summary = JSON.parse(rawSummary)
    const fields = Array.isArray(summary.fields) ? summary.fields : []
    const text = extractionResult.stdout.trim()

    return res.status(200).json({ ok: true, text, fields, summary })
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Extraction failed'
    return res.status(500).json({ ok: false, message })
  } finally {
    if (uploadEntry?.filepath) {
      try {
        await fs.promises.unlink(uploadEntry.filepath)
      } catch {}
    }
    try {
      await fs.promises.unlink(summaryTarget)
    } catch {}
  }
}
