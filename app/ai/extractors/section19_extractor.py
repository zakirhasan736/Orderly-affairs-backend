from app.ai.extractors.base_extractor import extract_structured_from_document
from app.ai.schemas.section19_schema import SECTION19_FULL_SCHEMA

VALID_SECTION19_SUBSECTIONS = {
    "19A",
    "19B",
}

SECTION19_PROMPT = """
You are extracting data for the 'Assets & Valuables' section of an estate planning app.

Return JSON only.
Do not guess.
Only include values clearly supported by the uploaded document.
If a field is not present, use null for scalar fields and [] for arrays.

Important global rules:
- 19A means Valuable Items.
- 19B means Real Estate Properties.
- patch["19A"] must always be an array when valuable item data is returned.
- patch["19B"] must always be an array when real estate property data is returned.
- If the uploaded document describes one valuable item or one property, return exactly one object inside the correct array.
- If the uploaded document describes multiple valuable items or properties, return one object per item/property.
- Keep keys exactly as required by schema.
- Never invent values, purchase information, recipients, appraisals, property addresses, ownership details, mortgage details, tenant details, insurance details, or document locations.
- If values are approximate or estimated in the document, copy them as approximate.
- If a document only says where certificates, appraisals, deeds, titles, receipts, property files, photos, or documents are stored, copy that storage location or note into the relevant document field.
- If a value is unclear, return null.
- Do not include markdown.
- Do not explain.

19A Valuable Items rules:

item_type normalization:
Use one of these values only if clearly supported:
- Jewelry
- Artwork
- Collectibles
- Antiques
- Precious Metals
- Coins/Currency
- Electronics
- Musical Instruments
- Sports Memorabilia
- Books/Documents
- Furniture
- Tools/Equipment
- Other

If the item type is clearly present but does not match the list:
- item_type = "Other"
- item_type_other = the actual item type from the document

19A field meanings:
- item_type = normalized valuable item category.
- item_type_other = custom item type when item_type is Other.
- item_description = detailed description including brand, model, serial number, material, identifying characteristics, size, condition, color, quantity, certificate number, or distinguishing details.
- estimated_value = current value, appraisal value, insured value, purchase value, estimated market value, or approximate value.
- purchase_info = purchase date, seller, store, auction, original cost, receipt details, invoice details, or acquisition notes.
- current_location = where the item is stored, displayed, secured, located, safe/lockbox details, storage unit, room, address, or possession holder.
- insurance_info = insurance carrier, policy number, scheduled item coverage, rider, insured amount, coverage notes, or document location.
- appraisal_info = appraisal date, appraiser, appraisal amount, authenticity certificate, certificate number, valuation document, or storage location.
- intended_recipient = person, heir, family member, charity, organization, or instructions for who should receive the item.
- care_instructions = maintenance, cleaning, storage, handling, preservation, repair, climate control, or special care notes.
- item_history = family history, sentimental value, provenance, origin story, inheritance history, significance, or why the item is important.
- item_documents = photos, receipts, certificates, appraisals, authenticity documents, warranty, storage location, or upload notes.

19B Real Estate Properties rules:

property_type normalization:
Use one of these values only if clearly supported:
- Residential Rental
- Commercial Property
- Vacant Land
- Investment Property
- Vacation Home
- Mobile Home
- Condo/Townhouse
- Farm/Agricultural
- Other

If the property type is clearly present but does not match the list:
- property_type = "Other"
- property_type_other = the actual property type from the document

19B field meanings:
- property_type = normalized real estate property type.
- property_type_other = custom property type when property_type is Other.
- property_address = full property address, parcel location, legal description, lot/block, or property location.
- property_description = size, acreage, square footage, rooms, features, condition, improvements, zoning, land use, or property details.
- ownership_details = owner names, joint ownership, trust ownership, LLC ownership, deed type, title details, percentages, or vesting details.
- purchase_info = purchase date, purchase price, seller, closing details, settlement statement details, or acquisition notes.
- current_value = appraised value, market value, assessed value, estimated value, tax value, or approximate value.
- mortgage_info = lender, loan number, mortgage balance, monthly payment, escrow, due date, interest rate, payoff info, or mortgage document location.
- rental_info = tenant names, lease dates, rental income, deposit, lease terms, property occupancy, tenant contact, or rental management notes.
- property_manager = property manager name, management company, phone, email, address, contact card, or management agreement details.
- property_taxes = annual taxes, tax parcel ID, tax bill, payment method, assessed value, tax authority, or tax document location.
- insurance_info = insurance company, policy number, coverage amount, premium, agent contact, flood/landlord/property insurance, or insurance document location.
- intended_disposition = instructions to sell, keep, transfer, specific heir, trust handling, charity, family wishes, or estate instructions.
- property_documents = deed, title, survey, appraisal, inspection, lease, tax bill, insurance policy, mortgage document, closing statement, property photos, storage location, or upload notes.

Common source documents:
- valuable item inventory
- appraisal report
- jewelry appraisal
- art certificate
- certificate of authenticity
- purchase receipt
- invoice
- warranty
- insurance schedule
- photos or screenshots of valuables
- deed
- property tax bill
- mortgage statement
- lease agreement
- property appraisal
- title document
- settlement statement
- survey
- property insurance policy
- property management agreement
- real estate inventory document
"""


async def extract_section19_from_document(
    document_url: str,
    subsection: str | None = None,
    mime_type: str = "application/pdf",
    field_catalog: list[dict] | None = None,
):
    if subsection and subsection not in VALID_SECTION19_SUBSECTIONS:
        raise ValueError(f"Invalid Section 19 subsection: {subsection}")

    prompt = SECTION19_PROMPT + f"""

Requested section: assets_valuables
Requested subsection: {subsection or "FULL_SECTION"}

Return this exact JSON structure:
- section: "assets_valuables"
- scope: {"subsection" if subsection else "section"}
- subsection: {subsection if subsection else "null"}
- confidence: number between 0 and 1
- patch: extracted values only

If subsection is "19A", only return valuable item data inside patch["19A"].
If subsection is "19B", only return real estate property data inside patch["19B"].
If subsection is FULL_SECTION, return both patch["19A"] and patch["19B"] if found.

Required 19A patch shape:
{{
  "19A": [
    {{
      "item_type": null,
      "item_type_other": null,
      "item_description": null,
      "estimated_value": null,
      "purchase_info": null,
      "current_location": null,
      "insurance_info": null,
      "appraisal_info": null,
      "intended_recipient": null,
      "care_instructions": null,
      "item_history": null,
      "item_documents": null
    }}
  ]
}}

Required 19B patch shape:
{{
  "19B": [
    {{
      "property_type": null,
      "property_type_other": null,
      "property_address": null,
      "property_description": null,
      "ownership_details": null,
      "purchase_info": null,
      "current_value": null,
      "mortgage_info": null,
      "rental_info": null,
      "property_manager": null,
      "property_taxes": null,
      "insurance_info": null,
      "intended_disposition": null,
      "property_documents": null
    }}
  ]
}}

If no information is found for the requested subsection:
- for 19A return {{"19A": []}}
- for 19B return {{"19B": []}}
"""

    return await extract_structured_from_document(
        document_url=document_url,
        mime_type=mime_type,
        prompt=prompt,
        field_catalog=field_catalog,
        response_schema=SECTION19_FULL_SCHEMA,
    )