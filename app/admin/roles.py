"""Admin roles & staff access person management."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, EmailStr, Field

from app.admin.audit import log_admin_action
from app.admin.deps import is_permitted_admin, require_admin, require_system_owner
from app.admin.permissions import (
    ADMIN_AREAS,
    AREA_IDS,
    BUILTIN_ROLES,
    PERM_MATRIX_COLUMNS,
    PERM_MATRIX_ROWS,
    normalize_admin_role,
    resolve_areas_for_role,
    user_can_manage_roles,
)
from app.billing.access import default_billing_fields
from app.database import admin_role_defs_collection, users_collection
from app.security.password_handler import hash_password

admin_roles_router = APIRouter(prefix="/admin/roles", tags=["admin-roles"])


class CreateRoleDefRequest(BaseModel):
    id: str = Field(min_length=2, max_length=40, pattern=r"^[a-z][a-z0-9_]*$")
    label: str = Field(min_length=2, max_length=80)
    description: str = Field(default="", max_length=400)
    areas: list[str] = Field(default_factory=list)
    can_manage_roles: bool = False
    can_delete_users: bool = False
    read_only: bool = False


class InviteStaffRequest(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=128)
    admin_role: str = Field(min_length=2, max_length=40)
    # Empty / omit → use role defaults. ["*"] → all areas.
    admin_areas: Optional[list[str]] = None
    note: Optional[str] = Field(default=None, max_length=400)


class PatchStaffRequest(BaseModel):
    admin_role: Optional[str] = None
    admin_areas: Optional[list[str]] = None
    is_admin: Optional[bool] = None
    suspended: Optional[bool] = None
    full_name: Optional[str] = Field(default=None, max_length=120)


def _require_role_manager(admin: dict) -> None:
    user = admin.get("user") or {}
    if not user_can_manage_roles(user) and admin.get("admin_role") not in (
        "super_admin",
        "system_owner",
    ):
        raise HTTPException(403, "Only Super Admin can manage roles and staff")


async def _custom_role_map() -> dict[str, dict]:
    out: dict[str, dict] = {}
    async for doc in admin_role_defs_collection.find({}):
        rid = doc.get("id") or doc.get("_id")
        if not rid:
            continue
        out[str(rid)] = {
            "id": str(rid),
            "label": doc.get("label") or str(rid),
            "description": doc.get("description") or "",
            "areas": doc.get("areas") or [],
            "can_manage_roles": bool(doc.get("can_manage_roles")),
            "can_delete_users": bool(doc.get("can_delete_users")),
            "read_only": bool(doc.get("read_only")),
            "builtin": False,
        }
    return out


async def _all_roles() -> dict[str, dict]:
    merged = {**BUILTIN_ROLES}
    merged.update(await _custom_role_map())
    return merged


@admin_roles_router.get("/catalog")
async def roles_catalog(
    request: Request,
    authorization: str | None = Header(default=None),
):
    await require_admin(request, authorization)
    roles = list((await _all_roles()).values())
    return {
        "areas": ADMIN_AREAS,
        "roles": roles,
        "matrix_columns": [
            {
                "id": c,
                "label": (BUILTIN_ROLES.get(c) or {}).get("label") or c,
            }
            for c in PERM_MATRIX_COLUMNS
        ],
        "matrix_rows": PERM_MATRIX_ROWS,
    }


@admin_roles_router.post("/definitions")
async def create_role_definition(
    payload: CreateRoleDefRequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    admin = await require_system_owner(request, authorization)
    _require_role_manager(admin)

    rid = payload.id.strip().lower()
    if rid in BUILTIN_ROLES or rid in ("system_owner",):
        raise HTTPException(400, "Cannot overwrite a built-in role id")

    areas = payload.areas
    if "*" not in areas:
        areas = [a for a in areas if a in AREA_IDS]
        if not areas:
            raise HTTPException(400, "Select at least one access area")

    existing = await admin_role_defs_collection.find_one({"id": rid})
    if existing:
        raise HTTPException(400, "Role id already exists")

    doc = {
        "id": rid,
        "label": payload.label.strip(),
        "description": payload.description.strip(),
        "areas": areas,
        "can_manage_roles": payload.can_manage_roles,
        "can_delete_users": payload.can_delete_users,
        "read_only": payload.read_only,
        "created_at": datetime.utcnow(),
        "created_by": admin.get("email"),
    }
    await admin_role_defs_collection.insert_one(doc)
    await log_admin_action(
        admin.get("email") or "",
        "role_definition_create",
        target=rid,
        meta={"areas": areas},
    )
    return {"role": {k: v for k, v in doc.items() if k != "_id"}, "builtin": False}


@admin_roles_router.delete("/definitions/{role_id}")
async def delete_role_definition(
    role_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
):
    admin = await require_system_owner(request, authorization)
    _require_role_manager(admin)
    rid = role_id.strip().lower()
    if rid in BUILTIN_ROLES:
        raise HTTPException(400, "Cannot delete a built-in role")
    result = await admin_role_defs_collection.delete_one({"id": rid})
    if result.deleted_count == 0:
        raise HTTPException(404, "Role not found")
    await log_admin_action(admin.get("email") or "", "role_definition_delete", target=rid)
    return {"message": "Role deleted"}


@admin_roles_router.get("/staff")
async def list_staff(
    request: Request,
    authorization: str | None = Header(default=None),
    q: Optional[str] = Query(default=None, max_length=200),
):
    await require_admin(request, authorization)
    query: dict = {
        "role": "owner",
        "$or": [
            {"is_admin": True},
            {"role_admin": True},
            {"admin_role": {"$exists": True, "$nin": [None, ""]}},
        ],
        "deleted_at": {"$exists": False},
    }
    if q and q.strip():
        term = q.strip()
        query["$and"] = [
            {
                "$or": [
                    {"email": {"$regex": term, "$options": "i"}},
                    {"full_name": {"$regex": term, "$options": "i"}},
                    {"name": {"$regex": term, "$options": "i"}},
                ]
            }
        ]

    roles = await _all_roles()
    staff = []
    async for u in users_collection.find(
        query,
        {
            "email": 1,
            "full_name": 1,
            "name": 1,
            "admin_role": 1,
            "admin_areas": 1,
            "admin_mfa_enabled": 1,
            "is_admin": 1,
            "suspended": 1,
            "last_login": 1,
            "owner_last_login": 1,
            "created_at": 1,
        },
    ).sort("email", 1):
        role_id = normalize_admin_role(u.get("admin_role"))
        areas = resolve_areas_for_role(
            role_id,
            u.get("admin_areas") if isinstance(u.get("admin_areas"), list) else None,
            roles.get(role_id),
        )
        staff.append(
            {
                "id": str(u["_id"]),
                "email": u.get("email"),
                "full_name": u.get("full_name") or u.get("name"),
                "admin_role": role_id,
                "admin_role_label": (roles.get(role_id) or {}).get("label") or role_id,
                "admin_areas": areas,
                "admin_mfa_enabled": bool(u.get("admin_mfa_enabled")),
                "is_admin": bool(u.get("is_admin")),
                "suspended": bool(u.get("suspended")),
                "last_login": u.get("last_login") or u.get("owner_last_login"),
                "created_at": u.get("created_at"),
            }
        )
    return {"staff": staff, "total": len(staff)}


@admin_roles_router.post("/staff")
async def invite_staff(
    payload: InviteStaffRequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    admin = await require_system_owner(request, authorization)
    _require_role_manager(admin)

    email = payload.email.lower().strip()
    roles = await _all_roles()
    role_id = normalize_admin_role(payload.admin_role)
    if role_id not in roles:
        raise HTTPException(400, f"Unknown role: {payload.admin_role}")

    areas = resolve_areas_for_role(role_id, payload.admin_areas, roles.get(role_id))
    now = datetime.utcnow()
    existing = await users_collection.find_one({"email": email})

    if existing and existing.get("role") == "nextkin":
        raise HTTPException(400, "Email already used by a next-of-kin account")

    if existing and existing.get("role") == "owner":
        await users_collection.update_one(
            {"_id": existing["_id"]},
            {
                "$set": {
                    "password": hash_password(payload.password),
                    "full_name": payload.full_name.strip(),
                    "name": payload.full_name.strip(),
                    "is_admin": True,
                    "role_admin": True,
                    "admin_role": role_id,
                    "admin_areas": areas,
                    "suspended": False,
                    "access_revoked": False,
                    "updated_at": now,
                },
                "$unset": {"deleted_at": ""},
            },
        )
        user_id = str(existing["_id"])
        action = "staff_updated"
    else:
        doc = {
            "email": email,
            "password": hash_password(payload.password),
            "full_name": payload.full_name.strip(),
            "name": payload.full_name.strip(),
            "role": "owner",
            "verified": True,
            "mfa_enabled": False,
            "is_admin": True,
            "role_admin": True,
            "admin_role": role_id,
            "admin_areas": areas,
            "admin_mfa_enabled": False,
            "billing": {
                **default_billing_fields(),
                "status": "complimentary",
                "plan": "complimentary",
                "comp": {
                    "enabled": True,
                    "kind": "lifetime",
                    "starts_at": now,
                    "ends_at": None,
                    "granted_by": admin.get("email"),
                    "granted_at": now,
                    "note": payload.note or "Admin staff account",
                    "reminders_sent": [],
                },
            },
            "created_at": now,
            "updated_at": now,
        }
        result = await users_collection.insert_one(doc)
        user_id = str(result.inserted_id)
        action = "staff_invited"

    await log_admin_action(
        admin.get("email") or "",
        action,
        target=email,
        meta={"admin_role": role_id, "admin_areas": areas},
    )
    return {
        "message": "Staff access person saved",
        "id": user_id,
        "email": email,
        "admin_role": role_id,
        "admin_areas": areas,
    }


@admin_roles_router.patch("/staff/{user_id}")
async def patch_staff(
    user_id: str,
    payload: PatchStaffRequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    admin = await require_system_owner(request, authorization)
    _require_role_manager(admin)

    try:
        oid = ObjectId(user_id)
    except (InvalidId, TypeError):
        raise HTTPException(400, "Invalid user id")

    user = await users_collection.find_one({"_id": oid, "role": "owner"})
    if not user:
        raise HTTPException(404, "Staff user not found")

    roles = await _all_roles()
    updates: dict = {"updated_at": datetime.utcnow()}

    if payload.full_name is not None:
        updates["full_name"] = payload.full_name.strip()
        updates["name"] = payload.full_name.strip()
    if payload.suspended is not None:
        updates["suspended"] = payload.suspended
    if payload.is_admin is not None:
        updates["is_admin"] = payload.is_admin
        updates["role_admin"] = payload.is_admin

    role_id = normalize_admin_role(
        payload.admin_role if payload.admin_role is not None else user.get("admin_role")
    )
    if payload.admin_role is not None:
        if role_id not in roles:
            raise HTTPException(400, f"Unknown role: {payload.admin_role}")
        updates["admin_role"] = role_id
        updates["is_admin"] = True
        updates["role_admin"] = True

    if payload.admin_areas is not None:
        updates["admin_areas"] = resolve_areas_for_role(
            role_id, payload.admin_areas, roles.get(role_id)
        )
    elif payload.admin_role is not None:
        # Reset areas to role defaults when role changes and areas not sent
        updates["admin_areas"] = resolve_areas_for_role(role_id, None, roles.get(role_id))

    await users_collection.update_one({"_id": oid}, {"$set": updates})
    await log_admin_action(
        admin.get("email") or "",
        "staff_patched",
        target=user.get("email"),
        meta={k: v for k, v in updates.items() if k != "updated_at"},
    )
    return {"message": "Staff updated"}
