"""Every endpoint the web UI calls must exist in the backend, with that HTTP method.
A renamed or removed route then fails here instead of as a blank page."""
import re
from pathlib import Path

from app.main import app

API_TS = (Path(__file__).resolve().parents[1] / "frontend" / "lib" / "api.ts").read_text()


def _norm(path: str) -> str:
    path = path.split("?")[0]
    path = re.sub(r"\$\{[^}]*\}", "{}", path)          # frontend template parameters
    return re.sub(r"\{[^}/]*\}", "{}", path)           # backend path parameters


def backend_routes() -> set[tuple[str, str]]:
    out = set()
    for path, ops in app.openapi()["paths"].items():
        for method in ops:
            out.add((method.upper(), _norm(path)))
    return out


def _string_literal(src: str, i: int) -> tuple[str, int]:
    """Read the string/template literal starting at src[i]; ${...} may nest braces and strings."""
    quote = src[i]
    out, depth, j = [], 0, i + 1
    while j < len(src):
        ch = src[j]
        if depth == 0:
            if ch == quote:
                return "".join(out), j + 1
            if quote == "`" and src.startswith("${", j):
                depth, j = 1, j + 2
                out.append("${")
                continue
            out.append(ch)
        else:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    out.append("}")
                    j += 1
                    continue
        j += 1
    raise ValueError("unterminated literal")


HELPER_METHOD = {"post": "POST", "patch": "PATCH", "put": "PUT"}


def frontend_calls() -> list[tuple[str, str]]:
    """(method, path) for each request<>/post<>/patch<>/put<> call with a literal path in lib/api.ts."""
    calls = []
    for m in re.finditer(r"\b(request|post|patch|put)<", API_TS):
        helper = m.group(1)
        i, depth = m.end(), 1
        while depth:                                   # skip the (possibly nested) generic argument
            depth += {"<": 1, ">": -1}.get(API_TS[i], 0)
            i += 1
        if API_TS[i] != "(":
            continue
        i += 1
        while API_TS[i].isspace():
            i += 1
        if API_TS[i] not in "`\"'":
            continue                                   # the helper definitions pass `path` through
        raw, end = _string_literal(API_TS, i)
        if not raw.startswith("/api/"):
            continue
        path = raw
        while "${" in path:                            # a whole path segment becomes {}, query pieces vanish
            k = path.index("${")
            depth, j = 1, k + 2
            while depth:
                depth += {"{": 1, "}": -1}.get(path[j], 0)
                j += 1
            is_segment = k > 0 and path[k - 1] == "/" and (j == len(path) or path[j] in "/?")
            path = path[:k] + ("{}" if is_segment else "") + path[j:]
        if helper in HELPER_METHOD:
            method = HELPER_METHOD[helper]
        else:
            window = API_TS[end:end + 400]
            close = window.find(")")
            found = re.search(r"method:\s*[\"'](\w+)[\"']", window[: close if close != -1 else 400])
            method = (found.group(1) if found else "GET").upper()
        calls.append((method, _norm(path)))
    return calls


def test_the_parser_finds_a_realistic_number_of_calls():
    calls = frontend_calls()
    assert len(calls) > 100
    assert {m for m, _ in calls} >= {"GET", "POST", "PATCH", "PUT"}


def test_every_frontend_call_exists_in_the_backend():
    have = backend_routes()
    missing = sorted({c for c in frontend_calls() if c not in have})
    assert not missing, f"UI calls endpoints the API does not have: {missing}"
