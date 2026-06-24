from app.ai.extractors.base_extractor import extract_structured_from_document
from app.ai.schemas.section21_schema import SECTION21_FULL_SCHEMA

VALID_SECTION21_SUBSECTIONS = {
    "21A",
    "21B",
    "21C",
}

SECTION21_PROMPT = """
You are extracting data for the 'Estate Planning & Final Wishes' section of an estate planning app.

Return JSON only.
Do not guess.
Only include values clearly supported by the uploaded document.
If a field is not present, use null for scalar fields.

Important global rules:
- 21A means Estate Planning Documents. It is a single object.
- 21B means Final Arrangements & Wishes. It is a single object.
- 21C means Guardianship Arrangements. It is a single object.
- patch["21A"], patch["21B"], and patch["21C"] must be objects, not arrays.
- Keep keys exactly as required by schema.
- Never invent names, contact info, dates, legal document locations, executor names, trustee names, guardians, beneficiaries, funeral wishes, or final arrangement details.
- If sensitive legal documents are mentioned but not provided, only copy the document location/note if clearly stated.
- If a document is uploaded directly, describe that the uploaded copy/document appears to contain the relevant information.
- If a value is unclear, return null.
- Do not include markdown.
- Do not explain.

21A Estate Planning Documents field meanings:
- will_location = location of original will, uploaded will copy note, safe/folder/attorney location, or storage instructions.
- will_date = date current will was signed, executed, notarized, or last updated.
- executor_info = executor name, contact info, address, phone, email, relationship, or notes.
- alternate_executor = alternate executor name, contact info, relationship, or notes.
- will_attorney = attorney who prepared the will, law firm, phone, email, address, or document location.
- trust_info = trust name, trust type, trust document location, trust date, uploaded trust copy note, or trust summary.
- trustee_info = current trustee names, contact info, relationship, or trustee notes.
- successor_trustee = successor trustee names, contact info, relationship, or notes.
- trust_attorney = trust attorney, law firm, phone, email, address, or contact document.
- financial_poa = financial power of attorney document, agent name, contact info, document location, or uploaded copy note.
- medical_poa = medical power of attorney, healthcare proxy, agent name, contact info, document location, or uploaded copy note.
- living_will = living will, advance directive, healthcare directive, document location, or uploaded copy note.
- dnr_orders = DNR, POLST, MOLST, do-not-resuscitate instructions, document location, or uploaded copy note.
- organ_donation = organ donation preferences, donor registration, tissue donation wishes, or notes.
- primary_beneficiaries = main beneficiaries, names, relationships, contact info, inheritance summary, or notes.
- contingent_beneficiaries = alternate beneficiaries, contingent beneficiaries, names, relationships, contact info, or notes.
- special_bequests = specific gifts, personal property instructions, amounts, recipients, or bequest notes.
- charitable_bequests = charities, nonprofits, donation wishes, bequest amounts, contact info, or charitable instructions.

21B Final Arrangements & Wishes rules:

funeral_type normalization:
Use one of these values only if clearly supported:
- Traditional Funeral
- Memorial Service
- Celebration of Life
- No Service
- Other

If the service type is clearly present but does not match the list:
- funeral_type = "Other"
- funeral_type_other = the actual service preference from the document

disposition_type normalization:
Use one of these values only if clearly supported:
- Burial
- Cremation
- Donation to Science
- Other

If the disposition preference is clearly present but does not match the list:
- disposition_type = "Other"
- disposition_type_other = the actual disposition preference from the document

21B field meanings:
- funeral_type = normalized type of service.
- funeral_type_other = custom service type when funeral_type is Other.
- service_location = church, funeral home, cemetery chapel, venue, city, address, or preferred location.
- funeral_home = funeral home, mortuary, funeral director, phone, email, address, prepaid provider, or uploaded contract note.
- clergy_officiant = clergy, celebrant, officiant, minister, rabbi, imam, priest, friend, phone, email, or contact info.
- service_preferences = music, readings, flowers, dress code, speakers, rituals, viewing, visitation, program notes, or service wishes.
- disposition_type = normalized body disposition preference.
- disposition_type_other = custom disposition preference when disposition_type is Other.
- burial_location = cemetery, plot, mausoleum, grave location, deed location, plot number, or upload note.
- cremation_preferences = ashes instructions, scattering location, urn, burial, family recipient, or cremation wishes.
- body_donation_info = donation to science program, school, organization, contact, registration, or document location.
- headstone_marker = headstone, marker, inscription, monument, design, location, or memorial marker wishes.
- memorial_donations = charities, nonprofits, donation instructions, memorial fund, or recipient organizations.
- special_requests = any other funeral, memorial, cultural, religious, personal, or final arrangement wishes.
- obituary_details = obituary text, family details, achievements, interests, biography notes, or life story.
- photo_for_obituary = preferred obituary photo, photo location, uploaded photo note, or storage details.
- prepaid_funeral = prepaid funeral contract, provider, amount, contract location, policy, or upload note.
- cemetery_plot = cemetery plot ownership, deed, plot number, cemetery contact, purchase details, or upload note.
- funeral_insurance = funeral/burial insurance policy, insurer, policy number, coverage, premium, beneficiary, or document location.

21C Guardianship Arrangements rules:

guardian consent normalization:
Use one of these values only if clearly supported:
- Agreed to serve
- Needs to be asked
- Verbal agreement only
- Written agreement

21C field meanings:
- minor_children_info = names, birthdates, ages, schools, needs, or details of minor children.
- primary_guardian_name = full legal name of primary guardian.
- primary_guardian_relationship = relationship to children or parents.
- primary_guardian_contact = phone, email, address, contact card, or uploaded contact info.
- primary_guardian_consent = normalized consent status for primary guardian.
- alternate_guardian_name = full legal name of alternate guardian.
- alternate_guardian_relationship = relationship to children or parents.
- alternate_guardian_contact = phone, email, address, contact card, or uploaded contact info.
- alternate_guardian_consent = normalized consent status for alternate guardian.
- parenting_philosophy = parenting values, household rules, discipline, emotional guidance, family values, or care approach.
- education_preferences = schools, tutoring, college goals, special programs, educational philosophy, or child-specific education notes.
- religious_preferences = religious/spiritual upbringing, church/temple/mosque, rituals, values, or faith-related instructions.
- healthcare_instructions = medical history, doctors, medications, allergies, therapies, insurance, emergency care, or healthcare preferences.
- special_needs = disabilities, special education, therapy, behavioral considerations, accessibility needs, or special care notes.
- extracurricular_activities = sports, music, hobbies, clubs, lessons, interests, or activities important to each child.
- relationship_maintenance = grandparents, relatives, siblings, family friends, visitation wishes, or important relationships to preserve.
- trust_arrangements = child trust, trust document location, trustee, terms, uploaded trust note, or financial trust instructions.
- life_insurance = policies for children, beneficiaries, policy notes, guardian instructions, or insurance document location.
- education_funding = 529 plans, college savings, education accounts, scholarship funds, or education funding wishes.
- guardian_compensation = compensation, reimbursement, housing, support funds, stipend, or financial instructions for guardians.
- guardianship_will = will with guardian designation, document location, upload note, or will reference.
- guardian_letters = letters to guardians, children, instruction letters, document location, or uploaded copy note.
- custody_agreements = custody agreements, co-parenting orders, court documents, document location, or uploaded copy note.
- guardianship_attorney = family law attorney, estate attorney, law firm, contact info, or document location.
- excluded_persons = people excluded from guardianship, reasons, restrictions, or concerns.
- temporary_caregivers = short-term caregivers, babysitters, relatives, family friends, emergency pickup contacts, or temporary care instructions.
- school_contacts = school emergency contacts, authorized pickup people, school decision contacts, or school office contacts.
- medical_contacts = people authorized for emergency medical decisions, doctors, pediatricians, healthcare contacts, or medical authorization notes.

Common source documents:
- will
- trust
- power of attorney
- medical power of attorney
- living will
- advance healthcare directive
- DNR/POLST/MOLST
- beneficiary summary
- funeral plan
- prepaid funeral contract
- cemetery plot deed
- obituary draft
- memorial instruction letter
- guardianship document
- custody agreement
- letter to guardian
- child care instruction document
- insurance or trust document for children
"""


