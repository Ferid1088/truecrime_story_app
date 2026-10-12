"""End-to-end smoke check of local processes.

Run: python scripts/smoke.py http://127.0.0.1:8000 <ACCESS_TOKEN>
"""
import sys

import httpx


def main(base: str, token: str) -> int:
    fails: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))
        if not ok:
            fails.append(name)

    with httpx.Client(base_url=base, timeout=15) as c:
        r = c.get("/health")
        check("health is public", r.status_code == 200 and r.json().get("status") == "ok", r.text)
        check("health carries a request id", bool(r.headers.get("x-request-id")))
        check("nosniff header", r.headers.get("x-content-type-options") == "nosniff")
        r = c.get("/api/cases")
        check("API refuses anonymous requests", r.status_code == 401, str(r.status_code))
        r = c.post("/api/auth/login", json={"token": "definitely-wrong"})
        check("wrong token rejected", r.status_code == 401, str(r.status_code))
        r = c.get("/api/ops/summary", headers={"Authorization": f"Bearer {token}"})
        check("bearer token accepted", r.status_code == 200, str(r.status_code))
        r = c.post("/api/auth/login", json={"token": token})
        check("login sets an HttpOnly strict cookie",
              r.status_code == 200 and "httponly" in r.headers.get("set-cookie", "").lower()
              and "samesite=strict" in r.headers.get("set-cookie", "").lower(), r.headers.get("set-cookie", ""))
        check("cookie session works", c.get("/api/ops/summary").status_code == 200)
        r = c.get("/api/cases")
        check("data endpoint answers when signed in", r.status_code == 200, str(r.status_code))
    print("SMOKE: " + ("GREEN" if not fails else "RED - " + ", ".join(fails)))
    return 0 if not fails else 1


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    raise SystemExit(main(sys.argv[1].rstrip("/"), sys.argv[2]))
