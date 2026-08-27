"""
Authorization to Collect, Verify, and Use Death Certificate and Vital Information.

Owner must agree (checkbox + typed electronic signature) before naming anyone
for after-death Vault access. Stored on the owner record and shown in the vault.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

VERSION = "2026-08-19"
TITLE = (
    "Authorization to Collect, Verify, and Use Death Certificate "
    "and Vital Information"
)
LAST_UPDATED = "August 19, 2026"
COMPANY = "Orderly Affairs Digital, LLC"
SUPPORT_EMAIL = "support@orderly-affairs.com"
ADDRESS_LINES = ["5900 Balcones Drive STE 100", "Austin, TX 78731"]

OWNER_RECORD_KEY = "death_certificate_authorization"

CHECKBOX_LABEL = (
    "I have read this Authorization and I agree that Orderly Affairs Digital, "
    "LLC may collect, review, retain, and share my death certificate and related "
    "vital information solely to verify my death and administer next-of-kin, "
    "executor, or attorney access to my Vault."
)

INTRO = [
    (
        'This Authorization is between you, the Vault account holder ("you," "I," '
        'or "Account Holder"), and Orderly Affairs Digital, LLC, a Texas limited '
        'liability company ("Orderly Affairs," "Company," "we," or "us"). It is '
        "separate from, and in addition to, the Orderly Affairs Terms of Service "
        "and Privacy Policy, which continue to apply. You must affirmatively agree "
        "to this Authorization, by checkbox and electronic signature at account "
        "setup or in Access Management, before Orderly Affairs will act on any "
        "death certificate or related information in connection with your account."
    ),
]

SECTIONS: list[dict[str, str]] = [
    {
        "number": "1",
        "title": "Why this exists",
        "body": (
            "Orderly Affairs releases Vault access to your named next of kin only "
            "after verifying that you have died. That verification depends on a "
            "death certificate and a small set of identifying details about you. "
            "This Authorization gives us permission to collect, review, cross-check, "
            "retain, and share that information for that one purpose. Without it "
            "signed and on file, we cannot act on a death certificate even if your "
            "next of kin uploads one, and your Vault will not unlock through this "
            "process.\n\n"
            "Separately, your next of kin, and your attorney or executor if you've "
            "named one, each have to verify their own identity at the point they "
            "take action, a government-issued ID plus a live selfie, matched against "
            "the contact details on file for them. That check is about confirming "
            "who they are, using their own information, not yours, and it happens "
            "under their own consent when they complete it. This Authorization does "
            "not cover that step."
        ),
    },
    {
        "number": "2",
        "title": "What you are authorizing",
        "body": (
            "You authorize Orderly Affairs to do each of the following, solely for "
            "the purpose of verifying your death and administering next-of-kin "
            "access to your Vault under the process described in our published Vault "
            "unlock instructions:\n\n"
            "Receive and retain a copy of your death certificate or comparable "
            "official documentation (such as a probate filing) when it is submitted "
            "by a next of kin, attorney, or executor you have named on your account. "
            "Orderly Affairs does not request certified copies of your death "
            "certificate directly from a vital records office; state law generally "
            "limits who may obtain one, and it is your next of kin, attorney, or "
            "executor who must be the qualified applicant for that copy.\n\n"
            "Extract and review the information on that document, including your "
            "full legal name, date of birth, date and place of death, and any "
            "certificate or file number, together with any comparable vital "
            "information you have separately stored in your Vault, such as the last "
            "four digits of a Social Security number, for the purpose of confirming "
            "it matches your account.\n\n"
            "Share the document and the extracted information with third-party "
            "service providers who perform document-authenticity review and "
            "independent mortality-record cross-checks on our behalf, limited to "
            "what those providers need to perform that specific check, and subject "
            "to their own confidentiality obligations to us.\n\n"
            "Retain the document and the verification record for so long as "
            "reasonably necessary to complete the verification, respond to a "
            "dispute about it, and comply with our record-keeping and legal "
            "obligations, consistent with the retention terms in our Privacy Policy."
        ),
    },
    {
        "number": "3",
        "title": "What this does not authorize",
        "body": (
            "This Authorization does not permit Orderly Affairs to use your death "
            "certificate or vital information for any purpose other than verifying "
            "your death and administering the next-of-kin access process. It does "
            "not authorize us to sell that information, use it for marketing, or "
            "share it beyond the verification providers described above. It also "
            "does not, on its own, grant anyone legal authority to act on your "
            "behalf, close your accounts, or administer your estate. Whoever gains "
            "access to your Vault will still need a validly executed will, trust, "
            "power of attorney, or letters of administration or testamentary, as "
            "applicable, to take those actions."
        ),
    },
    {
        "number": "4",
        "title": "Notification and your right to stop it",
        "body": (
            "Consistent with the Vault unlock process, any attempt to act on a "
            "death certificate submitted under this Authorization triggers a "
            "notification to you on every contact channel on file immediately, the "
            "moment the process starts, not after any other check has run. If you "
            "are living and receive that notification, the request is cancelled and "
            "no access changes. This Authorization does not shorten, replace, or "
            "bypass that notification window."
        ),
    },
    {
        "number": "5",
        "title": "Duration and revocation",
        "body": (
            "This Authorization takes effect when you agree to it and remains in "
            "effect until you revoke it in writing, delete your account, or it is "
            "superseded by an updated version we publish. You may revoke it at any "
            "time while living, from Access Management or by writing to "
            f"{SUPPORT_EMAIL}. Because its entire purpose is to allow verification "
            "after your death, a revocation only has practical effect while you are "
            "alive to make it. If a death certificate has already been submitted by "
            "a next of kin, attorney, or executor at the time you revoke, that "
            "specific request may still need to complete or be formally closed out; "
            "it will not restart the process from scratch."
        ),
    },
    {
        "number": "6",
        "title": "Accuracy of information",
        "body": (
            "You are responsible for keeping the vital information stored in your "
            "Vault accurate and current, since it is what your death certificate is "
            "checked against. Inaccurate or outdated information on your account "
            "may cause a verification to be flagged for manual review, or, in rare "
            "cases, to fail, which delays your next of kin rather than preventing "
            "them from ever gaining access."
        ),
    },
    {
        "number": "7",
        "title": "Not legal advice",
        "body": (
            "This Authorization is a consent and data-handling document, not a "
            "legal instrument that transfers property, appoints a fiduciary, or has "
            "the force of a will, trust, or power of attorney. Orderly Affairs is "
            "not a law firm and does not give legal advice. If you have questions "
            "about how this interacts with your estate plan, or with your state's "
            "laws on death certificates, vital records, or digital assets, "
            "including the Revised Uniform Fiduciary Access to Digital Assets Act "
            "as adopted in your state, talk to a licensed attorney."
        ),
    },
    {
        "number": "8",
        "title": "Contact",
        "body": (
            f"{COMPANY}\n"
            + "\n".join(ADDRESS_LINES)
            + f"\n{SUPPORT_EMAIL}"
        ),
    },
]

AFTER_DEATH_ACCESS_REQUIRES_AUTH = (
    "Agree to the Authorization to Collect, Verify, and Use Death Certificate "
    "and Vital Information before naming a next of kin, executor, attorney, or "
    "anyone else for after-death Vault access."
)

PERSON_CONFIRM_REQUIRED = (
    "Confirm the death certificate authorization for this person before saving "
    "after-death Vault access."
)

SIGNATURE_REQUIRED = (
    "Type your full legal name as your electronic signature to agree to the "
    "Authorization."
)


def document_payload() -> dict[str, Any]:
    return {
        "title": TITLE,
        "last_updated": LAST_UPDATED,
        "version": VERSION,
        "company": COMPANY,
        "support_email": SUPPORT_EMAIL,
        "address_lines": ADDRESS_LINES,
        "intro": INTRO,
        "sections": SECTIONS,
        "checkbox_label": CHECKBOX_LABEL,
    }


def owner_has_death_certificate_authorization(owner: dict | None) -> bool:
    rec = (owner or {}).get(OWNER_RECORD_KEY) or {}
    return bool(rec.get("agreed")) and str(rec.get("version") or "") == VERSION


def agreement_status(owner: dict | None) -> dict[str, Any]:
    rec = (owner or {}).get(OWNER_RECORD_KEY) or {}
    agreed = owner_has_death_certificate_authorization(owner)
    return {
        "agreed": agreed,
        "agreed_at": rec.get("agreed_at") if agreed else None,
        "signature_name": rec.get("signature_name") if agreed else None,
        "version": rec.get("version") if agreed else None,
    }


def agreement_set_fields(signature_name: str) -> dict[str, Any]:
    cleaned = (signature_name or "").strip()
    return {
        OWNER_RECORD_KEY: {
            "version": VERSION,
            "agreed": True,
            "agreed_at": datetime.now(timezone.utc),
            "signature_name": cleaned,
        }
    }