async def extract_section21_from_document(
    document_url: str,
    subsection: str | None = None,
    mime_type: str = "application/pdf",
    field_catalog: list[dict] | None = None,
):
    if subsection and subsection not in VALID_SECTION21_SUBSECTIONS:
        raise ValueError(f"Invalid Section 21 subsection: {subsection}")

    prompt = SECTION21_PROMPT + f"""

Requested section: estate_planning_final_wishes
Requested subsection: {subsection or "FULL_SECTION"}

Return this exact JSON structure:
- section: "estate_planning_final_wishes"
- scope: {"subsection" if subsection else "section"}
- subsection: {subsection if subsection else "null"}
- confidence: number between 0 and 1
- patch: extracted values only

If subsection is "21A", only return estate planning document data inside patch["21A"].
If subsection is "21B", only return final arrangement and wishes data inside patch["21B"].
If subsection is "21C", only return guardianship arrangement data inside patch["21C"].
If subsection is FULL_SECTION, return patch["21A"], patch["21B"], and patch["21C"] if found.

Required 21A patch shape:
{{
  "21A": {{
    "will_location": null,
    "will_date": null,
    "executor_info": null,
    "alternate_executor": null,
    "will_attorney": null,
    "trust_info": null,
    "trustee_info": null,
    "successor_trustee": null,
    "trust_attorney": null,
    "financial_poa": null,
    "medical_poa": null,
    "living_will": null,
    "dnr_orders": null,
    "organ_donation": null,
    "primary_beneficiaries": null,
    "contingent_beneficiaries": null,
    "special_bequests": null,
    "charitable_bequests": null
  }}
}}

Required 21B patch shape:
{{
  "21B": {{
    "funeral_type": null,
    "funeral_type_other": null,
    "service_location": null,
    "funeral_home": null,
    "clergy_officiant": null,
    "service_preferences": null,
    "disposition_type": null,
    "disposition_type_other": null,
    "burial_location": null,
    "cremation_preferences": null,
    "body_donation_info": null,
    "headstone_marker": null,
    "memorial_donations": null,
    "special_requests": null,
    "obituary_details": null,
    "photo_for_obituary": null,
    "prepaid_funeral": null,
    "cemetery_plot": null,
    "funeral_insurance": null
  }}
}}

Required 21C patch shape:
{{
  "21C": {{
    "minor_children_info": null,
    "primary_guardian_name": null,
    "primary_guardian_relationship": null,
    "primary_guardian_contact": null,
    "primary_guardian_consent": null,
    "alternate_guardian_name": null,
    "alternate_guardian_relationship": null,
    "alternate_guardian_contact": null,
    "alternate_guardian_consent": null,
    "parenting_philosophy": null,
    "education_preferences": null,
    "religious_preferences": null,
    "healthcare_instructions": null,
    "special_needs": null,
    "extracurricular_activities": null,
    "relationship_maintenance": null,
    "trust_arrangements": null,
    "life_insurance": null,
    "education_funding": null,
    "guardian_compensation": null,
    "guardianship_will": null,
    "guardian_letters": null,
    "custody_agreements": null,
    "guardianship_attorney": null,
    "excluded_persons": null,
    "temporary_caregivers": null,
    "school_contacts": null,
    "medical_contacts": null
  }}
}}

If no information is found for the requested subsection:
- for 21A return {{"21A": {{}}}}
- for 21B return {{"21B": {{}}}}
- for 21C return {{"21C": {{}}}}
"""

    return await extract_structured_from_document(
        document_url=document_url,
        mime_type=mime_type,
        prompt=prompt,
        field_catalog=field_catalog,
        response_schema=SECTION21_FULL_SCHEMA,
    )