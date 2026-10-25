
from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, jsonify
)
import sqlite3
import os
import ast
import subprocess
import tempfile
import time
import sys
from pathlib import Path
from functools import wraps

# --------------------------------------------------
# APPLICATION CONFIGURATION
# --------------------------------------------------

APP_DIR = Path(__file__).resolve().parent

# NOTE: SQLite in /tmp is temporary on Vercel.
# Use a persistent external database for production.
if os.environ.get("VERCEL"):
    DB_PATH = Path("/tmp/codemind.db")
else:
    DB_PATH = APP_DIR / "codemind.db"

app = Flask(__name__)

secret = os.environ.get("CODEMIND_SECRET")
if not secret:
    if os.environ.get("VERCEL"):
        raise RuntimeError(
            "Missing CODEMIND_SECRET environment variable."
        )
    secret = "local-development-only-secret"

app.secret_key = secret


# --------------------------------------------------
# DATABASE
# --------------------------------------------------

def db():
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                action TEXT NOT NULL,
                details TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute(
            """
            INSERT OR IGNORE INTO users (username, password)
            VALUES (?, ?)
            """,
            ("demo", "codemind123")
        )


# Initialize database when the application loads.
init_db()


# --------------------------------------------------
# ACTIVITY LOGGING
# --------------------------------------------------

def log_action(action, details=""):
    username = session.get("username")
    if not username:
        return

    with db() as conn:
        conn.execute(
            """
            INSERT INTO history (username, action, details)
            VALUES (?, ?, ?)
            """,
            (username, action, details[:500])
        )


# --------------------------------------------------
# AUTHENTICATION
# --------------------------------------------------

def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("username"):
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapped


@app.route("/")
def home():
    if session.get("username"):
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not username or not password:
            flash("Enter your username and password.", "error")
            return render_template("login.html"), 400

        try:
            with db() as conn:
                user = conn.execute(
                    """
                    SELECT id, username, password
                    FROM users
                    WHERE username = ? AND password = ?
                    """,
                    (username, password)
                ).fetchone()

            if user:
                session.clear()
                session["username"] = user["username"]
                log_action("Login", "Successful sign in")
                return redirect(url_for("dashboard"))

            flash(
                "Invalid username or password. Try demo / codemind123.",
                "error"
            )

        except sqlite3.Error:
            app.logger.exception("Database error during login")
            return "Login service temporarily unavailable.", 500

        except Exception:
            app.logger.exception("Unexpected error during login")
            return "An unexpected server error occurred.", 500

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# --------------------------------------------------
# DASHBOARD
# --------------------------------------------------

@app.route("/dashboard")
@login_required
def dashboard():
    with db() as conn:
        recent = conn.execute(
            """
            SELECT * FROM history
            WHERE username = ?
            ORDER BY id DESC LIMIT 6
            """,
            (session["username"],)
        ).fetchall()

        total = conn.execute(
            """
            SELECT COUNT(*) AS n FROM history
            WHERE username = ?
            """,
            (session["username"],)
        ).fetchone()["n"]

    return render_template(
        "dashboard.html",
        recent=recent,
        total=total
    )


# --------------------------------------------------
# PYTHON WORKSPACE
# --------------------------------------------------

@app.route("/workspace", methods=["GET", "POST"])
@login_required
def workspace():
    default_code = (
        'def greet(name):\n'
        '    return f"Hello, {name}!"\n\n'
        'print(greet("CodeMind"))'
    )

    code = request.form.get("code", default_code)
    output = ""
    error = ""
    elapsed = None

    if request.method == "POST":
        action = request.form.get("action", "run")

        if action == "run":
            temp_name = None

            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    suffix=".py",
                    delete=False,
                    encoding="utf-8"
                ) as file:
                    file.write(code)
                    temp_name = file.name

                start = time.perf_counter()

                result = subprocess.run(
                    [sys.executable, "-I", temp_name],
                    capture_output=True,
                    text=True,
                    timeout=3,
                    cwd=str(APP_DIR),
                    env={"PATH": os.environ.get("PATH", "")}
                )

                elapsed = round(time.perf_counter() - start, 4)
                output = result.stdout[:6000]
                error = result.stderr[:6000]

                if result.returncode and not error:
                    error = (
                        f"Program exited with code {result.returncode}"
                    )

            except subprocess.TimeoutExpired:
                error = "Execution timed out after 3 seconds."

            except Exception:
                app.logger.exception("Workspace execution failed")
                error = "Unable to execute the program."

            finally:
                if temp_name:
                    try:
                        os.unlink(temp_name)
                    except OSError:
                        pass

            log_action("Code execution", f"Runtime: {elapsed}s")

        elif action == "analyze":
            return redirect(url_for("analyzer", code=code))

    return render_template(
        "workspace.html",
        code=code,
        output=output,
        error=error,
        elapsed=elapsed
    )


# --------------------------------------------------
# CODE ANALYZER
# --------------------------------------------------

