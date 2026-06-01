import os
import time
import json
import requests
import shutil
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import pytesseract
from pdf2image import convert_from_path
from PIL import Image

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://ollama:11434")
WATCH_DIR = os.getenv("WATCH_DIR", "/app/incoming")
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "/app/processed")
ARCHIVE_DIR = os.getenv("ARCHIVE_DIR", "/app/archive")

# Load compliance rules
with open("/app/compliance_rules.json", "r") as f:
    COMPLIANCE_RULES = json.load(f)

def extract_text_by_page(file_path: str) -> dict:
    """Returns a dict {page_number: text} for a PDF using pdfplumber."""
    ext = file_path.lower().split('.')[-1]
    if ext != 'pdf':
        return {}
    import pdfplumber
    pages_text = {}
    try:
        with pdfplumber.open(file_path) as pdf:
            for i, page in enumerate(pdf.pages, start=1):
                text = page.extract_text()
                if text:
                    pages_text[i] = text
    except Exception as e:
        print(f"Error splitting PDF: {e}")
    return pages_text

def extract_text_from_file(file_path: str) -> str:
    """Extract readable text from PDF, image, or plain text files."""
    ext = file_path.lower().split('.')[-1]
    try:
        if ext == 'pdf':
            # Try pdfplumber first (fast, works for text‑based PDFs)
            import pdfplumber
            with pdfplumber.open(file_path) as pdf:
                text = ""
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
                
                # If pdfplumber extracted something and it doesn't look garbled, use it
                if text.strip() and not any(garbled in text for garbled in ["JJ", "MM", "VV", "aann", "ee  ", "  SS"]):
                    return text.strip()
                
                # Otherwise, fall back to OCR (image‑based or garbled PDFs)
                from pdf2image import convert_from_path
                images = convert_from_path(file_path, first_page=1, last_page=min(len(pdf.pages), 5))
                ocr_text = ""
                for img in images:
                    ocr_text += pytesseract.image_to_string(img) + "\n"
                return ocr_text.strip() if ocr_text.strip() else "[No text extracted]"
        elif ext in ('png', 'jpg', 'jpeg', 'bmp', 'tiff'):
            img = Image.open(file_path)
            return pytesseract.image_to_string(img)
        elif ext == 'txt':
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                return f.read()
        else:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                return f.read()
    except Exception as e:
        return f"[Could not extract text: {e}]"

def clean_address(raw_address: str) -> str:
    """Clean OCR artifacts from an address string."""
    if not raw_address:
        return raw_address
    # Remove underscores
    cleaned = raw_address.replace('_', '')
    # Replace multiple spaces/newlines with a single space
    import re
    cleaned = re.sub(r'\s+', ' ', cleaned)
    # Remove leading/trailing spaces and commas
    cleaned = cleaned.strip().strip(',')
    # Fix common patterns like "Toronto, ON M5A 1B2, Canada" if they got split
    # (the regex above already merges lines, so this should be fine)
    return cleaned

def clean_address_with_llm(raw_address: str) -> str:
    """Use the local LLM to fix OCR artifacts in an address string."""
    if not raw_address or len(raw_address) < 5:
        return raw_address
    prompt = f"""Fix any OCR errors in this address. Remove stray underscores, extra spaces, and broken words (e.g., "To ro nto" → "Toronto"). Return ONLY the corrected address, no other text.    
    
Address: {raw_address}
"""
    try:
        fixed = query_ollama(prompt, system="You are a text cleanup assistant. Return only the fixed address.", timeout=30, raw=True)
        return fixed.strip().replace('"', '')
    except Exception:
        return raw_address  # fallback to original if LLM call fails

def clean_text_with_llm(text: str, context: str = "text") -> str:
    """Use the LLM to fix OCR splitting/errors in a short text field."""
    if not text or len(text) < 3:
        return text
    prompt = f"""Fix any OCR splitting errors in this {context}. 
For example, "Salar y" → "Salary", "Soft ware Engi neer" → "Software Engineer".
Return ONLY the corrected text, no other words.
Text: {text}
"""
    try:
        fixed = query_ollama(prompt, system="You are a text cleanup assistant. Return only the corrected text.", timeout=20, raw=True)
        return fixed.strip().replace('"', '')
    except Exception:
        return text

