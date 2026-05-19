# app/ai/autofill_registry.py

from app.ai.extractors.section1_extractor import extract_section1_from_document
from app.ai.extractors.section5_extractor import extract_section5_from_document
from app.ai.extractors.section6_extractor import extract_section6_from_document
from app.ai.extractors.section7_extractor import extract_section7_from_document
from app.ai.extractors.section8_extractor import extract_section8_from_document
from app.ai.extractors.section9_extractor import extract_section9_from_document
from app.ai.extractors.section10_extractor import extract_section10_from_document
from app.ai.extractors.section11_extractor import extract_section11_from_document
from app.ai.extractors.section12_extractor import extract_section12_from_document
from app.ai.extractors.section13_extractor import extract_section13_from_document
from app.ai.extractors.section14_extractor import extract_section14_from_document
from app.ai.extractors.section15_extractor import extract_section15_from_document
from app.ai.extractors.section16_extractor import extract_section16_from_document
from app.ai.extractors.section17_extractor import extract_section17_from_document
from app.ai.extractors.section18_extractor import extract_section18_from_document
from app.ai.extractors.section19_extractor import extract_section19_from_document
from app.ai.extractors.section20_extractor import extract_section20_from_document
from app.ai.extractors.section21_extractor import extract_section21_from_document


SECTION_EXTRACTORS = {
    "vital_information": extract_section1_from_document,
    "vehicles": extract_section5_from_document,
    "main_residence": extract_section6_from_document,
    "insurance_policies": extract_section7_from_document,
    "community_memberships": extract_section8_from_document,
    "charitable_giving": extract_section9_from_document,
    "education_accomplishments": extract_section10_from_document,
    "military_service": extract_section11_from_document,
    "banking_financial_accounts": extract_section12_from_document,
    "passwords_online_accounts": extract_section13_from_document,
    "investment_accounts": extract_section14_from_document,
    "health_information": extract_section15_from_document,
    "credit_cards_debt": extract_section16_from_document,
    "family_treasured_connections": extract_section17_from_document,
    "employment_business": extract_section18_from_document,
    "assets_valuables": extract_section19_from_document,
    "legal_documents_records": extract_section20_from_document,
    "estate_planning_final_wishes": extract_section21_from_document,
}