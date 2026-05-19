from app.ai.extractors.base_extractor import extract_structured_from_document
from app.ai.schemas.section15_schema import SECTION15_FULL_SCHEMA

VALID_SECTION15_SUBSECTIONS = {
    "15A",
    "15B",
}

SECTION15_PROMPT = """
You are extracting data for the 'Health Information' section of an estate planning app.

Return JSON only.
Do not guess.
Only include values clearly supported by the uploaded document.
If a field is not present, use null for scalar fields and [] for arrays.

Important global rules:
- 15A means Health Insurance & Medical Information. It is a single object.
- 15B means Healthcare Providers. It is an array.
- patch["15A"] must be an object when health/insurance data is returned.
- patch["15B"] must always be an array when healthcare provider data is returned.
- If the uploaded document describes one healthcare provider, return exactly one object inside patch["15B"].
- If the uploaded document describes multiple healthcare providers, return one object per provider inside patch["15B"].
- Keep keys exactly as required by schema.
- Never invent insurance policy numbers, medical conditions, medications, allergies, patient IDs, provider names, doctor names, contact info, portal credentials, emergency contacts, or power of attorney details.
- If insurance numbers, patient IDs, or portal information are masked, copy only the masked value exactly as shown. Never complete or unmask it.
- For patient portal access, usernames, passwords, PINs, or security answers: only copy values if clearly present in the uploaded document. If the document only says where credentials are stored, copy that storage location or note into portal_access.
- If a document only says where insurance cards, medication lists, POA documents, provider records, or medical documents are stored, copy that storage location or note into the relevant field.
- If a value is unclear, return null.
- Do not include markdown.
- Do not explain.

15A field meanings:
- primary_health_insurance = primary insurance company, plan name, policy number, member ID, group number, insurance card details, contact info, or document location.
- secondary_health_insurance = secondary insurance, supplemental plan, policy/member/group number, insurance card details, contact info, or document location.
- medicare_medicaid = Medicare, Medicaid, Medicare Advantage, supplement plan, card number, member ID, plan details, or document location.
- current_conditions = current diagnoses, chronic illnesses, medical history, ongoing health problems, or condition notes.
- allergies = drug allergies, food allergies, environmental allergies, latex allergy, reactions, or allergy notes.
- current_medications = medication names, dosages, frequency, prescribing doctor, pharmacy, medication list, supplement list, or document location.
- medical_devices = medical equipment, implants, CPAP, oxygen, pacemaker, wheelchair, walker, hearing aid, glucose monitor, prosthetic, or equipment notes.
- emergency_contact_1 = first emergency medical contact name, relationship, phone, email, address, or contact notes.
- emergency_contact_2 = second emergency medical contact name, relationship, phone, email, address, or contact notes.
- medical_power_of_attorney = healthcare proxy, medical POA, healthcare agent, living will contact, document location, uploaded document note, or attorney/agent details.

15B specialty normalization:
Use one of these values only if clearly supported:
- Primary Care Physician
- Cardiologist
- Dermatologist
- Dentist
- Optometrist/Ophthalmologist
- Neurologist
- Orthopedist
- Gynecologist
- Urologist
- Psychiatrist/Psychologist
- Pharmacy
- Physical Therapy
- Chiropractor
- Other Specialist

15B field meanings:
- provider_name = clinic, hospital, practice, pharmacy, office, provider group, or facility name.
- specialty = normalized provider specialty from the allowed list.
- doctor_name = doctor, provider, physician, dentist, therapist, pharmacist, clinician, nurse practitioner, or named professional.
- contact_info = phone, email, address, fax, website, portal URL, office contact, or uploaded contact details.
- patient_id = patient ID, account number, chart number, medical record number, member ID, or masked ID exactly as shown.
- frequency = visit frequency, appointment schedule, checkup cadence, prescription refill cadence, or follow-up pattern.
- last_visit = last visit date, last appointment date, most recent visit, or approximate date.
- conditions_treated = conditions, diagnoses, symptoms, treatments, prescriptions, or reasons for seeing this provider.
- insurance_accepted = insurance accepted, plan used, copay notes, billing notes, or network information.
- portal_access = patient portal URL, username/password only if clearly shown, access notes, or credential storage location.
- important_notes = appointment notes, care instructions, special instructions, treatment notes, referrals, or anything important for next of kin.

Common source documents:
- health insurance card
- Medicare or Medicaid card
- medication list
- allergy list
- medical history summary
- doctor visit summary
- hospital discharge document
- healthcare provider contact list
- prescription document
- pharmacy profile
- patient portal screenshot
- medical power of attorney document
- living will or healthcare directive
- emergency contact sheet
- photo or screenshot containing medical or provider details
"""


async def extract_section15_from_document(
    document_url: str,
    subsection: str | None = None,
    mime_type: str = "application/pdf",
):
    if subsection and subsection not in VALID_SECTION15_SUBSECTIONS:
        raise ValueError(f"Invalid Section 15 subsection: {subsection}")

    prompt = SECTION15_PROMPT + f"""

Requested section: health_information
Requested subsection: {subsection or "FULL_SECTION"}

Return this exact JSON structure:
- section: "health_information"
- scope: {"subsection" if subsection else "section"}
- subsection: {subsection if subsection else "null"}
- confidence: number between 0 and 1
- patch: extracted values only

If subsection is "15A", only return health insurance and medical information inside patch["15A"].
If subsection is "15B", only return healthcare provider data inside patch["15B"].
If subsection is FULL_SECTION, return both patch["15A"] and patch["15B"] if found.

Required 15A patch shape:
{{
  "15A": {{
    "primary_health_insurance": null,
    "secondary_health_insurance": null,
    "medicare_medicaid": null,
    "current_conditions": null,
    "allergies": null,
    "current_medications": null,
    "medical_devices": null,
    "emergency_contact_1": null,
    "emergency_contact_2": null,
    "medical_power_of_attorney": null
  }}
}}

Required 15B patch shape:
{{
  "15B": [
    {{
      "provider_name": null,
      "specialty": null,
      "doctor_name": null,
      "contact_info": null,
      "patient_id": null,
      "frequency": null,
      "last_visit": null,
      "conditions_treated": null,
      "insurance_accepted": null,
      "portal_access": null,
      "important_notes": null
    }}
  ]
}}

If no information is found for the requested subsection:
- for 15A return {{"15A": {{}}}}
- for 15B return {{"15B": []}}
"""

    return await extract_structured_from_document(
        document_url=document_url,
        mime_type=mime_type,
        prompt=prompt,
        response_schema=SECTION15_FULL_SCHEMA,
    )