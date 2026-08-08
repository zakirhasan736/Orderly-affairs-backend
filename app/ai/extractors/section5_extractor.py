from app.ai.extractors.base_extractor import extract_structured_from_document
from app.ai.schemas.section5_schema import SECTION5_FULL_SCHEMA

VALID_SECTION5_SUBSECTIONS = {
    "5A",
}

SECTION5_PROMPT = """
You are extracting data for the 'Vehicles' section of an estate planning app.

Return JSON only.
Do not guess.
Read the full document carefully (all pages, tables, card faces, stamps, and fine print).
Only include values clearly supported by the uploaded document.
If a field is not present, use null for scalar fields and [] for arrays.

Important rules:
- The only supported subsection is 5A.
- 5A means Current Vehicles.
- patch["5A"] must always be an array.
- If the uploaded document describes one vehicle, return exactly one object inside patch["5A"].
- If the uploaded document describes multiple vehicles, return one object per vehicle inside patch["5A"].
- Insurance cards, declarations pages, and multi-vehicle registration documents often list 2 or more vehicles on the same page. Extract EVERY distinct vehicle listed — do not stop after the first one.
- A distinct vehicle is identified by its own year/make/model and/or VIN. Two rows or blocks on an insurance card usually mean two vehicles.
- If multiple vehicles share the same insurance policy number or insurance company, copy those shared values into each vehicle object.
- Place year, make, model, color, vin, and license_plate into their dedicated fields — never leave vehicle identity only in notes when it is clearly printed.
- VIN is critical: auto insurance cards and declarations almost always print a VIN (or Veh ID / Vehicle Identification Number / Serial No.) for each vehicle in a schedule or table. Extract it into `vin` for every vehicle whenever shown — do not leave VIN only in notes.
- VIN values are typically 17 characters (letters and digits, never I/O/Q). Copy exactly as printed, including when shown with spaces or dashes (normalize into the vin field without spaces).
- Keep keys exactly as required by schema.
- Never invent VIN, license plate, insurance policy number, loan information, or ownership details.
- If a value is unclear, return null.
- Do not include markdown.
- Do not explain.

Vehicle fields (exact placement):
- year = vehicle year.
- make = vehicle manufacturer, for example Toyota, Honda, Ford, BMW.
- model = vehicle model.
- color = vehicle color.
- vin = Vehicle Identification Number / VIN / Veh ID / Vehicle ID / Serial No. (required whenever printed for that vehicle).
- license_plate = current license plate number.
- registration_expiry = registration expiration / "valid through" / "expires" date.
  Also use the END date of a clearly labeled registration period or policy/registration period
  (for example "Period: 01/01/2025 to 12/31/2025" → registration_expiry = 2025-12-31)
  when no separate registration expiry is printed.
  Prefer YYYY-MM-DD when possible.
- insurance_company = current insurance provider / carrier name (exact field — do not leave only in notes).
- insurance_policy = insurance policy number / insurance number / NAIC / policy ID (exact field — do not leave only in notes). Treat labels like "Policy #", "Insurance Number", and "Member ID" as insurance_policy.
- financing = loan, lease, lender, payoff, monthly payment, or owned-outright information.
- maintenance_records = service records, receipts, maintenance schedule, or where records are stored.
- parking_location = usual parking location.
- spare_keys = spare key location.
- notes = any other important vehicle-related information clearly present that does not fit another field.

Common source documents:
- vehicle registration
- insurance card
- title document
- loan or lease statement
- maintenance receipt
- service record
- vehicle information sheet
- photo or screenshot containing vehicle details
"""


async def extract_section5_from_document(
    document_url: str,
    subsection: str | None = None,
    mime_type: str = "application/pdf",
    field_catalog: list[dict] | None = None,
):
    if subsection and subsection not in VALID_SECTION5_SUBSECTIONS:
        raise ValueError(f"Invalid Section 5 subsection: {subsection}")

    prompt = SECTION5_PROMPT + f"""

Requested section: vehicles
Requested subsection: {subsection or "FULL_SECTION"}

Return this exact JSON structure:
- section: "vehicles"
- scope: {"subsection" if subsection else "section"}
- subsection: {subsection if subsection else "null"}
- confidence: number between 0 and 1
- patch: extracted values only

Required patch shape:
{{
  "5A": [
    {{
      "year": null,
      "make": null,
      "model": null,
      "color": null,
      "vin": null,
      "license_plate": null,
      "registration_expiry": null,
      "insurance_company": null,
      "insurance_policy": null,
      "financing": null,
      "maintenance_records": null,
      "parking_location": null,
      "spare_keys": null,
      "notes": null
    }}
  ]
}}

If no vehicle information is found:
{{
  "5A": []
}}
"""

    return await extract_structured_from_document(
        document_url=document_url,
        mime_type=mime_type,
        prompt=prompt,
        field_catalog=field_catalog,
        response_schema=SECTION5_FULL_SCHEMA,
    )