def extract_country_from_raw_address(raw_address: str) -> str:
    """Use the LLM to extract the country name from an address string."""
    if not raw_address or len(raw_address) < 5:
        return ""
    prompt = f"""Extract ONLY the country name from this address. If no country is present, return an empty string. Return nothing else.
Address: {raw_address}
"""
    try:
        country = query_ollama(prompt, system="You are a precise data extraction assistant.", timeout=20, raw=True)
        return country.strip()
    except Exception:
        return ""

def build_document_summary(client_dir: str) -> dict:
    """Walk through client folder and produce a list of filenames and short text snippets."""
    files = []
    for root, _, filenames in os.walk(client_dir):
        for fname in filenames:
            full_path = os.path.join(root, fname)
            relative = os.path.relpath(full_path, client_dir)
            text_preview = extract_text_from_file(full_path)
            files.append({
                "filename": relative,
                "text_preview": text_preview
            })
    return {"files": files}

def query_ollama(prompt: str, system: str = "", model: str = "llama3.1:8b", timeout: int = 120, raw: bool = False) -> dict:
    url = f"{OLLAMA_HOST}/api/chat"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt}
        ],
        "stream": False
    }
    if not raw:
        payload["format"] = "json"
    response = requests.post(url, json=payload, timeout=timeout)
    response.raise_for_status()
    result = response.json()
    content = result.get("message", {}).get("content", "{}")
    if raw:
        return content  # return raw string
    return json.loads(content)

def screen_names(names: list[str]) -> dict:
    """Call the local screening service and return matches."""
    import requests as req
    try:
        resp = req.post("http://screening:8000/screen", json={"names": names}, timeout=60)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"Screening call failed: {e}")
        return {"matches": []}

def check_document_completeness(client_dir: str) -> dict:
    """Use LLM to check required documents and EDD triggers."""
    doc_summary = build_document_summary(client_dir)
    rules_str = json.dumps(COMPLIANCE_RULES, indent=2)
    
    system_prompt = (
        "You are a precise compliance assistant. "
        "You analyze submitted client documents for KYC/AML onboarding. "
        "You only speak valid JSON. "
        "Do not include any extra text outside the JSON object."
    )
    
    user_prompt = f"""
Here are the compliance rules:
{rules_str}

A client has submitted the following documents (with short text previews):
{json.dumps(doc_summary['files'], indent=2)}

Your task:
1. Identify which required documents (for an individual, unless corporate documents suggest otherwise) are present. Use the filenames and text snippets.
2. List any missing required documents.
3. Determine if Enhanced Due Diligence (EDD) should be triggered. Base this on the EDD triggers listed in the rules and any evidence in the documents (e.g., mentions of PEP, high-risk jurisdiction, unusual source of funds, complex structures, sanctions hints).
4. Provide a short note on the overall completeness.

Return ONLY a JSON object with these exact keys:
- "entity_type": "individual" or "corporate"
- "documents_present": [list of strings]
- "documents_missing": [list of strings]
- "edd_triggered": true/false
- "edd_reasons": [list of strings] (empty if not triggered)
- "notes": "short summary"
"""
    return query_ollama(user_prompt, system=system_prompt)

def find_relevant_chunks(client_dir: str, keywords: list, max_chunks: int = 5) -> str:
    """
    Search all document text for paragraphs containing any of the keywords,
    and return the most relevant chunks up to a total of ~3000 characters.
    """
    doc_summary = build_document_summary(client_dir)
    all_text = ""
    for f in doc_summary['files']:
        all_text += f['text_preview'] + "\n"
    
    # Split into paragraphs (by newlines) and score by number of keyword matches
    paragraphs = [p.strip() for p in all_text.split('\n') if p.strip()]
    scored = []
    for p in paragraphs:
        score = sum(1 for kw in keywords if kw.lower() in p.lower())
        if score > 0:
            scored.append((score, p))
    # Sort by score descending, take top chunks
    scored.sort(key=lambda x: x[0], reverse=True)
    selected = [p for _, p in scored[:max_chunks]]
    return "\n".join(selected)[:3000]

