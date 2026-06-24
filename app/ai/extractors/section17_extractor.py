# app/ai/extractors/section17_extractor.py

from app.ai.extractors.base_extractor import extract_structured_from_document
from app.ai.schemas.section17_schema import SECTION17_FULL_SCHEMA

VALID_SECTION17_SUBSECTIONS = {
    "17A",
    "17B",
    "17C",
    "17D",
    "17E",
    "17F",
    "17G",
}

SECTION17_PROMPT = """
You are extracting data for the 'Family & Treasured Connections' section of an estate planning app.

Return JSON only.
Do not guess.
Only include values clearly supported by the uploaded document.
If a field is not present, use null for scalar fields and [] for arrays.

Important global rules:
- 17A Ancestry & Family Tree is a single object.
- 17B Family Members is an array.
- 17C Dependents is an array.
- 17D Close Friends is an array.
- 17E Important Relationships is an array.
- 17F Memorabilia & Sentimental Items is an array.
- 17G Pet Care & Records is an array.
- patch["17A"] must be an object.
- patch["17B"], patch["17C"], patch["17D"], patch["17E"], patch["17F"], and patch["17G"] must always be arrays.
- If a repeatable subsection is requested and the uploaded document describes one item/person/pet, return exactly one object inside that subsection array.
- If the document clearly describes multiple people/items/pets, return multiple objects.
- Keep keys exactly as required by schema.
- Never invent names, addresses, phone numbers, emails, birthdates, family relationships, pet records, item values, or care instructions.
- If a document is uploaded directly, summarize the relevant uploaded document reference in the matching upload/documentation field if useful.
- If a value is unclear, return null.
- Do not include markdown.
- Do not explain.

17A Ancestry & Family Tree field meanings:
- family_tree_overview = family lineage, parents, grandparents, spouse, children, known relatives, or family structure.
- genealogy_research = genealogy research notes, family history discoveries, ancestry research, family records, oral history, or genealogy researcher notes.
- ancestral_origins = countries, regions, immigration history, ethnic/cultural heritage, family origin locations, or ancestral background.
- family_stories = stories, traditions, family memories, oral history, cultural traditions, or meaningful family events.
- genealogy_contacts = relatives, genealogy researchers, historians, family members with ancestry knowledge, names, phone, email, or contact notes.
- family_records = family tree documents, birth/marriage/death records, certificates, family archive location, uploaded family record note.
- dna_testing = ancestry DNA test results, genetic genealogy service, DNA match notes, account/service name, or uploaded DNA result note.

17B Family Members field meanings:
- person_name = full name of family member.
- relationship = relationship to the user.
- contact_info = phone, email, address, social profile, or contact notes.
- birthdate = date of birth.
- importance = relationship importance, special memories, family role, or what family should know.
- notify_instructions = notification urgency.
- special_considerations = health issues, sensitive relationship notes, contact warnings, disability, family situation, or special circumstances.
- photos_mementos = uploaded photo note, memento location, family photo, letter, or document related to this family member.

17B relationship normalization:
Use one of these values only if clearly supported:
- Spouse/Partner
- Child
- Parent
- Sibling
- Grandparent
- Grandchild
- In-Law
- Niece/Nephew
- Aunt/Uncle
- Cousin
- Other Family

17C Dependents field meanings:
- dependent_name = full name of dependent.
- relationship = relationship to the user.
- birthdate = date of birth.
- dependency_type = type of dependency.
- support_details = financial, physical, medical, emotional, or legal support details, amount, frequency, routine, or care notes.
- backup_caregivers = backup caregivers, names, contact details, emergency carers, or support contacts.
- special_needs = medical conditions, disabilities, special care, therapy, medications, accessibility needs, or special requirements.
- future_care_plans = care wishes if the user cannot provide care, future arrangements, guardianship plans, or support instructions.
- legal_documents = guardianship papers, custody agreements, support documents, legal authorization, document location, or uploaded legal document note.
- financial_accounts = accounts, trusts, savings, benefits, support payments, financial arrangements, or related account notes.

17C relationship normalization:
Use one of these values only if clearly supported:
- Child
- Stepchild
- Adopted Child
- Parent
- Stepparent
- Grandparent
- Grandchild
- Spouse/Partner
- Sibling
- Other Family Member
- Non-Family Dependent

17C dependency_type normalization:
Use one of these values only if clearly supported:
- Financial Support
- Physical Care
- Medical Care
- Legal Guardianship
- Emotional Support
- Multiple Types

17D Close Friends field meanings:
- friend_name = full name of close friend.
- friendship_type = normalized friendship type.
- friendship_type_other = actual friendship type if friendship_type is Other.
- contact_info = phone, email, address, or contact notes.
- how_we_met = how and when they met, origin of friendship, place/event/school/work connection.
- friendship_significance = why the friendship matters, emotional significance, shared life events, or what family should know.
- notify_instructions = notification urgency.
- shared_memories = memories, stories, jokes, trips, milestones, or things to preserve.
- photos_mementos = uploaded photo/document note, photos, letters, keepsakes, or mementos related to the friendship.

17D friendship_type normalization:
Use one of these values only if clearly supported:
- Best Friend
- Close Friend
- Work Friend
- Childhood Friend
- School Friend
- Neighbor
- Activity Partner
- Other

17E Important Relationships field meanings:
- person_name = full name of important person.
- relationship_type = normalized relationship type.
- relationship_type_other = actual relationship type if relationship_type is Other.
- contact_info = phone, email, address, or contact notes.
- relationship_significance = why this person matters, role in life, emotional or practical importance.
- notify_instructions = notification urgency.
- special_notes = messages, considerations, relationship notes, special instructions, or sensitivities.
- relationship_documents = uploaded photo/letter/document note, documents, letters, or mementos related to this person.

17E relationship_type normalization:
Use one of these values only if clearly supported:
- Mentor
- Student/Mentee
- Caregiver
- Former Partner
- Godparent/Godchild
- Family Friend
- Neighbor
- Professional Contact
- Spiritual Guide
- Other

17F Memorabilia & Sentimental Items field meanings:
- item_name = item name or short description.
- item_type = normalized item category.
- item_type_other = actual item type if item_type is Other.
- sentimental_value = story, history, emotional meaning, family significance, origin, memories, or why item matters.
- current_location = where item is stored or displayed.
- intended_recipient = who should receive the item.
- care_instructions = handling, storage, preservation, cleaning, insurance, or protection instructions.
- estimated_value = approximate monetary value if shown.
- documentation = uploaded photo/document note, appraisal, certificate, provenance, receipt, story document, or photo location.

17F item_type normalization:
Use one of these values only if clearly supported:
- Family Heirloom
- Photo Album
- Jewelry
- Artwork
- Books/Documents
- Clothing/Textiles
- Furniture
- Religious/Spiritual Items
- Military Memorabilia
- Childhood Keepsakes
- Letters/Correspondence
- Other

17G Pet Care & Records field meanings:
- pet_name = pet name.
- pet_type = normalized animal type.
- pet_type_other = actual animal type if pet_type is Other.
- breed_age = breed, age, birth date, color, identifying features.
- veterinarian = vet name, clinic, address, phone, email, emergency vet, or contact notes.
- medical_history = conditions, medications, allergies, vaccination history, treatments, surgeries, or special health needs.
- feeding_care = feeding schedule, food, medication routine, exercise, daily care, grooming, behavior, favorite things, or care instructions.
- emergency_contact = emergency pet caregiver, contact name, phone, email, boarding contact, or pet sitter.
- long_term_care = future care wishes, preferred caregiver, adoption plan, funding, instructions if user cannot care.
- pet_supplies = food, litter, leash, crate, medicines, equipment, location of supplies.
- registration_microchip = microchip number, license, registration, adoption records, insurance, or ID tag details.
- veterinary_records = uploaded vet records, vaccination certificate, medication document, microchip record, pet photo, or document location.

17G pet_type normalization:
Use one of these values only if clearly supported:
- Dog
- Cat
- Bird
- Fish
- Rabbit
- Hamster/Guinea Pig
- Reptile
- Horse
- Farm Animal
- Exotic Pet
- Other

Notification instruction normalization:
Use one of these values only if clearly supported:
- Notify Immediately
- Notify Within a Week
- Notify When Convenient
- Do Not Notify

Common source documents:
- family tree
- genealogy report
- ancestry DNA result
- birth/marriage/death certificates
- family contact list
- emergency contact list
- dependent care plan
- custody/guardianship document
- letter to family
- personal letter
- friendship note
- photo/memento description
- item appraisal
- insurance inventory
- pet vaccination record
- veterinary record
- microchip registration
- pet care instruction document
"""


