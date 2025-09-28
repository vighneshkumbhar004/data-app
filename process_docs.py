#!/usr/bin/env python3
import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import List, Dict, Tuple, Optional, Iterable

from langdetect import detect, DetectorFactory
from tqdm import tqdm

# Optional deps based on file types
try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None  # type: ignore

try:
    import docx  # python-docx
except Exception:
    docx = None  # type: ignore

try:
    import chardet
except Exception:
    chardet = None  # type: ignore

DetectorFactory.seed = 0  # deterministic language detection

SUPPORTED_EXTS = {".pdf", ".docx", ".txt"}

ENG_STOP = set(
    """
    a an and are as at be by for from has have he her hers him his i in is it its of on or our so that the their them they this to was were will with you your we us not no if but into over under across while when where which who whom whose why how than then too very can may shall must should would could there here also more most less least each per upon via among within without above below before after between during against further such only same own both any all few many much other some nor like just ever never always often sometimes else one two three four five six seven eight nine ten
    """.split()
)

ML_STOP = set([  # minimal Malayalam stopword seeds; extend later as needed
    "ഒരു", "ഈ", "ആ", "എന്ന്", "അല്ല", "ഉണ്ട്", "അതായത്", "എന്നിവ", "വേണ്ടി", "കൊണ്ട്",
])

TAG_RULES: Dict[str, List[str]] = {
    "Engineering/Rolling Stock": [
        "rolling stock", "bogie", "traction", "pantograph", "brake", "maintenance", "schedule", "maximo",
        "job card", "depot", "workshop", "coach", "trainset", "ohe", "track",
    ],
    "Procurement/Finance": [
        "invoice", "po ", "purchase order", "vendor", "payment", "tender", "rfq", "gst", "grn", "bill",
    ],
    "Safety": [
        "safety", "crs", "commissioner of metro rail safety", "incident", "near miss", "ptw", "sop", "circular",
    ],
    "HR/Training": [
        "hr", "leave", "attendance", "policy", "training", "refresher", "shift", "roster",
    ],
    "Legal/Compliance": [
        "rti", "legal", "mohua", "compliance", "audit", "contract", "arbitration", "directive", "regulation",
    ],
    "Environment": [
        "environment", "eia", "pollution", "esg", "sustainability", "waste", "noise",
    ],
    "Operations/Stations": [
        "station", "controller", "operations", "timetable", "headway", "passenger", "ticket", "ridership",
    ],
    "IT/Systems": [
        "sharepoint", "sap", "iot", "uns", "scada", "network", "server", "database",
    ],
}

DATE_PATTERNS = [
    r"\b\d{4}-\d{2}-\d{2}\b",           # 2025-09-28
    r"\b\d{2}/\d{2}/\d{4}\b",           # 28/09/2025
    r"\b\d{1,2}-\d{1,2}-\d{2,4}\b",     # 28-09-25 or 28-09-2025
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+\d{1,2},\s+\d{4}\b",
]
AMOUNT_PATTERNS = [
    r"\b(?:INR|Rs\.?|₹)\s*\d[\d,]*(?:\.\d+)?\b",
]

ACTION_CLUES = [
    "must", "shall", "should", "required", "require", "submit", "approve", "approve by", "due", "deadline",
    "no later than", "not later than", "by ", "prior to", "immediately", "within ", "ensure",
]


def sha256_of_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def sniff_read_text(path: str) -> str:
    # For .txt files, detect encoding if chardet is available
    with open(path, "rb") as f:
        raw = f.read()
    encoding = None
    if chardet:
        r = chardet.detect(raw)
        encoding = r.get("encoding")
    try:
        return raw.decode(encoding or "utf-8", errors="ignore")
    except LookupError:
        return raw.decode("utf-8", errors="ignore")


def read_pdf(path: str) -> str:
    if PdfReader is None:
        return ""
    try:
        reader = PdfReader(path)
        texts = []
        for page in reader.pages:
            try:
                t = page.extract_text() or ""
            except Exception:
                t = ""
            if t:
                texts.append(t)
        return "\n".join(texts)
    except Exception:
        return ""