import re

def extract_missing_fields_deterministic(text: str) -> dict:
    """
    Use regex to extract date of birth, source of funds, source of wealth, and address
    from a block of text. Falls back to null if not found.
    """
    result = {
        "date_of_birth": None,
        "residential_address": None,
        "source_of_funds_description": None,
        "source_of_wealth_description": None
    }
    
    # Date of birth: common formats (DD/MM/YYYY, YYYY-MM-DD, Month DD, YYYY, etc.)
    dob_patterns = [
        r'\b(\d{2}[/\-\.]\d{2}[/\-\.]\d{4})\b',   # 14/07/1982 or 14-07-1982
        r'\b(\d{4}[/\-\.]\d{2}[/\-\.]\d{2})\b',   # 1982-07-14
        r'\b(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4})\b',  # 14 July 1982
        r'\b((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4})\b'  # July 14, 1982
    ]
    for pattern in dob_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            result["date_of_birth"] = match.group(1)
            break
    
    # Residential address: look for "Address:" or "Residential Address:" followed by text
    addr_match = re.search(r'(?:Residential\s+)?Address\s*:\s*(.*?)(?:\n|$)', text, re.IGNORECASE)
    if addr_match:
        result["residential_address"] = addr_match.group(1).strip()
    else:
        # fallback: capture lines that look like addresses (number + street, city, postal code)
        addr_lines = re.findall(r'(\d+\s+\w+.*(?:Street|St|Avenue|Ave|Road|Rd|Drive|Dr|Lane|Ln|Court|Ct|Toronto|ON|M5A).*)', text)
        if addr_lines:
            result["residential_address"] = addr_lines[0].strip()
    
    # Source of funds: look for explicit "Source of Funds:" or similar
    sof_match = re.search(r'Source\s+of\s+Funds\s*:\s*(.*?)(?:\n|$)', text, re.IGNORECASE)
    if sof_match and not sof_match.group(1).lower().startswith("we confirm"):
        result["source_of_funds_description"] = sof_match.group(1).strip()
    else:
        # fallback: look for occupation/employer lines that indicate salary
        occ_match = re.search(r'(?:Occupation|Employer)\s*:\s*(.*?)(?:\n|$)', text, re.IGNORECASE)
        if occ_match:
            result["source_of_funds_description"] = "Employment: " + occ_match.group(1).strip()
    
    # Source of wealth: similar pattern
    sow_match = re.search(r'Source\s+of\s+Wealth\s*:\s*(.*?)(?:\n|$)', text, re.IGNORECASE)
    if sow_match and not sow_match.group(1).lower().startswith("salary is the source"):
        result["source_of_wealth_description"] = sow_match.group(1).strip()
    else:
        # fallback: if we found "salary" in context, use that
        if re.search(r'salary|savings|investment', text, re.IGNORECASE):
            result["source_of_wealth_description"] = "Salary and savings"
    
    return result

def classify_files(file_list: list[dict]) -> str:
    """
    Identify the application form text among all files and return only that.
    Uses keyword density scoring with weighting for application‑specific terms.
    """
    # General KYC keywords (found in passports too)
    general_keywords = [
        "full name", "date of birth", "nationality", "passport", "address",
        "place of birth"
    ]
    # Application‑form‑only keywords (never appear on a passport)
    app_keywords = [
        "source of funds", "source of wealth", "subscription", "applicant registration",
        "self-certification", "tax identification", "beneficial owner",
        "politically exposed", "declaration", "remitting bank", "correspondent bank",
        "beneficiary bank", "redemption", "shares", "segregated portfolio",
        "private placing memorandum"
    ]
    
    best_score = 0
    application_text = ""
    
    for f in file_list:
        text = f.get("text_preview", "")
        # App‑specific keywords count 3x
        score = sum(3 for kw in app_keywords if kw.lower() in text.lower())
        # General keywords count 1x
        score += sum(1 for kw in general_keywords if kw.lower() in text.lower())
        
        if score > best_score:
            application_text = text
            best_score = score
    
    # Fallback: if no app‑specific keywords found, use longest text
    if best_score == 0:
        application_text = max((f.get("text_preview", "") for f in file_list), key=len, default="")
    
    return application_text

