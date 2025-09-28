# KMRL Document Summarizer MVP

A minimal local CLI to ingest mixed documents (PDF, DOCX, TXT), extract text, generate a quick extractive summary, detect action items, tag to departments, and write outputs:
- summary.csv (all documents)
- route_<TAG>.jsonl (per-tag queues)
- optional per-file JSON

This is intentionally simple for fast setup and offline-friendly operation. No external APIs are used.

## Features
- Supported inputs: .pdf, .docx, .txt (recursive folder scan)
- Language detection (basic) to adjust summarization stopwords (English and minimal Malayalam seed list)
- Extractive summaries (top-scoring sentences)
- Action item heuristics (must/shall/due/by/etc.)
- Rule-based tagging for quick routing (Engineering, Safety, Procurement/Finance, etc.)
- Traceability: source absolute path and file SHA-256

## Limitations (MVP)
- Scanned PDFs/embedded images are not OCR’d
- Malayalam summarization uses a light heuristic (extend stopwords & tokenization for better quality)
- No email/SharePoint/Maximo connectors yet; ingest is via a folder
- No security model; this is a local utility

## Setup (Windows PowerShell)

```powershell path=null start=null
# From your working directory
python -m venv .\kmrl-doc-mvp\.venv
.\kmrl-doc-mvp\.venv\Scripts\python -m pip install -U pip
.\kmrl-doc-mvp\.venv\Scripts\python -m pip install -r .\kmrl-doc-mvp\requirements.txt
```

## Run

```powershell path=null start=null
# Process sample docs
.\kmrl-doc-mvp\.venv\Scripts\python .\kmrl-doc-mvp\process_docs.py --input .\kmrl-doc-mvp\samples --out .\kmrl-doc-mvp\output --per-file-json
```

Outputs will appear under `kmrl-doc-mvp\output`:
- `summary.csv`
- `route_*.jsonl`
- `<file>.json` (if `--per-file-json` is used)

## Extend
- OCR: add `pytesseract` + `pdf2image` and a Tesseract install for scanned PDFs
- Better Malayalam: add a tokenizer and expanded stopword list, consider sentence segmentation rules
- Tagging: move to a YAML config and enrich with department-specific keywords
- Connectors: add email/SharePoint/Maximo ingestion via IMAP/Graph/REST
- Alerting: watch for action items with dates and push to Teams/Email