async def extract_section17_from_document(
    document_url: str,
    subsection: str | None = None,
    mime_type: str = "application/pdf",
    field_catalog: list[dict] | None = None,
):
    if subsection and subsection not in VALID_SECTION17_SUBSECTIONS:
        raise ValueError(f"Invalid Section 17 subsection: {subsection}")

    prompt = SECTION17_PROMPT + f"""

Requested section: family_treasured_connections
Requested subsection: {subsection or "FULL_SECTION"}

Return this exact JSON structure:
- section: "family_treasured_connections"
- scope: {"subsection" if subsection else "section"}
- subsection: {subsection if subsection else "null"}
- confidence: number between 0 and 1
- patch: extracted values only

If subsection is "17A", only return ancestry/family tree data inside patch["17A"].
If subsection is "17B", only return family member data inside patch["17B"] as an array.
If subsection is "17C", only return dependent data inside patch["17C"] as an array.
If subsection is "17D", only return close friend data inside patch["17D"] as an array.
If subsection is "17E", only return important relationship data inside patch["17E"] as an array.
If subsection is "17F", only return sentimental item data inside patch["17F"] as an array.
If subsection is "17G", only return pet care data inside patch["17G"] as an array.
If subsection is FULL_SECTION, return all relevant patch keys if found.

Required 17A patch shape:
{{
  "17A": {{
    "family_tree_overview": null,
    "genealogy_research": null,
    "ancestral_origins": null,
    "family_stories": null,
    "genealogy_contacts": null,
    "family_records": null,
    "dna_testing": null
  }}
}}

Required repeatable patch shape:
{{
  "17B": [
    {{
      "person_name": null,
      "relationship": null,
      "contact_info": null,
      "birthdate": null,
      "importance": null,
      "notify_instructions": null,
      "special_considerations": null,
      "photos_mementos": null
    }}
  ],
  "17C": [],
  "17D": [],
  "17E": [],
  "17F": [],
  "17G": []
}}

If no information is found for the requested subsection:
- for 17A return {{"17A": {{}}}}
- for 17B return {{"17B": []}}
- for 17C return {{"17C": []}}
- for 17D return {{"17D": []}}
- for 17E return {{"17E": []}}
- for 17F return {{"17F": []}}
- for 17G return {{"17G": []}}
"""

    return await extract_structured_from_document(
        document_url=document_url,
        mime_type=mime_type,
        prompt=prompt,
        field_catalog=field_catalog,
        response_schema=SECTION17_FULL_SCHEMA,
    )