def extract_core_kyc(client_dir: str, completeness_result: dict) -> dict:
    doc_summary = build_document_summary(client_dir)
    
    # Classify files: use only the application form for extraction
    app_text = classify_files(doc_summary['files'])
    combined_text = app_text[:20000]  # Use only the identified application form

    # ---- Call 1: Identity fields ----
    identity_prompt = f"""
Extract the following fields from the text as plain strings:
- full_name
- date_of_birth
- nationality
- passport_or_id_number
- residential_address (full address, including country)
Return ONLY a JSON object with those exact keys. Use null if not found.

Text:
{combined_text[:8000]}
"""
        # DEBUG: save combined text
    client_name = os.path.basename(client_dir)
    debug_path = os.path.join(OUTPUT_DIR, client_name, "debug_ocr_text.txt")
    os.makedirs(os.path.dirname(debug_path), exist_ok=True)
    with open(debug_path, "w", encoding="utf-8") as f:
        f.write(combined_text)
    identity_data = query_ollama(identity_prompt, system="You extract identity fields as JSON.", timeout=120)

    # Address cleaning and country fix
    addr = identity_data.get("residential_address")
    if isinstance(addr, dict):
        addr = ", ".join(str(v) for v in addr.values() if v)
    if addr:
        cleaned_addr = clean_address_with_llm(addr)
        country = extract_country_from_raw_address(addr)
        if country and country.lower() not in cleaned_addr.lower():
            cleaned_addr = cleaned_addr.rstrip(", ") + ", " + country
        identity_data["residential_address"] = cleaned_addr
    else:
        identity_data["residential_address"] = None

    # ---- Call 2: PEP, source of funds/wealth (with disambiguation) ----
    extra_prompt = f"""
Extract the following fields from the text. Read carefully and do not confuse employer/occupation with source of funds.

- "source_of_funds_description": The explicit source of funds for the subscription (e.g., "Salary from employment"). Look for "Source of Subscription Funds" or similar label. Do NOT use the employer/occupation unless it is explicitly given as the source.
- "source_of_wealth_description": The full narrative explaining how the applicant accumulated their wealth (e.g., "Accumulated savings and investment gains over 15 years; Senior Software Engineer at MapleTech Solutions Inc."). Include the complete sentence, not just the job title.
- "pep_declaration": true/false/null based on explicit PEP declaration.
- "applicant_is_pep": true/false/null (same as pep_declaration).
- "third_party_payment_detected": true/false/null.

Return ONLY a JSON object with those exact keys. Use null if not found.

Text:
{combined_text[:15000]}
"""
    extra_data = query_ollama(extra_prompt, system="You extract compliance fields as JSON.", timeout=120)

    # OCR cleanup on text fields
    if extra_data.get("source_of_funds_description"):
        extra_data["source_of_funds_description"] = clean_text_with_llm(
            extra_data["source_of_funds_description"], "source of funds"
        )
    if extra_data.get("source_of_wealth_description"):
        extra_data["source_of_wealth_description"] = clean_text_with_llm(
            extra_data["source_of_wealth_description"], "source of wealth"
        )

    # ---- Build final profile ----
    profile = {
        "entity_type": "individual",
        "individual_applicants": [{
            "full_name": identity_data.get("full_name"),
            "date_of_birth": identity_data.get("date_of_birth"),
            "nationality": identity_data.get("nationality"),
            "passport_or_id_number": identity_data.get("passport_or_id_number"),
            "residential_address": identity_data.get("residential_address"),
            "pep_declaration": extra_data.get("pep_declaration"),
            "source_of_funds": extra_data.get("source_of_funds_description"),
            "source_of_wealth": extra_data.get("source_of_wealth_description")
        }],
        "entity_applicant": None,
        "pep_sanctions_declarations": {
            "applicant_is_pep": extra_data.get("applicant_is_pep"),
            "beneficial_owner_is_pep": None,
            "controller_is_pep": None,
            "sanctions_exposure": None,
            "shell_bank_exposure": None,
            "high_risk_jurisdiction_involved": None
        },
        "source_of_funds_and_wealth": {
            "source_of_funds_description": extra_data.get("source_of_funds_description"),
            "source_of_wealth_description": extra_data.get("source_of_wealth_description"),
            "third_party_payment_detected": extra_data.get("third_party_payment_detected")
        },
        "risk_flags": {},
        "notes": ""
    }
    return profile

