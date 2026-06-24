from app.ai.extractors.base_extractor import extract_structured_from_document
from app.ai.schemas.section6_schema import SECTION6_FULL_SCHEMA

VALID_SECTION6_SUBSECTIONS = {
    "6A",
}

SECTION6_PROMPT = """
You are extracting data for the 'Main Residence' section of an estate planning app.

Return JSON only.
Do not guess.
Only include values clearly supported by the uploaded document.
If a field is not present, use null.

Important rules:
- The only supported subsection is 6A.
- 6A means Home Information & Inventory.
- patch["6A"] must be an object, not an array.
- If subsection is null, extract all relevant main residence data.
- If subsection is "6A", only fill patch["6A"].
- Keep keys exactly as required by schema.
- Never invent property ownership details, mortgage info, policy numbers, security codes, access codes, smart home passwords, contact names, or addresses.
- If the document only says where a document is stored, copy that storage location or note.
- If a value is unclear, return null.
- Do not include markdown.
- Do not explain.

Field meanings:
- home_address = full address of the primary residence.
- residence_type = Single Family Home, Townhouse, Condominium, Apartment, Mobile Home, Other, or document value.
- custom_residence_type = custom residence type when residence_type is Other.
- ownership_status = Own, Rent, Other, or document value.
- ownership_type = ownership structure such as Sole Ownership, Joint Tenancy, Trust Ownership, etc.
- custom_ownership_type = custom ownership type when ownership_type is Other.
- year_purchased_leased = year property was purchased or lease began.
- joint_owners = names and relationships of co-owners or joint tenants.
- county = county where property is located.

Mortgage and financial:
- mortgage_lienholder_landlord = lender, mortgage company, lienholder, landlord, or related contact info.
- payment_methods = how payments are made, autopay, online payment, check, account notes.
- property_deeds_titles = deed/title details, recording info, or location where deed/title is stored.
- mortgage_lease_statement = current mortgage statement or lease agreement info/location.
- second_mortgage_heloc = second mortgage, HELOC, lender, balance, statement, or storage location.
- property_tax_bills = property tax bill info, tax authority, payment status, or storage location.
- closing_refinancing_docs = closing/refinance documents or storage location.
- paid_off_documentation = paid-off lien/mortgage documentation or storage location.
- reverse_mortgage_info = reverse mortgage details or storage location.
- realtor_landlord_contact = real estate agent, realtor, property manager, or landlord contact.

Occupancy and home details:
- residents = people currently living in the home.
- pets = pet names, types, care instructions.
- year_built = year the home was built.
- square_footage = approximate square footage.
- lot_size = lot size.
- bedrooms = number of bedrooms.
- bathrooms = number of bathrooms.
- home_features = pool, septic, well, solar panels, generator, basement, attic, garage, etc.
- major_appliances = HVAC, water heater, washer/dryer, refrigerator, model numbers, warranties.
- home_inventory = valuable items, inventory notes, photos/video location, intended inheritors, value notes.
- inventory_date_location = date inventory completed and where it is located.

Other home documents and emergency info:
- builder_info = builder, contractor, development company info.
- home_warranty = home warranty provider, coverage, document location.
- appliance_manuals = manuals/warranties for appliances and systems.
- utility_shutoffs = water/gas/electric shutoff locations.
- circuit_breaker = breaker panel location, labeled circuit notes, photo/diagram location.
- home_systems_notes = HVAC, plumbing, electrical, septic, well, solar, generator notes.
- security_system = security system provider, monitoring company, codes/location notes.
- smart_home_devices = smart devices, apps, hubs, login/location notes.

Common source documents:
- deed or title
- mortgage statement
- lease agreement
- property tax bill
- homeowners insurance document
- home warranty document
- appliance warranty/manual
- home inspection report
- utility shutoff diagram
- home inventory file
- photos/screenshots containing home details
"""


async def extract_section6_from_document(
    document_url: str,
    subsection: str | None = None,
    mime_type: str = "application/pdf",
    field_catalog: list[dict] | None = None,
):
    if subsection and subsection not in VALID_SECTION6_SUBSECTIONS:
        raise ValueError(f"Invalid Section 6 subsection: {subsection}")

    prompt = SECTION6_PROMPT + f"""

Requested section: main_residence
Requested subsection: {subsection or "FULL_SECTION"}

Return this exact JSON structure:
- section: "main_residence"
- scope: {"subsection" if subsection else "section"}
- subsection: {subsection if subsection else "null"}
- confidence: number between 0 and 1
- patch: extracted values only

Required patch shape:
{{
  "6A": {{
    "home_address": null,
    "residence_type": null,
    "custom_residence_type": null,
    "ownership_status": null,
    "ownership_type": null,
    "custom_ownership_type": null,
    "year_purchased_leased": null,
    "joint_owners": null,
    "county": null,
    "mortgage_lienholder_landlord": null,
    "payment_methods": null,
    "property_deeds_titles": null,
    "mortgage_lease_statement": null,
    "second_mortgage_heloc": null,
    "property_tax_bills": null,
    "closing_refinancing_docs": null,
    "paid_off_documentation": null,
    "reverse_mortgage_info": null,
    "realtor_landlord_contact": null,
    "residents": null,
    "pets": null,
    "year_built": null,
    "square_footage": null,
    "lot_size": null,
    "bedrooms": null,
    "bathrooms": null,
    "home_features": null,
    "major_appliances": null,
    "home_inventory": null,
    "inventory_date_location": null,
    "builder_info": null,
    "home_warranty": null,
    "appliance_manuals": null,
    "utility_shutoffs": null,
    "circuit_breaker": null,
    "home_systems_notes": null,
    "security_system": null,
    "smart_home_devices": null
  }}
}}

If no main residence information is found:
{{
  "6A": {{}}
}}
"""

    return await extract_structured_from_document(
        document_url=document_url,
        mime_type=mime_type,
        prompt=prompt,
        field_catalog=field_catalog,
        response_schema=SECTION6_FULL_SCHEMA,
    )