# OnboardAI — Local AML/KYC Onboarding Assistant

A proof-of-concept pipeline that automates KYC document review, identity extraction, and sanctions/PEP screening — running entirely offline.

## What It Does

1. Drop a KYC pack into a watched folder
2. Document completeness check — verifies all required documents are present
3. KYC data extraction — pulls identity, source of funds/wealth, PEP declarations
4. Sanctions & PEP screening — matches names against OFAC SDN and OpenSanctions PEP data
5. Generates a Word report — ready for compliance review

Everything runs locally. No cloud, no third-party APIs, no data leaves the machine.

## Demo Video

[Watch the demo](https://youtu.be/i14d7llXzos)

## Quick Start

### Prerequisites
- Docker Desktop
- NVIDIA GPU (optional, but recommended for speed)
- 16GB+ RAM recommended

### Setup

git clone https://github.com/shockedpanda/onboardai.git
cd onboardai

Start the stack:
docker compose up --build -d

Pull the LLM model:
docker exec -it ollama ollama pull llama3.1:8b

Optional: Place a PEP/sanctions CSV in screening_data/openpeps.csv
Download from: https://www.opensanctions.org/datasets/peps/

Drop a client folder into incoming/:
mkdir incoming/test_client
cp sample_data/Jane_Smith_Demo/application_form.txt incoming/test_client/

Restart processor:
docker compose restart processor

View results in processed/test_client/kyc_report.docx

### Test Profiles (from demo)
- Jane Smith — clean individual, no hits
- Justin Trudeau — PEP hit
- MapleTech Solutions (John Maple) — clean entity
- Global Ventures (Viktor Bout) — sanctions hit

## Architecture

onboardai/
├── docker-compose.yml       # Ollama + Processor + Screening services
├── processor/               # Python watcher & extraction pipeline
│   ├── watch_and_process.py # Main processor script
│   ├── report_generator.py  # Word report generation
│   └── requirements.txt
├── screening/               # Sanctions/PEP screening service
│   ├── server.py            # FastAPI endpoint
│   └── requirements.txt
├── compliance_rules.json    # Configurable document requirements
└── sample_data/             # Demo KYC packs

## Tech Stack
- LLM: Llama 3.1 8B via Ollama
- OCR: pdfplumber + Tesseract
- Screening: OFAC SDN + OpenSanctions PEP data, fuzzy matching via rapidfuzz
- Report: python-docx
- Infra: Docker Compose, FastAPI, SQLite

## Important Note on Document Formats

This POC was built and tested using a specific KYC application pack format from my prior work in banking and asset management. The extraction prompts, document classification logic, and compliance rules are tuned to that format.

To adapt this for your own KYC packs, you will likely need to:
- Adjust the extraction prompts in watch_and_process.py to match your form field names and layout
- Update compliance_rules.json with your firm required document types and EDD triggers
- Test with your own document templates and refine the LLM prompts

It is a starting point, not a plug-and-play solution. The architecture is designed to be adaptable. The prompts and rules are the knobs you turn.

## Status

Proof of Concept — not production-ready. Built to demonstrate the concept and gather feedback. Significant refinement needed for real-world use.

## Roadmap (if interest continues)
- Document certification validation (signatures, dates, certifier verification)
- Parallel LLM calls for faster processing
- Web UI for non-technical users
- Multi-entity support
- Audit trail and logging
- Proper EDD risk flag logic

## License

Source available for review. Contact for usage terms.

## Contact

Built by shockedpanda. Background in banking and asset management.

https://www.linkedin.com/in/shanegjd/ | shockedpandaai@gmail.com