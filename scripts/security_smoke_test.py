"""Quick security smoke tests — run while backend is on http://127.0.0.1:8000"""
import json
import sys
import httpx

BASE = "http://127.0.0.1:8000"
PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = ""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name} — {detail}")


def main() -> int:
    print("\n=== Orderly Affairs security smoke test ===\n")
    client = httpx.Client(base_url=BASE, timeout=30.0, follow_redirects=True)

    # 1. Health
    r = client.get("/")
    check("Health check", r.status_code == 200 and r.json().get("status") == "ok", r.text[:120])

    # 2. Session unauthenticated
    r = client.get("/auth/session")
    body = r.json()
    check(
        "Session without cookie -> authenticated=false",
        r.status_code == 200 and body.get("authenticated") is False,
        str(body),
    )

    # 3. Password reset — no CAPTCHA blocked
    r = client.post("/auth/request-password-reset", json={"email": "nobody@example.com"})
    check(
        "Password reset without CAPTCHA -> 400",
        r.status_code == 400,
        f"{r.status_code} {r.text[:200]}",
    )

    # 4. Password reset — dev bypass CAPTCHA + generic message
    r = client.post(
        "/auth/request-password-reset",
        json={"email": "nobody@example.com", "captcha_token": "dev-bypass"},
    )
    body = r.json()
    check(
        "Password reset with CAPTCHA -> 200 generic",
        r.status_code == 200 and "If an account exists" in body.get("message", ""),
        str(body),
    )

    # 5. NOK login — no CAPTCHA blocked
    r = client.post(
        "/auth/nextkin-login",
        json={"email": "nok@example.com", "master_password": "wrong"},
    )
    check(
        "NOK login without CAPTCHA -> 400",
        r.status_code == 400,
        f"{r.status_code} {r.text[:200]}",
    )

    # 6. NOK login — CAPTCHA + bad creds -> generic 401
    r = client.post(
        "/auth/nextkin-login",
        json={
            "email": "nok@example.com",
            "master_password": "wrong",
            "captcha_token": "dev-bypass",
        },
    )
    check(
        "NOK login bad creds -> 401",
        r.status_code == 401,
        f"{r.status_code} {r.text[:200]}",
    )

    # 7. Owner MRR removed
    r = client.get("/billing/mrr")
    check("Owner /billing/mrr removed -> 404", r.status_code == 404, str(r.status_code))

    # 8. my-nextkin without auth
    r = client.get("/auth/my-nextkin")
    check("my-nextkin without auth -> 401", r.status_code == 401, str(r.status_code))

    # 9. Login — no CAPTCHA blocked
    r = client.post(
        "/auth/login",
        json={"email": "owner@example.com", "password": "WrongPassword123!"},
    )
    check(
        "Login without CAPTCHA -> 400",
        r.status_code == 400,
        f"{r.status_code} {r.text[:200]}",
    )

    # 10. Login bad password — generic error (with CAPTCHA)
    r = client.post(
        "/auth/login",
        json={
            "email": "owner@example.com",
            "password": "WrongPassword123!",
            "captcha_token": "dev-bypass",
        },
    )
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    check(
        "Login bad creds -> generic message",
        r.status_code == 401 and body.get("detail") == "Invalid email or password",
        str(body),
    )

    # 11. resume-pending-signup — no CAPTCHA blocked
    r = client.post(
        "/auth/resume-pending-signup",
        json={"email": "nobody@example.com"},
    )
    check(
        "resume-pending-signup without CAPTCHA -> 400",
        r.status_code == 400,
        f"{r.status_code} {r.text[:200]}",
    )

    # 12. resume-pending-signup — no secret in response
    r = client.post(
        "/auth/resume-pending-signup",
        json={"email": "nobody@example.com", "captcha_token": "dev-bypass"},
    )
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    check(
        "resume-pending-signup never returns secret",
        "secret" not in body,
        str(body),
    )

    # 13. Refresh — Bearer fallback removed (cookie-only)
    r = client.post(
        "/auth/refresh-token",
        headers={"Authorization": "Bearer fake.jwt.token"},
    )
    check(
        "Refresh without cookie -> 401",
        r.status_code == 401,
        f"{r.status_code} {r.text[:200]}",
    )

    # 14. Optional: owner login with env credentials
    import os

    test_email = os.environ.get("OA_TEST_EMAIL", "").strip()
    test_password = os.environ.get("OA_TEST_PASSWORD", "").strip()
    if test_email and test_password:
        print("\n--- Owner session tests (OA_TEST_EMAIL set) ---")
        jar = httpx.Client(base_url=BASE, timeout=30.0, follow_redirects=True)
        r = jar.post(
            "/auth/login",
            json={
                "email": test_email,
                "password": test_password,
                "captcha_token": "dev-bypass",
            },
        )
        if r.status_code == 200 and r.json().get("mfa_required"):
            print("  SKIP  Owner has MFA — complete MFA in browser to test NOK create")
        elif r.status_code == 200:
            cookies = r.cookies
            r2 = jar.get("/auth/session", cookies=cookies)
            sess = r2.json()
            check("Owner login -> session authenticated", sess.get("authenticated") is True, str(sess))

            r3 = jar.get("/auth/my-nextkin", cookies=cookies)
            if r3.status_code == 200:
                items = r3.json()
                leaked = any("master_password" in (x or {}) for x in items)
                check("my-nextkin does NOT return master_password", not leaked, json.dumps(items)[:300])
                has_flag = all("has_master_password" in (x or {}) for x in items) if items else True
                check("my-nextkin includes has_master_password", has_flag, "")
            else:
                check("my-nextkin loads for owner", r3.status_code == 200, str(r3.status_code))

            r4 = jar.post("/auth/refresh-token", cookies=cookies)
            check("Refresh token rotation -> 200", r4.status_code == 200, str(r4.status_code))

            r5 = jar.post("/auth/owner-logout", cookies=cookies)
            check("Logout -> 200", r5.status_code == 200, str(r5.status_code))

            r6 = jar.get("/auth/session", cookies=jar.cookies)
            check("After logout -> unauthenticated", r6.json().get("authenticated") is False, str(r6.json()))
        else:
            print(f"  SKIP  Owner login failed ({r.status_code}) — set valid OA_TEST_EMAIL / OA_TEST_PASSWORD")
    else:
        print("\n  INFO  Set OA_TEST_EMAIL + OA_TEST_PASSWORD to test owner login / my-nextkin / refresh / logout")

    # 14. Reset password — no CAPTCHA blocked
    r = client.post(
        "/auth/reset-password",
        json={
            "email": "nobody@example.com",
            "otp": 123456,
            "new_password": "NewPassword123!",
        },
    )
    check(
        "Reset password without CAPTCHA -> 400",
        r.status_code == 400,
        f"{r.status_code} {r.text[:200]}",
    )

    print(f"\n=== Results: {PASS} passed, {FAIL} failed ===\n")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