def read_docx(path: str) -> str:
    if docx is None:
        return ""
    try:
        d = docx.Document(path)
        paras = [p.text for p in d.paragraphs if p.text and p.text.strip()]
        return "\n".join(paras)
    except Exception:
        return ""


def clean_text(text: str) -> str:
    # Normalize whitespace
    text = re.sub(r"\r\n|\r|\n", "\n", text)
    text = re.sub(r"\t", " ", text)
    text = re.sub(r"[ \u00A0]+", " ", text)
    return text.strip()


def detect_language(text: str) -> str:
    sample = text[:5000] if text else ""
    if not sample:
        return "unknown"
    try:
        lang = detect(sample)
    except Exception:
        lang = "unknown"
    return lang


def split_sentences(text: str) -> List[str]:
    # Simple sentence splitter for . ! ? and Malayalam | Devanagari danda marks
    s = re.split(r"(?<=[\.!?\u0964\u0965\u0D3A\u0D3B])\s+|\n+", text)
    sentences = [x.strip() for x in s if x and x.strip()]
    return sentences


def tokenize(text: str) -> List[str]:
    return [w for w in re.split(r"[^\w\u00C0-\u1FFF\u2C00-\uD7FF]+", text.lower()) if w]


def summarize_extractive(text: str, max_sentences: int, lang: str) -> List[str]:
    sentences = split_sentences(text)
    if not sentences:
        return []
    if len(sentences) <= max_sentences:
        return sentences

    stop = ENG_STOP if lang.startswith("en") else ML_STOP
    # Score sentences by sum of token frequencies (excluding stopwords), normalized by sentence length
    freq: Dict[str, int] = {}
    for s in sentences:
        for tok in tokenize(s):
            if tok in stop:
                continue
            freq[tok] = freq.get(tok, 0) + 1

    def score(s: str) -> float:
        toks = [t for t in tokenize(s) if t not in stop]
        if not toks:
            return 0.0
        return sum(freq.get(t, 0) for t in toks) / (len(toks) ** 0.6)

    ranked = sorted(((score(s), i, s) for i, s in enumerate(sentences)), reverse=True)
    top = sorted(ranked[:max_sentences], key=lambda x: x[1])  # restore original order
    return [s for _, _, s in top]


def find_patterns(text: str, patterns: List[str]) -> List[str]:
    found = []
    for p in patterns:
        found += re.findall(p, text, flags=re.IGNORECASE)
    # Deduplicate while preserving order
    seen = set()
    uniq = []
    for x in found:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq


def extract_action_items(text: str) -> List[str]:
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    out = []
    for ln in lines:
        lnl = ln.lower()
        if any(clue in lnl for clue in ACTION_CLUES):
            out.append(ln)
    # Add lines around dates explicitly if they contain date mentions
    dates = find_patterns(text, DATE_PATTERNS)
    if dates:
        for ln in lines:
            if any(d in ln for d in dates):
                if ln not in out:
                    out.append(ln)
    return out[:10]


def tag_text(text: str) -> List[str]:
    tl = text.lower()
    tags = []
    for tag, keys in TAG_RULES.items():
        if any(k in tl for k in keys):
            tags.append(tag)
    if not tags:
        tags = ["General"]
    return tags


@dataclass
class DocSummary:
    source_path: str
    file_name: str
    file_sha256: str
    language: str
    title: str
    summary_sentences: List[str]
    action_items: List[str]
    tags: List[str]
    detected_dates: List[str]
    detected_amounts: List[str]
    first_seen_at: str

    def to_csv_row(self) -> List[str]:
        return [
            self.source_path,
            self.file_name,
            self.file_sha256,
            self.language,
            self.title,
            " • ".join(self.summary_sentences),
            " | ".join(self.action_items),
            "; ".join(self.tags),
            "; ".join(self.detected_dates),
            "; ".join(self.detected_amounts),
            self.first_seen_at,
        ]

    @staticmethod
    def csv_header() -> List[str]:
        return [
            "source_path",
            "file_name",
            "file_sha256",
            "language",
            "title",
            "summary",
            "action_items",
            "tags",
            "detected_dates",
            "detected_amounts",
            "first_seen_at",
        ]


