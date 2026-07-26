from pydantic import BaseModel, RootModel
from typing import Dict, List, Optional


# ---------------- Upload Models ----------------

class UploadedFile(BaseModel):
    name: str
    url: str
    public_id: str
    version: Optional[int] = 1


class UploadField(BaseModel):
    text: Optional[str] = None
    files: List[UploadedFile] = []
    _deleted_files: List[str] = []
    model_config = {"extra": "ignore"}


# ---------------- 17A — Ancestry & Family Tree (NON-REPEATABLE) ----------------

class AncestryFamilyTree(BaseModel):
    family_tree_overview: Optional[str] = None
    genealogy_research: Optional[str] = None
    ancestral_origins: Optional[str] = None
    family_stories: Optional[str] = None
    genealogy_contacts: Optional[str] = None

    family_records: Optional[UploadField] = None
    dna_testing: Optional[UploadField] = None


# ---------------- Repeatable Models (17B–17G) ----------------

class FamilyMember(BaseModel):
    person_name: Optional[str] = None
    relationship: Optional[str] = None
    contact_info: Optional[str] = None
    birthdate: Optional[str] = None
    importance: Optional[str] = None
    notify_instructions: Optional[str] = None
    special_considerations: Optional[str] = None
    photos_mementos: Optional[UploadField] = None


class Dependent(BaseModel):
    dependent_name: Optional[str] = None
    relationship: Optional[str] = None
    birthdate: Optional[str] = None
    dependency_type: Optional[str] = None
    support_details: Optional[str] = None
    backup_caregivers: Optional[str] = None
    special_needs: Optional[str] = None
    future_care_plans: Optional[str] = None

    legal_documents: Optional[UploadField] = None
    financial_accounts: Optional[str] = None


class Friend(BaseModel):
    friend_name: Optional[str] = None
    friendship_type: Optional[str] = None
    friendship_type_other: Optional[str] = None
    contact_info: Optional[str] = None
    how_we_met: Optional[str] = None
    friendship_significance: Optional[str] = None
    notify_instructions: Optional[str] = None
    shared_memories: Optional[str] = None
    photos_mementos: Optional[UploadField] = None


class ImportantRelationship(BaseModel):
    person_name: Optional[str] = None
    relationship_type: Optional[str] = None
    relationship_type_other: Optional[str] = None
    contact_info: Optional[str] = None
    relationship_significance: Optional[str] = None
    notify_instructions: Optional[str] = None
    special_notes: Optional[str] = None
    relationship_documents: Optional[UploadField] = None


class SentimentalItem(BaseModel):
    item_name: Optional[str] = None
    item_type: Optional[str] = None
    item_type_other: Optional[str] = None
    sentimental_value: Optional[str] = None
    current_location: Optional[str] = None
    intended_recipient: Optional[str] = None
    care_instructions: Optional[str] = None
    estimated_value: Optional[str] = None
    documentation: Optional[UploadField] = None


class Pet(BaseModel):
    pet_name: Optional[str] = None
    pet_type: Optional[str] = None
    pet_type_other: Optional[str] = None
    breed_age: Optional[str] = None
    veterinarian: Optional[str] = None
    medical_history: Optional[str] = None
    feeding_care: Optional[str] = None
    emergency_contact: Optional[str] = None
    long_term_care: Optional[str] = None
    pet_supplies: Optional[str] = None
    registration_microchip: Optional[str] = None
    veterinary_records: Optional[UploadField] = None


# ---------------- Root Payload ----------------
# {
#   "17A": {...},
#   "17B": [...],
#   "17C": [...],
#   ...
# }

class Section17FamilyTreasuredConnectionsPayload(
    RootModel[Dict[str, object]]
):
    pass