def process_client(client_name: str):
    """Main processing pipeline for one client folder."""
    client_dir = os.path.join(WATCH_DIR, client_name)
    if not os.path.isdir(client_dir):
        return

    print(f"Processing client: {client_name}")
    
    # Step 1: Document completeness & EDD
    completeness_result = check_document_completeness(client_dir)
    print("Completeness check result:", json.dumps(completeness_result, indent=2))
    
    # Step 2: Extract core KYC profile
    print("Extracting KYC profile...")
    kyc_profile = extract_core_kyc(client_dir, completeness_result)
    print("Extraction result:", json.dumps(kyc_profile, indent=2))
    
    # Merge completeness info into the profile (missing docs etc.)
    kyc_profile["completeness"] = {
        "documents_present": completeness_result.get("documents_present", []),
        "documents_missing": completeness_result.get("documents_missing", []),
        "edd_triggered_by_completeness": completeness_result.get("edd_triggered", False),
        "completeness_notes": completeness_result.get("notes", "")
    }
    
    # If the extraction flagged EDD or the completeness check flagged EDD, mark final EDD
    edd_extraction = kyc_profile.get("risk_flags", {}).get("edd_required", False)
    edd_completeness = completeness_result.get("edd_triggered", False)
    kyc_profile["final_edd"] = bool(edd_extraction or edd_completeness)
    
    # Step 3: Sanctions screening
    all_names = []
    for applicant in kyc_profile.get("individual_applicants", []):
        if applicant.get("full_name"):
            all_names.append(applicant["full_name"])
    # also add entity names if present
    if kyc_profile.get("entity_applicant") and kyc_profile["entity_applicant"].get("legal_name"):
        all_names.append(kyc_profile["entity_applicant"]["legal_name"])
    if all_names:
        screening_result = screen_names(all_names)
        kyc_profile["sanctions_screening"] = screening_result.get("matches", [])
    else:
        kyc_profile["sanctions_screening"] = []

    # Save the combined result
    client_out = os.path.join(OUTPUT_DIR, client_name)
    os.makedirs(client_out, exist_ok=True)
    with open(os.path.join(client_out, "completeness_check.json"), "w") as f:
        json.dump(completeness_result, f, indent=2)
    with open(os.path.join(client_out, "extracted_profile.json"), "w") as f:
        json.dump(kyc_profile, f, indent=2)
    
    # Generate Word report
    try:
        import report_generator
        report_path = os.path.join(client_out, "kyc_report.docx")
        report_generator.generate_kyc_report(os.path.join(client_out, "extracted_profile.json"), report_path)
    except Exception as e:
        print(f"Report generation failed: {e}")

    # Move processed client folder to archive
    archive_client_dir = os.path.join(ARCHIVE_DIR, client_name)
    shutil.move(client_dir, archive_client_dir)
    print(f"Moved {client_name} to archive.")
    
    print(f"Finished processing {client_name}. Output saved to {client_out}")

class ClientFolderHandler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory and os.path.dirname(event.src_path) == WATCH_DIR:
            client_name = os.path.basename(event.src_path)
            time.sleep(5)
            process_client(client_name)

if __name__ == "__main__":
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    for item in os.listdir(WATCH_DIR):
        if os.path.isdir(os.path.join(WATCH_DIR, item)):
            process_client(item)
    
    event_handler = ClientFolderHandler()
    observer = Observer()
    observer.schedule(event_handler, WATCH_DIR, recursive=False)
    observer.start()
    print(f"Watching {WATCH_DIR} for new client folders...")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()