def read_text_for_file(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return read_pdf(path)
    elif ext == ".docx":
        return read_docx(path)
    elif ext == ".txt":
        return sniff_read_text(path)
    return ""


def iter_files(root: str) -> Iterable[str]:
    for base, _, files in os.walk(root):
        for f in files:
            p = os.path.join(base, f)
            if os.path.splitext(f)[1].lower() in SUPPORTED_EXTS:
                yield p


def sanitize_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name)


def process(path: str, max_sentences: int) -> Optional[DocSummary]:
    text = read_text_for_file(path)
    if not text:
        return None
    text = clean_text(text)
    lang = detect_language(text)
    sentences = split_sentences(text)
    title = sentences[0][:140] if sentences else os.path.basename(path)
    summary = summarize_extractive(text, max_sentences=max_sentences, lang=lang)
    actions = extract_action_items(text)
    tags = tag_text(text)
    dates = find_patterns(text, DATE_PATTERNS)
    amounts = find_patterns(text, AMOUNT_PATTERNS)
    sha = sha256_of_file(path)
    return DocSummary(
        source_path=os.path.abspath(path),
        file_name=os.path.basename(path),
        file_sha256=sha,
        language=lang,
        title=title,
        summary_sentences=summary,
        action_items=actions,
        tags=tags,
        detected_dates=dates,
        detected_amounts=amounts,
        first_seen_at=datetime.utcnow().isoformat() + "Z",
    )


def main():
    ap = argparse.ArgumentParser(description="KMRL Document Summarizer MVP")
    ap.add_argument("--input", required=True, help="Input directory containing documents")
    ap.add_argument("--out", required=True, help="Output directory for summaries")
    ap.add_argument("--max-sentences", type=int, default=5, help="Max sentences in summary")
    ap.add_argument("--per-file-json", action="store_true", help="Write a .json per input file")
    args = ap.parse_args()

    in_dir = os.path.abspath(args.input)
    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)

    csv_path = os.path.join(out_dir, "summary.csv")

    # Prepare route files map (tag -> open handle)
    route_handles: Dict[str, any] = {}

    def write_route(tag: str, record: Dict):
        fname = os.path.join(out_dir, f"route_{sanitize_filename(tag)}.jsonl")
        if tag not in route_handles:
            route_handles[tag] = open(fname, "a", encoding="utf-8")
        route_handles[tag].write(json.dumps(record, ensure_ascii=False) + "\n")

    # Process
    files = list(iter_files(in_dir))
    if not files:
        print(f"No supported documents found in {in_dir}")
        return 2

    # CSV header
    csv_new = not os.path.exists(csv_path)

    with open(csv_path, "a", encoding="utf-8", newline="") as csvf:
        writer = csv.writer(csvf)
        if csv_new:
            writer.writerow(DocSummary.csv_header())

        for fp in tqdm(files, desc="Processing docs", unit="file"):
            try:
                ds = process(fp, args.max_sentences)
                if not ds:
                    continue
                writer.writerow(ds.to_csv_row())

                rec = asdict(ds)
                for tag in ds.tags:
                    write_route(tag, rec)

                if args.per_file_json:
                    per_json = os.path.join(out_dir, sanitize_filename(ds.file_name) + ".json")
                    with open(per_json, "w", encoding="utf-8") as jf:
                        json.dump(rec, jf, ensure_ascii=False, indent=2)
            except KeyboardInterrupt:
                raise
            except Exception as ex:
                # Log minimal error, continue
                sys.stderr.write(f"Error processing {fp}: {ex}\n")
                continue

    for h in route_handles.values():
        try:
            h.close()
        except Exception:
            pass

    print(f"Done. CSV: {csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
