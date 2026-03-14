from flask import Flask, request, jsonify
from flask_cors import CORS
import re
from datetime import datetime
from PIL import Image
import pytesseract
import io
import shutil
import os

# Auto-detect Tesseract — works on Linux (Railway) and Windows
_tess = (
    shutil.which("tesseract")                                      # works if it's in PATH
    or r"C:\Program Files\Tesseract-OCR\tesseract.exe"            # Windows default install
    or r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"      # Windows 32-bit fallback
)
if _tess and os.path.exists(_tess):
    pytesseract.pytesseract.tesseract_cmd = _tess

app = Flask(__name__)
CORS(app)

# OCR month misread corrections
OCR_MONTH_FIXES = {
    "Maren": "March", "Mareh": "March", "Marck": "March",
    "Januery": "January", "Janaury": "January",
    "Febuary": "February", "Febraury": "February",
    "Apri1": "April", "Apnl": "April",
    "Jume": "June", "Jure": "June",
    "Jaly": "July", "Juiy": "July",
    "Augast": "August", "Augest": "August",
    "Septmber": "September", "Sepember": "September", "Sept": "September",
    "Octcber": "October", "Ocober": "October",
    "Novmber": "November", "Noveber": "November",
    "Decmber": "December", "Deceber": "December",
}


def fix_ocr_month(text):
    for wrong, correct in OCR_MONTH_FIXES.items():
        text = re.sub(rf'\b{re.escape(wrong)}\b', correct, text, flags=re.IGNORECASE)
    return text


def parse_receipt_from_image(image_bytes):
    image = Image.open(io.BytesIO(image_bytes))
    text  = pytesseract.image_to_string(image)

    lines = [
        l.strip() for l in text.split('\n')
        if l.strip() and not l.strip().startswith("'")
    ]

    amount           = None
    transaction_date = None
    narration        = None

    for i, line in enumerate(lines):

        # 1. AMOUNT
        if amount is None:
            amt_match = re.search(
                r'(?<![A-Za-z])[N₦¥#&]\s*[\d,]+(?:\.\d{2})?'
                r'|(?:Amount|Total|Sum)\s*[:\-]?\s*([\d,]+(?:\.\d{2})?)',
                line,
                re.IGNORECASE
            )
            if amt_match:
                raw    = amt_match.group()
                digits = re.sub(r'[^\d.]', '', raw.split()[-1])
                try:
                    candidate = float(digits)
                    if candidate <= 1_000_000 or ',' in raw:
                        amount = candidate
                except ValueError:
                    pass

        # 2. TRANSACTION DATE
        if transaction_date is None:
            date_patterns = [
                r'(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+'
                r'[A-Za-z]{3,9}\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}\s*[|]?\s*\d{1,2}:\d{2}(?::\d{2})?(?:\s*[APap][Mm])?',
                r'[A-Za-z]{3,9}\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}\s+\d{1,2}:\d{2}(?::\d{2})?(?:\s*[APap][Mm])?',
                r'\d{4}[-/]\d{2}[-/]\d{2}\s+\d{1,2}:\d{2}(?::\d{2})?',
                r'\d{2}[-/]\d{2}[-/]\d{4}\s+\d{1,2}:\d{2}(?::\d{2})?',
                r'\d{4}[-/]\d{2}[-/]\d{2}',
                r'\d{2}[-/]\d{2}[-/]\d{4}',
            ]
            date_formats = [
                "%B %d, %Y %I:%M %p", "%B %d, %Y %I:%M%p",
                "%B %d, %Y %H:%M:%S", "%B %d, %Y %H:%M",
                "%B %d %Y %H:%M:%S",  "%B %d %Y %H:%M",
                "%b %d, %Y %I:%M %p", "%b %d, %Y %I:%M%p",
                "%b %d, %Y %H:%M:%S", "%b %d, %Y %H:%M",
                "%b %d %Y %H:%M:%S",  "%b %d %Y %H:%M",
                "%Y-%m-%d %H:%M:%S",  "%Y-%m-%d %H:%M",
                "%Y/%m/%d %H:%M:%S",  "%Y/%m/%d %H:%M",
                "%d/%m/%Y %H:%M:%S",  "%d/%m/%Y %H:%M",
                "%d-%m-%Y %H:%M:%S",  "%d-%m-%Y %H:%M",
                "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y",
            ]
            cleaned_line = fix_ocr_month(line)
            for pattern in date_patterns:
                m = re.search(pattern, cleaned_line, re.IGNORECASE)
                if m:
                    date_str = m.group()
                    date_str = re.sub(
                        r'^(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+',
                        '', date_str, flags=re.IGNORECASE
                    )
                    date_str = re.sub(r'(\d{1,2})(st|nd|rd|th)', r'\1', date_str)
                    date_str = re.sub(r'\s*\|\s*', ' ', date_str).strip()
                    for fmt in date_formats:
                        try:
                            transaction_date = datetime.strptime(date_str, fmt).isoformat()
                            break
                        except ValueError:
                            continue
                    if transaction_date:
                        break

        # 3. NARRATION
        if narration is None:
            kw = re.search(
                r'(?:Remark|Narration|Description|Memo|Purpose|Note)\s*[:\-]?\s*(.*)',
                line, re.IGNORECASE
            )
            if kw:
                narration = kw.group(1).strip()
                if not narration and (i + 1) < len(lines):
                    nxt = lines[i + 1].strip()
                    if not re.search(
                        r'(?:Amount|Date|Total|Ref|Bank|From|Sender|Beneficiary|Institution)',
                        nxt, re.IGNORECASE
                    ):
                        narration = nxt

    return {
        "amount":           amount,
        "transaction_date": transaction_date,
        "narration":        narration or None,
    }


@app.route("/parse-receipt", methods=["POST"])
def parse_receipt():
    if "image" not in request.files:
        return jsonify({"error": "No image file provided"}), 400

    file        = request.files["image"]
    image_bytes = file.read()

    try:
        result = parse_receipt_from_image(image_bytes)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(debug=True, port=5000)