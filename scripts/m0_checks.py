"""M0 environment checks: SQLCipher (encryption + FTS5) and Turbovec (API + behaviour).

Run:  uv run python scripts/m0_checks.py
"""

import inspect
import os
import tempfile


def check_sqlcipher() -> None:
    import sqlcipher3

    print("== SQLCipher ==")
    d = tempfile.mkdtemp()
    path = os.path.join(d, "t.db")
    key = os.urandom(32).hex()
    con = sqlcipher3.connect(path)
    con.execute(f"PRAGMA key = \"x'{key}'\"")
    print("cipher_version:", con.execute("PRAGMA cipher_version").fetchone()[0])
    print("sqlite_version:", con.execute("select sqlite_version()").fetchone()[0])
    opts = [r[0] for r in con.execute("PRAGMA compile_options")]
    print("FTS5 compiled in:", any("FTS5" in o for o in opts))
    con.execute("create virtual table f using fts5(content)")
    con.execute("insert into f(content) values ('port 8089 auth_middleware.py 0x80070005 error')")
    con.commit()
    hit = con.execute("select rowid, bm25(f) from f where f match '\"8089\"'").fetchall()
    print("FTS5 query hit:", hit)
    con.close()
    with open(path, "rb") as fh:
        raw = fh.read()
    head = raw[:16]
    print(
        "file header:", head[:16], "| plaintext SQLite header:", head.startswith(b"SQLite format 3")
    )
    print("demo string visible in raw file:", b"auth_middleware" in raw)
    bad = sqlcipher3.connect(path)
    try:
        bad.execute("select count(*) from sqlite_master").fetchone()
        print("open WITHOUT key: SUCCEEDED (bad)")
    except Exception as e:
        print("open WITHOUT key fails:", type(e).__name__, e)


def check_turbovec() -> None:
    import turbovec

    print("\n== Turbovec ==")
    print("module:", turbovec.__file__)
    print("public names:", [n for n in dir(turbovec) if not n.startswith("_")])
    for name in [n for n in dir(turbovec) if not n.startswith("_")]:
        obj = getattr(turbovec, name)
        if inspect.isclass(obj):
            print(f"\nclass {name}:")
            for m in [m for m in dir(obj) if not m.startswith("_")]:
                try:
                    sig = str(inspect.signature(getattr(obj, m)))
                except (TypeError, ValueError):
                    sig = "(?)"
                doc = (inspect.getdoc(getattr(obj, m)) or "").split("\n")[0]
                print(f"  {m}{sig}  {doc}")


if __name__ == "__main__":
    check_sqlcipher()
    check_turbovec()