def analyze_python(code):
    issues = []

    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [{
            "severity": "Error",
            "line": exc.lineno or 1,
            "message": f"Syntax error: {exc.msg}"
        }], None

    lines = code.splitlines()

    if len(lines) > 200:
        issues.append({
            "severity": "Info",
            "line": 1,
            "message": (
                f"Large file: {len(lines)} lines. "
                "Consider splitting it into modules."
            )
        })

    for number, line in enumerate(lines, 1):
        stripped = line.strip()

        if len(line) > 100:
            issues.append({
                "severity": "Warning",
                "line": number,
                "message": "Line exceeds 100 characters."
            })

        if stripped.startswith("print("):
            issues.append({
                "severity": "Info",
                "line": number,
                "message": (
                    "Print statement found; use logging "
                    "for production applications."
                )
            })

        if "==" in stripped and "if " in stripped:
            if stripped.endswith("== True:"):
                issues.append({
                    "severity": "Suggestion",
                    "line": number,
                    "message": (
                        "Use the boolean expression directly "
                        "instead of '== True'."
                    )
                })

        if stripped == "except:":
            issues.append({
                "severity": "Warning",
                "line": number,
                "message": (
                    "Bare except catches every exception; "
                    "catch a specific exception."
                )
            })

    functions = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]

    classes = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
    ]

    imports = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]

    assigned_names = {
        node.id for node in ast.walk(tree)
        if isinstance(node, ast.Name)
        and isinstance(node.ctx, ast.Store)
    }

    loaded_names = {
        node.id for node in ast.walk(tree)
        if isinstance(node, ast.Name)
        and isinstance(node.ctx, ast.Load)
    }

    for name in sorted(assigned_names - loaded_names):
        if not name.startswith("_"):
            issues.append({
                "severity": "Suggestion",
                "line": 1,
                "message": (
                    f"'{name}' may be assigned but never read "
                    "(basic heuristic)."
                )
            })

    metrics = {
        "lines": len(lines),
        "functions": len(functions),
        "classes": len(classes),
        "imports": len(imports)
    }

    if not issues:
        issues.append({
            "severity": "Success",
            "line": 1,
            "message": "No issues found by the built-in checks."
        })

    return issues, metrics


@app.route("/analyzer", methods=["GET", "POST"])
@login_required
def analyzer():
    default_code = "def add(a, b):\n    return a + b\n"

    code = request.form.get(
        "code",
        request.args.get("code", default_code)
    )

    issues, metrics = analyze_python(code)

    if request.method == "POST":
        log_action("Code analysis", f"{len(issues)} findings")

    return render_template(
        "analyzer.html",
        code=code,
        issues=issues,
        metrics=metrics
    )


# --------------------------------------------------
# TEST CASE GENERATOR
# --------------------------------------------------

@app.route("/tests", methods=["GET", "POST"])
@login_required
def tests():
    default_code = "def add(a, b):\n    return a + b\n"

    code = request.form.get("code", default_code)
    result = None

    if request.method == "POST":
        function_name = request.form.get(
            "function_name", "add"
        ).strip()

        raw_cases = request.form.get(
            "cases",
            "1,2,3\n2,5,7\n-1,1,0"
        )

        try:
            namespace = {}

            # Demo-only execution. Not a secure public sandbox.
            safe_builtins = {
                "range": range,
                "len": len,
                "sum": sum,
                "min": min,
                "max": max,
                "abs": abs
            }

            exec(
                compile(code, "<user_code>", "exec"),
                {"__builtins__": safe_builtins},
                namespace
            )

            fn = namespace.get(function_name)

            if not callable(fn):
                raise ValueError(
                    f"Function '{function_name}' was not found."
                )

            rows = []

            for line in raw_cases.splitlines()[:30]:
                if not line.strip():
                    continue

                parts = [
                    part.strip()
                    for part in line.split(",")
                ]

                # Only allow simple numeric arguments.
                try:
                    args = [float(part) if "." in part else int(part)
                            for part in parts]
                except ValueError:
                    rows.append({
                        "input": line,
                        "output": "Invalid numeric test arguments.",
                        "status": "Error"
                    })
                    continue

                try:
                    value = fn(*args)
                    rows.append({
                        "input": line,
                        "output": repr(value),
                        "status": "Passed"
                    })
                except Exception as exc:
                    rows.append({
                        "input": line,
                        "output": str(exc),
                        "status": "Failed"
                    })

            result = rows
            log_action("Test generation", f"{len(rows)} test cases")

        except Exception as exc:
            result = [{
                "input": "—",
                "output": str(exc),
                "status": "Error"
            }]

    return render_template(
        "tests.html",
        code=code,
        result=result
    )


# --------------------------------------------------
# PROJECT HISTORY
# --------------------------------------------------

@app.route("/projects")
@login_required
def projects():
    with db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM history
            WHERE username = ?
            ORDER BY id DESC LIMIT 50
            """,
            (session["username"],)
        ).fetchall()

    return render_template("projects.html", history=rows)


# --------------------------------------------------
# HEALTH CHECK
# --------------------------------------------------

@app.route("/api/health")
def health():
    try:
        with db() as conn:
            conn.execute("SELECT 1").fetchone()

        return jsonify({
            "status": "ok",
            "app": "CodeMind AI",
            "database": "connected"
        })

    except sqlite3.Error:
        app.logger.exception("Health check database failure")
        return jsonify({
            "status": "error",
            "app": "CodeMind AI",
            "database": "unavailable"
        }), 500


# --------------------------------------------------
# LOCAL DEVELOPMENT
# --------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True, port=5000)
