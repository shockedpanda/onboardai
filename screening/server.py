import os, csv, sqlite3, requests
from fastapi import FastAPI
from pydantic import BaseModel
from rapidfuzz import fuzz

app = FastAPI()

OFAC_URL = "https://www.treasury.gov/ofac/downloads/sdn.csv"
DATA_DIR = "/app/data"
DB_PATH = os.path.join(DATA_DIR, "screening.db")
OFAC_FILE = os.path.join(DATA_DIR, "sdn.csv")
PEP_FILE = os.path.join(DATA_DIR, "openpeps.csv")   # mounted from host

class ScreenRequest(BaseModel):
    names: list[str]

class ScreenResult(BaseModel):
    matches: list[dict]

def download_ofac():
    """Download OFAC SDN list if not present."""
    if os.path.exists(OFAC_FILE):
        print("OFAC data already present.")
        return
    print("Downloading OFAC SDN list...")
    resp = requests.get(OFAC_URL, stream=True, timeout=60)
    resp.raise_for_status()
    with open(OFAC_FILE, "wb") as f:
        f.write(resp.content)
    print("OFAC download complete.")

def build_database():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DROP TABLE IF EXISTS screening")
    conn.execute("CREATE TABLE screening (id INTEGER PRIMARY KEY, name TEXT, source_type TEXT)")

    # 1. Load OFAC (mandatory, always present)
    with open(OFAC_FILE, "r", encoding="utf-8", errors="ignore") as f:
        reader = csv.reader(f)
        next(reader)  # skip header
        for i, row in enumerate(reader):
            name = row[1].strip() if len(row) > 1 else ""
            if name:
                conn.execute("INSERT INTO screening (id, name, source_type) VALUES (?, ?, ?)",
                             (i, name, "sanction"))

    # 2. Load OpenSanctions PEP file if available (optional)
    if os.path.exists(PEP_FILE):
        print("Loading OpenSanctions PEP data...")
        with open(PEP_FILE, "r", encoding="utf-8", errors="ignore") as f:
            reader = csv.DictReader(f)
            for i, row in enumerate(reader, start=1000000):  # offset IDs to avoid collision
                name = row.get("name", "").strip()
                if not name:
                    continue
                datasets = row.get("dataset", "").lower()
                source_type = "pep" if "pep" in datasets else "sanction"
                conn.execute("INSERT INTO screening (id, name, source_type) VALUES (?, ?, ?)",
                             (i, name, source_type))
        print("PEP data loaded.")
    else:
        print("No OpenSanctions PEP file found – continuing with OFAC only.")

    # Build full‑text search index
    conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS screening_fts USING fts5(id, name, source_type, content=screening, content_rowid=id)")
    conn.commit()
    conn.close()
    print("Indexing complete.")

@app.on_event("startup")
def startup():
    os.makedirs(DATA_DIR, exist_ok=True)
    download_ofac()
    build_database()

@app.post("/screen", response_model=ScreenResult)
def screen_names(req: ScreenRequest):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    matches = []
    for name in req.names:
        # Build a set of words from the input name
        words = name.lower().split()
        candidates = set()
        # Use LIKE to find any record containing any of the words
        for word in words:
            cur.execute("SELECT id, name, source_type FROM screening WHERE lower(name) LIKE ?", (f"%{word}%",))
            for row in cur.fetchall():
                candidates.add(row)
        # Fuzzy match against candidates
        for row in candidates:
            score = fuzz.token_sort_ratio(name.lower(), row[1].lower())
            if score >= 85:
                matches.append({
                    "queried_name": name,
                    "matched_name": row[1],
                    "source_type": row[2],
                    "score": score
                })
    conn.close()
    # Deduplicate and sort by score
    seen = set()
    unique_matches = []
    for m in sorted(matches, key=lambda x: x["score"], reverse=True):
        key = (m["queried_name"], m["matched_name"])
        if key not in seen:
            seen.add(key)
            unique_matches.append(m)
    return {"matches": unique_matches[:20]}