"""
KOHLI VPS PANEL - WHITE PURPLE ULTRA PREMIUM EDITION
Complete Flask Application with Persistent Storage
Owner: @xxLEGEND_KOHLI
"""
import os
import json
import time
import uuid
import shutil
import subprocess
import threading
import secrets
import tempfile
import urllib.parse
from collections import deque
from pathlib import Path
from functools import wraps
from flask import (
    Flask, request, redirect, url_for, session,
    render_template_string, jsonify, send_from_directory, send_file
)
from werkzeug.utils import secure_filename

# ============================================
#  INITIALIZATION
# ============================================
import logging
logging.basicConfig(level=logging.DEBUG)

APP_DIR = Path(__file__).parent.absolute()
DATA_DIR = APP_DIR / "data"
USERS_FILE = DATA_DIR / "users.json"
PRICING_FILE = DATA_DIR / "pricing.json"
FILES_ROOT = APP_DIR / "user_files"

for d in [DATA_DIR, FILES_ROOT]:
    d.mkdir(exist_ok=True)

OWNER_USER = os.environ.get("OWNER_USER", "KOHLI")
OWNER_PASS = os.environ.get("OWNER_PASS", "MODS")

DEFAULT_PRICING = {
    "currency": "₹",
    "contact": "TELEGRAM: @xxLEGEND_KOHLI",
    "plans": [
        {"name": "STARTER", "duration": "24 HOURS", "price": "49", "features": "1 FILE RUN, 512MB RAM"},
        {"name": "BASIC", "duration": "7 DAYS", "price": "199", "features": "MULTI-FILE UPLOAD, PIP/NPM"},
        {"name": "PRO", "duration": "30 DAYS", "price": "599", "features": "UNLIMITED MODULES, PRIORITY"},
        {"name": "PREMIUM", "duration": "LIFETIME", "price": "1999", "features": "ALL FEATURES, CUSTOM DOMAIN"},
    ]
}

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024

# ============================================
#  FILTERS
# ============================================
@app.template_filter('timestamp_to_date')
def timestamp_to_date(ts):
    if not ts:
        return "LIFETIME"
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
    except:
        return "INVALID"

# ============================================
#  STORAGE FUNCTIONS
# ============================================
_lock = threading.Lock()

def load_users():
    if not USERS_FILE.exists():
        return {}
    try:
        with open(USERS_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return {}

def save_users(users):
    with _lock:
        with open(USERS_FILE, 'w') as f:
            json.dump(users, f, indent=2)

def load_pricing():
    if not PRICING_FILE.exists():
        save_pricing(DEFAULT_PRICING)
        return DEFAULT_PRICING
    try:
        with open(PRICING_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return DEFAULT_PRICING

def save_pricing(pricing):
    with _lock:
        with open(PRICING_FILE, 'w') as f:
            json.dump(pricing, f, indent=2)

def user_dir(username):
    d = FILES_ROOT / username
    d.mkdir(parents=True, exist_ok=True)
    return d

# ============================================
#  AUTH DECORATORS
# ============================================
def is_owner():
    return session.get("role") == "owner"

def current_user():
    return session.get("username")

def require_owner(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not is_owner():
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper

def require_user(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        username = current_user()
        if not username or session.get("role") != "user":
            return redirect(url_for("login"))
        users = load_users()
        if username not in users:
            session.clear()
            return redirect(url_for("login"))
        if users[username].get("expires_at") and time.time() > users[username]["expires_at"]:
            del users[username]
            save_users(users)
            session.clear()
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper

# Permission-based administration. The owner always has every permission.
ADMIN_PERMISSIONS = {
    "manage_users": "Create, extend and delete user accounts",
    "manage_files": "Browse and edit assigned account files",
    "run_servers": "Start, stop and restart assigned processes",
    "manage_pricing": "Update public pricing plans",
}

def has_permission(permission):
    if is_owner():
        return True
    if session.get("role") != "admin":
        return False
    users = load_users()
    info = users.get(current_user(), {})
    return permission in info.get("permissions", [])

def require_permission(permission):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            username = current_user()
            if not username or (not is_owner() and session.get("role") != "admin") or not has_permission(permission):
                return redirect(url_for("login"))
            return f(*args, **kwargs)
        return wrapper
    return decorator

def require_account(permission=None):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if session.get("role") == "user":
                return f(*args, **kwargs)
            if (is_owner() or session.get("role") == "admin") and (permission is None or has_permission(permission)):
                return f(*args, **kwargs)
            return redirect(url_for("login"))
        return wrapper
    return decorator

def require_file_access(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if session.get("role") == "user":
            return f(*args, **kwargs)
        if (is_owner() or session.get("role") == "admin") and has_permission("manage_files"):
            return f(*args, **kwargs)
        return redirect(url_for("login"))
    return wrapper

def file_owner_from_request():
    # Users edit their own files. Admins may edit only the account named in the
    # request when they have manage_files; owners may select any account.
    if is_owner():
        return request.args.get("account") or request.form.get("account") or current_user()
    return current_user()

def safe_account_dir(account):
    if not account:
        return None
    account = secure_filename(account).upper()
    if account == OWNER_USER:
        return None
    users = load_users()
    if account not in users:
        return None
    return user_dir(account)

# ============================================
#  SYSTEM METRICS
# ============================================
_METRICS = {"total": 0, "idle": 0, "ts": 0, "cpu": 0.0}

def read_system_metrics():
    try:
        parts = Path("/proc/stat").read_text().splitlines()[0].split()
        values = [int(v) for v in parts[1:]
        ]
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        total = sum(values)
        prev_total, prev_idle = _METRICS["total"], _METRICS["idle"]
        _METRICS.update(total=total, idle=idle, ts=time.time())
        if prev_total:
            dt, di = total - prev_total, idle - prev_idle
            _METRICS["cpu"] = round(max(0, min(100, 100 * (dt - di) / max(dt, 1))), 1)
        mem = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, value = line.split(":", 1)
            mem[key] = int(value.strip().split()[0])
        total_mb = mem.get("MemTotal", 0) / 1024
        available_mb = mem.get("MemAvailable", mem.get("MemFree", 0)) / 1024
        used_mb = max(0, total_mb - available_mb)
        disk = shutil.disk_usage(APP_DIR)
        return {"cpu": _METRICS["cpu"], "ram": round(used_mb / max(total_mb, 1) * 100, 1),
                "ram_used": round(used_mb), "ram_total": round(total_mb),
                "disk": round((disk.used / max(disk.total, 1)) * 100, 1),
                "uptime": round(float(Path("/proc/uptime").read_text().split()[0]))}
    except Exception:
        return {"cpu": 0, "ram": 0, "ram_used": 0, "ram_total": 0, "disk": 0, "uptime": 0}

# ============================================
#  PROCESS MANAGER
# ============================================
PROCS = {}

def start_process(username, filename):
    stop_process(username)
    udir = user_dir(username)
    fpath = udir / filename
    if not fpath.exists():
        return False, "FILE NOT FOUND"
    
    ext = fpath.suffix.lower()
    if ext == ".py":
        cmd = ["python", "-u", str(fpath)]
    elif ext in (".js", ".mjs", ".cjs"):
        cmd = ["node", str(fpath)]
    elif ext == ".sh":
        cmd = ["bash", str(fpath)]
    else:
        return False, f"UNSUPPORTED FILE TYPE: {ext}"
    
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(udir),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            bufsize=1
        )
    except FileNotFoundError:
        return False, "RUNTIME NOT INSTALLED"
    
    logs = deque(maxlen=2000)
    logs.append(f"[START] {' '.join(cmd)}")
    PROCS[username] = {"proc": proc, "logs": logs, "file": filename}
    
    def reader():
        try:
            for line in iter(proc.stdout.readline, b""):
                try:
                    txt = line.decode("utf-8", errors="replace").rstrip()
                except:
                    txt = str(line)
                logs.append(f"[{time.strftime('%H:%M:%S')}] {txt}")
        except Exception as e:
            logs.append(f"[ERROR] {e}")
        finally:
            logs.append(f"[EXIT] PROCESS ENDED WITH CODE {proc.poll()}")
    
    threading.Thread(target=reader, daemon=True).start()
    return True, "STARTED"

def stop_process(username):
    info = PROCS.get(username)
    if not info:
        return False
    proc = info["proc"]
    if proc.poll() is None:
        try:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        except:
            pass
        info["logs"].append("[STOP] PROCESS TERMINATED")
    return True

def is_running(username):
    info = PROCS.get(username)
    return bool(info and info["proc"].poll() is None)

def get_logs(username):
    info = PROCS.get(username)
    return list(info["logs"]) if info else []

# ============================================
#  INSTALL MODULE
# ============================================
INSTALL_LOGS = {}

def run_install(username, command):
    parts = command.strip().split()
    if not parts:
        return False, "EMPTY COMMAND"
    if parts[0] not in ("pip", "pip3", "npm"):
        return False, "ONLY 'PIP INSTALL' OR 'NPM INSTALL' ALLOWED"
    if len(parts) < 3 or parts[1] != "install":
        return False, "FORMAT: PIP INSTALL <MODULE> OR NPM INSTALL <MODULE>"
    if any(c in command for c in [";", "&", "|", "`", "$(", ">"]):
        return False, "INVALID CHARACTERS"
    
    logs = INSTALL_LOGS.setdefault(username, deque(maxlen=1000))
    logs.append(f"[INSTALL] $ {command}")
    cwd = str(user_dir(username))
    
    def worker():
        try:
            proc = subprocess.Popen(parts, cwd=cwd,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            for line in iter(proc.stdout.readline, b""):
                try:
                    txt = line.decode("utf-8", errors="replace").rstrip()
                except:
                    txt = str(line)
                logs.append(txt)
            proc.wait()
            logs.append(f"[INSTALL] FINISHED WITH CODE {proc.returncode}")
        except Exception as e:
            logs.append(f"[INSTALL-ERROR] {e}")
    
    threading.Thread(target=worker, daemon=True).start()
    return True, "INSTALLING..."

def get_install_logs(username):
    return list(INSTALL_LOGS.get(username, []))

# ============================================
#  ROUTES
# ============================================
@app.route("/")
def home():
    if is_owner():
        return redirect(url_for("owner_dashboard"))
    if current_user():
        return redirect(url_for("user_dashboard"))
    return render_template_string(HTML_LANDING, pricing=load_pricing())

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip().upper()
        password = request.form.get("password", "")
        
        if username == OWNER_USER and password == OWNER_PASS:
            session.clear()
            session["role"] = "owner"
            session["username"] = username
            return redirect(url_for("owner_dashboard"))
        
        users = load_users()
        if username in users and users[username]["password"] == password:
            if users[username].get("expires_at") and time.time() > users[username]["expires_at"]:
                error = "ACCOUNT EXPIRED"
            else:
                session.clear()
                session["role"] = users[username].get("role", "user")
                session["username"] = username
                if session["role"] == "admin":
                    return redirect(url_for("owner_dashboard")) if "manage_users" in users[username].get("permissions", []) else redirect(url_for("user_dashboard"))
                return redirect(url_for("user_dashboard"))
        else:
            error = "INVALID CREDENTIALS"
    
    return render_template_string(HTML_LOGIN, error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))

@app.route("/auto/<token>")
def auto_login(token):
    users = load_users()
    for username, info in users.items():
        if info.get("token") == token:
            if info.get("expires_at") and time.time() > info["expires_at"]:
                return "ACCOUNT EXPIRED", 403
            session.clear()
            session["role"] = "user"
            session["username"] = username
            return redirect(url_for("user_dashboard"))
    return "INVALID LINK", 404

@app.route("/download/<filename>")
@require_account("manage_files")
def download_file(filename):
    username = current_user()
    filename = secure_filename(filename)
    udir = user_dir(username)
    fpath = udir / filename
    if fpath.exists() and fpath.is_file():
        return send_file(fpath, as_attachment=True, download_name=filename)
    return "FILE NOT FOUND", 404

# ============================================
#  OWNER ROUTES
# ============================================
@app.route("/owner")
@require_permission("manage_users")
def owner_dashboard():
    users = load_users()
    pricing = load_pricing()
    now = time.time()
    changed = False
    for username in list(users.keys()):
        if users[username].get("expires_at") and now > users[username]["expires_at"]:
            del users[username]
            stop_process(username)
            changed = True
    if changed:
        save_users(users)
    
    return render_template_string(
        HTML_OWNER,
        users=users,
        pricing=pricing,
        now=now,
        base_url=request.host_url.rstrip("/"),
        time=time,
        admins={u: info for u, info in users.items() if info.get("role") == "admin"},
        permissions=ADMIN_PERMISSIONS
    )

@app.route("/owner/create", methods=["POST"])
@require_permission("manage_users")
def owner_create():
    username = request.form.get("username", "").strip().upper()
    password = request.form.get("password", "").strip()
    try:
        days = float(request.form.get("days", "7"))
    except:
        days = 7
    
    if not username or not password or username == OWNER_USER:
        return redirect(url_for("owner_dashboard"))
    
    users = load_users()
    users[username] = {
        "password": password,
        "created_at": time.time(),
        "expires_at": time.time() + days * 86400 if days > 0 else 0,
        "token": secrets.token_urlsafe(16),
        "role": "user",
        "permissions": []
    }
    save_users(users)
    user_dir(username)
    return redirect(url_for("owner_dashboard"))

@app.route("/owner/delete/<username>", methods=["POST"])
@require_permission("manage_users")
def owner_delete(username):
    users = load_users()
    if username in users:
        stop_process(username)
        del users[username]
        save_users(users)
        shutil.rmtree(FILES_ROOT / username, ignore_errors=True)
    return redirect(url_for("owner_dashboard"))

@app.route("/owner/extend/<username>", methods=["POST"])
@require_permission("manage_users")
def owner_extend(username):
    try:
        days = float(request.form.get("days", "7"))
    except:
        days = 7
    
    users = load_users()
    if username in users:
        base = max(users[username].get("expires_at") or time.time(), time.time())
        users[username]["expires_at"] = base + days * 86400
        save_users(users)
    return redirect(url_for("owner_dashboard"))

@app.route("/owner/admin/create", methods=["POST"])
@require_owner
def owner_admin_create():
    username = request.form.get("username", "").strip().upper()
    password = request.form.get("password", "").strip()
    permissions = [p for p in request.form.getlist("permissions") if p in ADMIN_PERMISSIONS]
    if not username or not password or username in (OWNER_USER, ""):
        return redirect(url_for("owner_dashboard"))
    users = load_users()
    users[username] = {
        "password": password, "created_at": time.time(), "expires_at": 0,
        "token": secrets.token_urlsafe(16), "role": "admin", "permissions": permissions
    }
    save_users(users)
    user_dir(username)
    return redirect(url_for("owner_dashboard"))

@app.route("/owner/admin/delete/<username>", methods=["POST"])
@require_owner
def owner_admin_delete(username):
    users = load_users()
    if username in users and users[username].get("role") == "admin":
        stop_process(username); users.pop(username, None); save_users(users)
        shutil.rmtree(FILES_ROOT / username, ignore_errors=True)
    return redirect(url_for("owner_dashboard"))

@app.route("/owner/admin/permissions/<username>", methods=["POST"])
@require_owner
def owner_admin_permissions(username):
    users = load_users()
    if username in users and users[username].get("role") == "admin":
        users[username]["permissions"] = [p for p in request.form.getlist("permissions") if p in ADMIN_PERMISSIONS]
        save_users(users)
    return redirect(url_for("owner_dashboard"))

@app.route("/owner/pricing", methods=["POST"])
@require_owner
def owner_pricing():
    try:
        pricing = load_pricing()
        pricing["currency"] = request.form.get("currency", "₹").strip() or "₹"
        pricing["contact"] = request.form.get("contact", "").strip()
        plans = []
        names = request.form.getlist("p_name")
        durs = request.form.getlist("p_duration")
        prices = request.form.getlist("p_price")
        feats = request.form.getlist("p_features")
        for i in range(len(names)):
            if not names[i].strip():
                continue
            plans.append({
                "name": names[i].strip().upper(),
                "duration": durs[i].strip().upper() if i < len(durs) else "",
                "price": prices[i].strip() if i < len(prices) else "0",
                "features": feats[i].strip() if i < len(feats) else "",
            })
        pricing["plans"] = plans
        save_pricing(pricing)
        return redirect(url_for("owner_dashboard"))
    except Exception as e:
        return f"ERROR: {e}", 500

# ============================================
#  USER ROUTES
# ============================================
@app.route("/dashboard")
@require_account()
def user_dashboard():
    username = current_user()
    users = load_users()
    info = users.get(username, {})
    udir = user_dir(username)
    files = sorted([f.name for f in udir.iterdir() if f.is_file()])
    pricing = load_pricing()
    
    return render_template_string(
        HTML_USER,
        username=username,
        info=info,
        files=files,
        running=is_running(username),
        running_file=PROCS.get(username, {}).get("file") if is_running(username) else None,
        expires_at=info.get("expires_at", 0),
        now=time.time(),
        pricing=pricing
    )

@app.route("/upload", methods=["POST"])
@require_account("manage_files")
def upload():
    username = current_user()
    udir = user_dir(username)
    files = request.files.getlist("files")
    
    for f in files:
        if f and f.filename:
            name = secure_filename(f.filename)
            if name:
                f.save(udir / name)
    
    return redirect(url_for("user_dashboard"))

@app.route("/file/delete/<name>", methods=["POST"])
@require_account("manage_files")
def file_delete(name):
    username = current_user()
    name = secure_filename(name)
    p = user_dir(username) / name
    if p.exists() and p.is_file():
        p.unlink()
    return redirect(url_for("user_dashboard"))

@app.route("/file/view/<name>")
@require_account("manage_files")
def file_view(name):
    username = current_user()
    name = secure_filename(name)
    return send_from_directory(user_dir(username), name, as_attachment=False)

@app.route("/server/start", methods=["POST"])
@require_account("run_servers")
def server_start():
    username = current_user()
    filename = secure_filename(request.form.get("file", ""))
    ok, msg = start_process(username, filename)
    return jsonify({"ok": ok, "msg": msg})

@app.route("/server/stop", methods=["POST"])
@require_account("run_servers")
def server_stop():
    username = current_user()
    stop_process(username)
    return jsonify({"ok": True})

@app.route("/server/restart", methods=["POST"])
@require_account("run_servers")
def server_restart():
    username = current_user()
    info = PROCS.get(username)
    filename = info["file"] if info else secure_filename(request.form.get("file", ""))
    if not filename:
        return jsonify({"ok": False, "msg": "NO FILE"})
    stop_process(username)
    time.sleep(0.3)
    ok, msg = start_process(username, filename)
    return jsonify({"ok": ok, "msg": msg})

@app.route("/server/delete", methods=["POST"])
@require_account("run_servers")
def server_delete():
    username = current_user()
    stop_process(username)
    PROCS.pop(username, None)
    return jsonify({"ok": True})

@app.route("/logs")
@require_account("run_servers")
def logs_api():
    username = current_user()
    return jsonify({
        "running": is_running(username),
        "file": PROCS.get(username, {}).get("file"),
        "logs": get_logs(username),
        "install": get_install_logs(username)
    })

@app.route("/install", methods=["POST"])
@require_account("run_servers")
def install():
    username = current_user()
    cmd = request.form.get("command", "").strip()
    ok, msg = run_install(username, cmd)
    return jsonify({"ok": ok, "msg": msg})

@app.route("/file/read/<name>")
@require_account("manage_files")
def file_read(name):
    account = current_user()
    path = safe_account_dir(account)
    filename = secure_filename(name)
    target = path / filename if path else None
    if not target or not target.is_file():
        return jsonify({"ok": False, "msg": "FILE NOT FOUND"}), 404
    size = target.stat().st_size
    if size > 5 * 1024 * 1024:
        return jsonify({"ok": False, "msg": "FILE TOO LARGE TO EDIT ONLINE", "size": size}), 413
    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return jsonify({"ok": False, "msg": "BINARY FILE CANNOT BE EDITED ONLINE", "size": size}), 415
    return jsonify({"ok": True, "name": filename, "size": size, "large": size > 120 * 1024, "content": content})

@app.route("/file/save/<name>", methods=["POST"])
@require_account("manage_files")
def file_save(name):
    account = current_user()
    path = safe_account_dir(account)
    filename = secure_filename(name)
    target = path / filename if path else None
    content = request.get_data(as_text=True)
    if not target or not target.is_file():
        return jsonify({"ok": False, "msg": "FILE NOT FOUND"}), 404
    if len(content.encode("utf-8")) > 5 * 1024 * 1024:
        return jsonify({"ok": False, "msg": "MAX ONLINE EDIT SIZE IS 5MB"}), 413
    try:
        target.write_text(content, encoding="utf-8")
        return jsonify({"ok": True, "msg": "FILE SAVED", "size": target.stat().st_size})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500

@app.route("/metrics")
@require_account()
def metrics_api():
    return jsonify(read_system_metrics())

@app.route("/github/import", methods=["POST"])
@require_account("manage_files")
def github_import():
    repo_url = request.form.get("repo_url", "").strip()
    branch = request.form.get("branch", "").strip()
    parsed = urllib.parse.urlparse(repo_url)
    if parsed.scheme != "https" or parsed.netloc.lower() not in ("github.com", "www.github.com"):
        return jsonify({"ok": False, "msg": "ONLY PUBLIC HTTPS GITHUB URLS ARE ALLOWED"}), 400
    if not parsed.path.strip("/") or parsed.path.count("/") < 1:
        return jsonify({"ok": False, "msg": "INVALID GITHUB REPOSITORY URL"}), 400
    account = current_user()
    target = safe_account_dir(account)
    if not target:
        return jsonify({"ok": False, "msg": "ACCOUNT DIRECTORY NOT FOUND"}), 404
    with tempfile.TemporaryDirectory(prefix="kohli-github-") as tmp:
        cmd = ["git", "clone", "--depth", "1"]
        if branch:
            if any(c in branch for c in " ;&|`$><"):
                return jsonify({"ok": False, "msg": "INVALID BRANCH"}), 400
            cmd += ["--branch", branch]
        cmd += [repo_url, tmp + "/repo"]
        try:
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            return jsonify({"ok": False, "msg": "IMPORT TIMED OUT AFTER 120 SECONDS"}), 504
        if result.returncode != 0:
            return jsonify({"ok": False, "msg": result.stdout[-600:] or "GITHUB IMPORT FAILED"}), 400
        imported = 0
        for src in Path(tmp + "/repo").rglob("*"):
            if ".git" in src.parts or not src.is_file():
                continue
            rel = src.relative_to(tmp + "/repo")
            dest = target / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.stat().st_size if dest.exists() else 0:
                pass
            shutil.copy2(src, dest)
            imported += 1
    return jsonify({"ok": True, "msg": f"IMPORTED {imported} FILES FROM GITHUB"})

@app.route("/healthz")
def health():
    return "OK"

# ============================================
#  HTML TEMPLATES - WHITE PURPLE THEME
# ============================================
HTML_LANDING = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>KOHLI VPS — ULTRA PREMIUM</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{--primary:#22D3EE;--secondary:#2563EB;--accent:#7DD3FC;--light:#0B1220;--dark:#E2E8F0;--card:rgba(15,23,42,0.94);--brd:rgba(148,163,184,0.18);--gold:#8B5CF6;--txt:#E2E8F0;--mt:#94A3B8}
body{font-family:'Inter',sans-serif;background:linear-gradient(135deg,#08111f 0%,#0f172a 55%,#111827 100%);color:var(--txt);min-height:100vh;overflow-x:hidden;letter-spacing:.2px}
canvas#bg{position:fixed;inset:0;z-index:0;opacity:0.5;pointer-events:none}
.mesh{position:fixed;inset:0;z-index:0;pointer-events:none;background:radial-gradient(ellipse 70% 55% at 80% -10%,rgba(34,211,238,0.08),transparent),radial-gradient(ellipse 60% 50% at -10% 90%,rgba(37,99,235,0.08),transparent)}
.wrap{position:relative;z-index:1;max-width:1200px;margin:0 auto;padding:0 24px}
.glass-nav{position:sticky;top:0;z-index:50;backdrop-filter:blur(28px);background:rgba(15,23,42,0.92);border-bottom:2px solid var(--primary);padding:0.8rem 2rem;display:flex;align-items:center;justify-content:center;box-shadow:0 4px 20px rgba(34,211,238,0.08)}
.brand{display:flex;align-items:center;gap:12px}
.brand-icon{width:44px;height:44px;border-radius:12px;background:linear-gradient(135deg,var(--primary),var(--secondary));display:flex;align-items:center;justify-content:center;font-size:20px;font-weight:900;color:#fff;box-shadow:0 8px 30px rgba(34,211,238,0.35);font-family:'Inter',sans-serif}
.brand-text{font-size:22px;font-weight:900;background:linear-gradient(135deg,var(--primary),var(--gold));-webkit-background-clip:text;-webkit-text-fill-color:transparent;font-family:'Inter',sans-serif;letter-spacing:3px}
.hero{min-height:80vh;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;padding:40px 20px}
.eyebrow{display:inline-flex;align-items:center;gap:10px;padding:8px 24px;background:rgba(34,211,238,0.08);border:1px solid rgba(34,211,238,0.2);border-radius:50px;margin-bottom:30px}
.eyebrow .dot{width:8px;height:8px;border-radius:50%;background:var(--primary);animation:pulse 2s infinite;box-shadow:0 0 10px var(--primary)}
.eyebrow span{font-size:11px;font-weight:700;letter-spacing:4px;;color:var(--primary);font-family:'Inter',sans-serif}
h1{font-size:clamp(36px,7vw,68px);font-weight:800;line-height:1.05;letter-spacing:-1px;margin-bottom:18px;font-family:'Inter',sans-serif;color:var(--dark)}
h1 .highlight{background:linear-gradient(135deg,var(--primary),var(--accent),var(--gold));background-size:300% 300%;-webkit-background-clip:text;-webkit-text-fill-color:transparent;animation:gradient 4s ease infinite}
.sub{font-size:16px;color:var(--mt);max-width:560px;margin:0 auto 30px;line-height:1.8;font-weight:500;letter-spacing:2px}
.btn-main{padding:16px 40px;border-radius:8px;background:linear-gradient(135deg,var(--primary),var(--secondary));color:#fff;text-decoration:none;font-weight:700;font-size:13px;letter-spacing:2px;;transition:0.3s;box-shadow:0 8px 30px rgba(34,211,238,0.3);font-family:'Inter',sans-serif;display:inline-block}
.btn-main:hover{transform:translateY(-3px) scale(1.05);box-shadow:0 16px 48px rgba(34,211,238,0.45);color:#fff}
.pricing-section{width:100%;max-width:1000px;margin:20px auto 30px}
.pricing-title{font-family:'Inter',sans-serif;font-size:18px;font-weight:700;letter-spacing:4px;color:var(--primary);margin-bottom:16px;text-align:center}
.pricing-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:14px}
.plan-card{background:var(--card);border:2px solid var(--brd);border-radius:12px;padding:18px 16px;text-align:center;transition:0.3s;backdrop-filter:blur(10px);box-shadow:0 4px 16px rgba(34,211,238,0.05)}
.plan-card:hover{border-color:var(--primary);transform:translateY(-4px);box-shadow:0 16px 40px rgba(34,211,238,0.15)}
.plan-card.hot{border-color:var(--primary);background:linear-gradient(135deg,rgba(34,211,238,0.05),rgba(125,211,252,0.08))}
.plan-card .hot-tag{font-size:8px;color:var(--primary);letter-spacing:2px;font-family:'Inter',sans-serif;margin-bottom:4px;font-weight:700}
.plan-card .pname{font-family:'Inter',sans-serif;font-size:12px;font-weight:700;letter-spacing:2px;color:var(--primary)}
.plan-card .pprice{font-family:'Inter',sans-serif;font-size:26px;font-weight:900;color:var(--dark);margin:4px 0}
.plan-card .pdur{font-size:10px;color:var(--mt);letter-spacing:2px}
.plan-card .pfeat{font-size:9px;color:var(--mt);letter-spacing:1px;margin-top:4px}
.contact-bar{padding:10px 20px;background:rgba(34,211,238,0.04);border:2px solid rgba(34,211,238,0.1);border-radius:10px;margin-top:14px;text-align:center;font-size:12px;color:var(--mt);letter-spacing:2px}
.contact-bar strong{color:var(--primary)}
.features{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:16px;width:100%;max-width:1000px;margin-top:20px}
.feature{background:var(--card);border:1px solid var(--brd);border-radius:12px;padding:24px 18px;text-align:center;backdrop-filter:blur(20px);transition:0.3s;box-shadow:0 4px 16px rgba(34,211,238,0.05)}
.feature:hover{border-color:var(--primary);transform:translateY(-4px);box-shadow:0 12px 30px rgba(34,211,238,0.12)}.feature-strip{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;width:100%;max-width:1000px;margin:18px auto 0;padding:14px 16px;background:rgba(15,23,42,.72);border:1px solid rgba(34,211,238,.18);border-radius:12px;text-align:left}.feature-strip div{display:flex;flex-direction:column;gap:5px}.feature-strip strong{font-size:10px;color:var(--primary);letter-spacing:1px}.feature-strip span{font-size:10px;color:var(--mt);letter-spacing:.2px}@media(max-width:650px){.feature-strip{grid-template-columns:1fr}}
.feature i{font-size:32px;color:var(--primary);margin-bottom:10px}
.feature h3{font-size:13px;font-weight:700;letter-spacing:2px;;margin-bottom:4px;font-family:'Inter',sans-serif;color:var(--dark)}
.feature p{font-size:11px;color:var(--mt);letter-spacing:1px}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.3}}
@keyframes gradient{0%,100%{background-position:0% 50%}50%{background-position:100% 50%}}
@media(max-width:768px){.glass-nav{padding:0.8rem 1rem}.wrap{padding:0 16px}h1{font-size:clamp(30px,7vw,50px)}.pricing-grid{grid-template-columns:1fr 1fr}}
@media(max-width:480px){.pricing-grid{grid-template-columns:1fr}}
</style>
</head>
<body>
<canvas id="bg"></canvas>
<div class="mesh"></div>
<nav class="glass-nav">
<div class="brand"><div class="brand-icon">NX</div><span class="brand-text">KOHLI</span></div>
</nav>
<div class="wrap">
<section class="hero">
<div class="eyebrow"><div class="dot"></div><span>NEXT-GEN VPS INFRASTRUCTURE</span></div>
<h1>DEPLOY &amp; RUN<br><span class="highlight">YOUR APPS</span></h1>
<p class="sub">UPLOAD, MANAGE &amp; MONITOR PYTHON, NODE.JS, AND SHELL SCRIPTS WITH REAL-TIME LOGS</p>
<a href="/login" class="btn-main"><i class="fas fa-arrow-right"></i> LAUNCH PANEL</a>
<div class="pricing-section">
<div class="pricing-title"><i class="fas fa-tags"></i> PRICING PLANS</div>
<div class="pricing-grid">
{% for plan in pricing.plans %}
<div class="plan-card {% if loop.index == 3 %}hot{% endif %}">
{% if loop.index == 3 %}<div class="hot-tag"><i class="fas fa-star"></i> POPULAR</div>{% endif %}
<div class="pname">{{ plan.name }}</div>
<div class="pprice">{{ pricing.currency }}{{ plan.price }}</div>
<div class="pdur">{{ plan.duration }}</div>
<div class="pfeat">{{ plan.features }}</div>
</div>
{% endfor %}
</div>
<div class="contact-bar"><i class="fas fa-headset"></i> CONTACT: <strong>{{ pricing.contact }}</strong></div>
</div>
<div class="features">
<div class="feature"><i class="fas fa-server"></i><h3>99.9% UPTIME</h3><p>ENTERPRISE-GRADE</p></div>
<div class="feature"><i class="fas fa-bolt"></i><h3>INSTANT DEPLOY</h3><p>UNDER 1 SECOND</p></div>
<div class="feature"><i class="fas fa-upload"></i><h3>200MB UPLOAD</h3><p>LARGE FILES</p></div>
<div class="feature"><i class="fas fa-code"></i><h3>5+ LANGUAGES</h3><p>PYTHON, NODE, SHELL</p></div>
</div>
<div class="feature-strip"><div><strong><i class="fab fa-github"></i> GITHUB IMPORT</strong><span>Pull a public repo straight into your workspace</span></div><div><strong><i class="fas fa-chart-line"></i> LIVE METRICS</strong><span>Track CPU, RAM and disk usage in real time</span></div><div><strong><i class="fas fa-folder-open"></i> FILE MANAGER</strong><span>Edit, deploy and manage files from one panel</span></div></div>
</section>
</div>
<script>
const c=document.getElementById('bg'),ctx=c.getContext('2d');
let W,H,p=[];
function resize(){W=c.width=innerWidth;H=c.height=innerHeight}
class P{constructor(){this.reset()}reset(){this.x=Math.random()*W;this.y=Math.random()*H;this.vx=(Math.random()-.5)*0.3;this.vy=(Math.random()-.5)*0.3;this.r=Math.random()*1.6+0.4;this.a=Math.random()*0.25+0.06;this.col='34,211,238'}update(){this.x+=this.vx;this.y+=this.vy;if(this.x<0||this.x>W||this.y<0||this.y>H)this.reset()}draw(){ctx.beginPath();ctx.arc(this.x,this.y,this.r,0,Math.PI*2);ctx.fillStyle=`rgba(${this.col},${this.a})`;ctx.fill()}}
function init(){p=[];for(let i=0;i<80;i++)p.push(new P())}
function loop(){ctx.clearRect(0,0,W,H);p.forEach(d=>{d.update();d.draw()});requestAnimationFrame(loop)}
window.addEventListener('resize',()=>{resize();init()});resize();init();loop();
</script>
</body>
</html>"""

HTML_LOGIN = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>KOHLI VPS — SECURE ACCESS</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{--primary:#22D3EE;--secondary:#2563EB;--accent:#7DD3FC;--light:#0B1220;--dark:#E2E8F0;--card:rgba(15,23,42,0.96);--brd:rgba(148,163,184,0.18);--mt:#94A3B8}
body{font-family:'Inter',sans-serif;background:linear-gradient(135deg,#08111f 0%,#0f172a 55%,#111827 100%);color:var(--dark);min-height:100vh;display:flex;align-items:center;justify-content:center;overflow:hidden;letter-spacing:.2px}
canvas#bg{position:fixed;inset:0;z-index:0;opacity:0.5;pointer-events:none}
.orb{position:fixed;border-radius:50%;filter:blur(120px);pointer-events:none;z-index:0}
.o1{width:400px;height:400px;top:-150px;right:-100px;background:rgba(34,211,238,0.15)}
.o2{width:400px;height:400px;bottom:-150px;left:-100px;background:rgba(37,99,235,0.12)}
.box{position:relative;z-index:1;width:100%;max-width:420px;margin:0 20px;animation:rise 0.6s ease}
.box-inner{background:var(--card);backdrop-filter:blur(36px);border-radius:16px;padding:48px 36px 40px;border:2px solid var(--brd);box-shadow:0 32px 80px rgba(34,211,238,0.15)}
.logo{text-align:center;margin-bottom:36px}
.logo-icon{width:60px;height:60px;border-radius:14px;background:linear-gradient(135deg,var(--primary),var(--secondary));display:inline-flex;align-items:center;justify-content:center;font-size:26px;font-weight:900;color:#fff;box-shadow:0 12px 40px rgba(34,211,238,0.35);margin-bottom:14px;font-family:'Inter',sans-serif}
.logo-text{font-size:28px;font-weight:900;background:linear-gradient(135deg,var(--primary),var(--accent));-webkit-background-clip:text;-webkit-text-fill-color:transparent;font-family:'Inter',sans-serif;letter-spacing:3px}
.logo-sub{font-size:11px;color:var(--mt);letter-spacing:4px;;margin-top:4px}
.error{background:rgba(34,211,238,0.08);border:2px solid rgba(34,211,238,0.2);border-radius:10px;padding:12px 16px;margin-bottom:24px;color:var(--primary);font-size:12px;font-weight:700;text-align:center;letter-spacing:2px}
.field{margin-bottom:20px}
.field label{display:block;font-size:10px;font-weight:700;color:var(--mt);letter-spacing:.7px;margin-bottom:8px;font-family:'Inter',sans-serif}
.field input{width:100%;padding:14px 16px;border-radius:10px;background:rgba(34,211,238,0.03);border:2px solid var(--brd);color:var(--dark);font-size:14px;font-family:'Inter',sans-serif;outline:none;transition:0.3s;letter-spacing:.2px}
.field input:focus{border-color:var(--primary);background:rgba(34,211,238,0.05);box-shadow:0 0 0 4px rgba(34,211,238,0.08)}
.field input::placeholder{color:#94A3B8}
.btn-submit{width:100%;padding:16px;border:none;border-radius:10px;background:linear-gradient(135deg,var(--primary),var(--secondary));color:#fff;font-size:14px;font-weight:800;letter-spacing:3px;;cursor:pointer;transition:0.3s;font-family:'Inter',sans-serif;box-shadow:0 8px 30px rgba(34,211,238,0.3)}
.btn-submit:hover{transform:translateY(-2px);box-shadow:0 16px 48px rgba(34,211,238,0.45)}
.back{text-align:center;margin-top:20px}
.back a{color:var(--mt);text-decoration:none;font-size:11px;font-weight:600;letter-spacing:3px;;transition:0.3s;font-family:'Inter',sans-serif}
.back a:hover{color:var(--primary)}
@keyframes rise{from{opacity:0;transform:translateY(30px) scale(0.97)}to{opacity:1;transform:translateY(0) scale(1)}}
</style>
</head>
<body>
<canvas id="bg"></canvas>
<div class="orb o1"></div><div class="orb o2"></div>
<div class="box">
<div class="box-inner">
<div class="logo"><div class="logo-icon">NX</div><div class="logo-text">KOHLI VPS</div><div class="logo-sub">SECURE ACCESS PORTAL</div></div>
{% if error %}<div class="error"><i class="fas fa-exclamation-triangle"></i> {{ error }}</div>{% endif %}
<form method="POST">
<div class="field"><label><i class="fas fa-user"></i> USERNAME</label><input type="text" name="username" placeholder="ENTER USERNAME" required></div>
<div class="field"><label><i class="fas fa-lock"></i> PASSWORD</label><input type="password" name="password" placeholder="ENTER PASSWORD" required></div>
<button type="submit" class="btn-submit"><i class="fas fa-arrow-right-to-bracket"></i> ACCESS</button>
</form>
<div class="back"><a href="/"><i class="fas fa-arrow-left"></i> BACK TO HOME</a></div>
</div>
</div>
<script>
const c=document.getElementById('bg'),ctx=c.getContext('2d');
let W,H,p=[];
function resize(){W=c.width=innerWidth;H=c.height=innerHeight}
class P{constructor(){this.reset()}reset(){this.x=Math.random()*W;this.y=Math.random()*H;this.vx=(Math.random()-.5)*0.3;this.vy=(Math.random()-.5)*0.3;this.r=Math.random()*1.4+0.4;this.a=Math.random()*0.25+0.05;this.col='34,211,238'}update(){this.x+=this.vx;this.y+=this.vy;if(this.x<0||this.x>W||this.y<0||this.y>H)this.reset()}draw(){ctx.beginPath();ctx.arc(this.x,this.y,this.r,0,Math.PI*2);ctx.fillStyle=`rgba(${this.col},${this.a})`;ctx.fill()}}
function init(){p=[];for(let i=0;i<60;i++)p.push(new P())}
function loop(){ctx.clearRect(0,0,W,H);p.forEach(d=>{d.update();d.draw()});requestAnimationFrame(loop)}
window.addEventListener('resize',()=>{resize();init()});resize();init();loop();
</script>
</body>
</html>"""

HTML_OWNER = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>KOHLI VPS — OWNER PANEL</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{--primary:#22D3EE;--secondary:#2563EB;--accent:#7DD3FC;--light:#0B1220;--dark:#E2E8F0;--card:rgba(15,23,42,0.94);--brd:rgba(148,163,184,0.18);--mt:#94A3B8;--txt:#E2E8F0}
body{font-family:'Inter',sans-serif;background:linear-gradient(135deg,#08111f 0%,#0f172a 55%,#111827 100%);color:var(--txt);min-height:100vh;letter-spacing:.2px;overflow-y:auto}
canvas#bg{position:fixed;inset:0;z-index:0;opacity:0.4;pointer-events:none}
.mesh{position:fixed;inset:0;z-index:0;pointer-events:none;background:radial-gradient(ellipse 60% 50% at 80% 0%,rgba(34,211,238,0.06),transparent),radial-gradient(ellipse 50% 40% at 0% 100%,rgba(37,99,235,0.06),transparent)}
.glass-nav{position:sticky;top:0;z-index:50;backdrop-filter:blur(28px);background:rgba(255,255,255,0.92);border-bottom:2px solid var(--primary);padding:0.6rem 2rem;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px;box-shadow:0 4px 20px rgba(34,211,238,0.06)}
.brand{display:flex;align-items:center;gap:10px}
.brand-icon{width:34px;height:34px;border-radius:8px;background:linear-gradient(135deg,var(--primary),var(--secondary));display:flex;align-items:center;justify-content:center;font-size:14px;font-weight:900;color:#fff;box-shadow:0 8px 24px rgba(34,211,238,0.3);font-family:'Inter',sans-serif}
.brand-text{font-size:18px;font-weight:900;background:linear-gradient(135deg,var(--primary),var(--accent));-webkit-background-clip:text;-webkit-text-fill-color:transparent;font-family:'Inter',sans-serif;letter-spacing:3px}
.badge-owner{padding:3px 14px;background:rgba(34,211,238,0.08);border:1px solid rgba(34,211,238,0.2);border-radius:50px;font-size:9px;font-weight:700;letter-spacing:3px;color:var(--primary);font-family:'Inter',sans-serif}
.nav-actions{display:flex;align-items:center;gap:10px}
.btn{padding:6px 16px;border-radius:6px;border:none;font-size:10px;font-weight:700;letter-spacing:2px;;cursor:pointer;transition:0.3s;font-family:'Inter',sans-serif;text-decoration:none;display:inline-flex;align-items:center;gap:5px}
.btn-danger{background:rgba(34,211,238,0.08);color:var(--primary);border:2px solid rgba(34,211,238,0.2)}
.btn-danger:hover{background:rgba(34,211,238,0.15)}
.btn-success{background:rgba(34,211,238,0.06);color:var(--primary);border:2px solid rgba(34,211,238,0.15)}
.btn-success:hover{background:rgba(34,211,238,0.12)}
.btn-primary{background:linear-gradient(135deg,var(--primary),var(--secondary));color:#fff;box-shadow:0 4px 16px rgba(34,211,238,0.25)}
.btn-primary:hover{transform:translateY(-2px);box-shadow:0 8px 32px rgba(34,211,238,0.4)}
.btn-sm{padding:4px 10px;font-size:9px}
.wrap{position:relative;z-index:1;max-width:1260px;margin:0 auto;padding:14px 24px}
.card{background:var(--card);backdrop-filter:blur(20px);border-radius:12px;border:2px solid var(--brd);padding:16px 20px;margin-bottom:14px;transition:0.3s;box-shadow:0 4px 16px rgba(34,211,238,0.04)}
.card:hover{border-color:rgba(34,211,238,0.25)}
.card-header{display:flex;align-items:center;gap:10px;font-size:12px;font-weight:700;letter-spacing:.7px;margin-bottom:12px;font-family:'Inter',sans-serif;color:var(--dark)}
.card-header i{color:var(--primary)}
.card-header .badge-count{font-weight:400;color:var(--mt);font-size:9px;letter-spacing:2px}
.form-row{display:flex;gap:10px;flex-wrap:wrap;align-items:center;justify-content:center}
.form-row input{padding:8px 14px;border-radius:6px;background:rgba(34,211,238,0.03);border:2px solid var(--brd);color:var(--dark);font-size:11px;font-family:'Inter',sans-serif;flex:1;min-width:100px;max-width:200px;outline:none;transition:0.3s;letter-spacing:.2px}
.form-row input:focus{border-color:var(--primary);background:rgba(34,211,238,0.05)}
.form-row input::placeholder{color:#94A3B8}
.form-row input[type="number"]{max-width:80px}
.table-wrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:12px}
th{padding:8px 12px;text-align:left;font-size:9px;font-weight:700;letter-spacing:.7px;color:var(--mt);border-bottom:2px solid var(--brd);font-family:'Inter',sans-serif;position:sticky;top:0;background:rgba(15,23,42,0.98);z-index:2}
td{padding:8px 12px;border-bottom:1px solid rgba(34,211,238,0.06);color:var(--dark)}
tr:hover td{background:rgba(34,211,238,0.03)}
.badge{display:inline-flex;align-items:center;gap:4px;padding:2px 12px;border-radius:50px;font-size:8px;font-weight:700;letter-spacing:2px;;font-family:'Inter',sans-serif}
.badge-active{background:rgba(34,211,238,0.08);color:var(--primary);border:1px solid rgba(34,211,238,0.2)}
.badge-expired{background:rgba(239,68,68,0.08);color:#EF4444;border:1px solid rgba(239,68,68,0.2)}
.badge-soon{background:rgba(245,158,11,0.08);color:#F59E0B;border:1px solid rgba(245,158,11,0.2)}
.link{font-size:9px;color:#94A3B8;font-family:'Orbitron',monospace;text-decoration:none;padding:2px 6px;border-radius:4px;background:rgba(34,211,238,0.04);transition:0.3s;word-break:break-all}
.link:hover{color:var(--primary);background:rgba(34,211,238,0.08)}
.actions{display:flex;gap:4px;flex-wrap:wrap;align-items:center}
.actions form{display:inline}
.actions input[type="number"]{width:44px;padding:3px 6px;border-radius:4px;background:rgba(34,211,238,0.03);border:2px solid var(--brd);color:var(--dark);font-size:10px;text-align:center;outline:none}
.pricing-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px;margin-top:10px}
.plan-card{background:rgba(34,211,238,0.02);border:2px solid var(--brd);border-radius:10px;padding:14px 12px;text-align:center;transition:0.3s}
.plan-card:hover{border-color:var(--primary)}
.plan-card .pname{font-family:'Inter',sans-serif;font-size:10px;font-weight:700;letter-spacing:2px;color:var(--primary)}
.plan-card .pprice{font-family:'Inter',sans-serif;font-size:22px;font-weight:900;color:var(--dark);margin:4px 0}
.plan-card .pdur{font-size:9px;color:var(--mt);letter-spacing:2px}
.plan-card .pfeat{font-size:8px;color:var(--mt);letter-spacing:1px;margin-top:4px}
.plan-card.hot{border-color:var(--primary);background:rgba(34,211,238,0.04)}
.plan-card .hot-tag{font-size:7px;color:var(--primary);letter-spacing:2px;font-family:'Inter',sans-serif}
.plan-card input{width:100%;padding:4px 8px;border-radius:4px;background:rgba(34,211,238,0.02);border:1px solid var(--brd);color:var(--dark);font-size:9px;text-align:center;font-family:'Inter',sans-serif;margin-bottom:3px;outline:none}
.plan-card input:focus{border-color:var(--primary)}
.contact-bar{padding:8px 16px;background:rgba(34,211,238,0.03);border:2px solid rgba(34,211,238,0.08);border-radius:8px;margin-top:10px;text-align:center;font-size:11px;color:var(--mt);letter-spacing:2px}
.contact-bar strong{color:var(--primary)}
.row2{display:grid;grid-template-columns:1.4fr 0.6fr;gap:14px}.admin-card .check{font-size:10px;color:var(--mt);display:flex;align-items:center;gap:5px}.permission-list{font-size:10px;color:var(--mt);line-height:1.7}
@media(max-width:900px){.row2{grid-template-columns:1fr}}
@media(max-width:600px){.glass-nav{padding:0.6rem 1rem}.wrap{padding:10px 16px}.form-row{flex-direction:column}.form-row input{max-width:100%}}
</style>
</head>
<body>
<canvas id="bg"></canvas><div class="mesh"></div>
<nav class="glass-nav">
<div class="brand"><div class="brand-icon">NX</div><span class="brand-text">KOHLI</span><span class="badge-owner"><i class="fas fa-crown"></i> OWNER</span></div>
<div class="nav-actions"><a href="/logout" class="btn btn-danger btn-sm"><i class="fas fa-sign-out-alt"></i> LOGOUT</a></div>
</nav>
<div class="wrap">
<!-- CREATE USER - CENTER -->
<div class="card">
<div class="card-header" style="justify-content:center"><i class="fas fa-user-plus"></i> CREATE USER</div>
<form method="POST" action="/owner/create" class="form-row">
<input type="text" name="username" placeholder="USERNAME" required>
<input type="text" name="password" placeholder="PASSWORD" required>
<input type="number" name="days" placeholder="DAYS" value="7" min="1">
<button type="submit" class="btn btn-primary"><i class="fas fa-plus"></i> CREATE</button>
</form>
</div>

<!-- ADMIN ACCOUNT MANAGEMENT -->
<div class="card admin-card">
<div class="card-header"><i class="fas fa-user-shield"></i> ADMIN ACCOUNTS <span class="badge-count">({{ admins|length }})</span></div>
<form method="POST" action="/owner/admin/create" class="form-row" style="justify-content:flex-start;margin-bottom:12px">
<input type="text" name="username" placeholder="ADMIN USERNAME" required>
<input type="password" name="password" placeholder="PASSWORD" required>
<label class="check"><input type="checkbox" name="permissions" value="manage_users"> USERS</label>
<label class="check"><input type="checkbox" name="permissions" value="manage_files"> FILES</label>
<label class="check"><input type="checkbox" name="permissions" value="run_servers"> SERVERS</label>
<button type="submit" class="btn btn-primary"><i class="fas fa-user-plus"></i> ADD ADMIN</button>
</form>
<div class="table-wrap"><table><thead><tr><th>ADMIN</th><th>PERMISSIONS</th><th>ACTION</th></tr></thead><tbody>
{% for username, info in admins.items() %}<tr><td><strong style="color:var(--primary)">{{ username }}</strong></td><td><span class="permission-list">{% for key in info.get('permissions', []) %}{{ permissions.get(key, key) }}{% if not loop.last %} · {% endif %}{% endfor %}</span></td><td class="actions"><form method="POST" action="/owner/admin/delete/{{ username }}" onsubmit="return confirm('DELETE ADMIN {{ username }}?')"><button type="submit" class="btn btn-danger btn-sm"><i class="fas fa-trash"></i> DELETE</button></form></td></tr>{% endfor %}
</tbody></table></div>
</div>

<!-- USERS + PRICING ROW -->
<div class="row2">
<!-- USERS TABLE -->
<div class="card" style="overflow:visible">
<div class="card-header"><i class="fas fa-users"></i> USERS <span class="badge-count">({{ users|length }})</span></div>
<div class="table-wrap" style="max-height:400px;overflow-y:auto">
<table>
<thead><tr><th>USER</th><th>PASS</th><th>EXPIRES</th><th>STATUS</th><th>LINK</th><th>ACTIONS</th></tr></thead>
<tbody>
{% for username, info in users.items() %}
<tr>
<td><strong style="color:var(--primary);font-size:11px">{{ username }}</strong></td>
<td><span style="font-family:'Orbitron',monospace;font-size:10px;color:var(--dark)">{{ info.password }}</span></td>
<td style="font-size:10px;color:var(--mt)">
{% if info.expires_at %}{{ time.strftime('%Y-%m-%d', time.localtime(info.expires_at)) }}{% else %}NEVER{% endif %}
</td>
<td>
{% if info.expires_at and info.expires_at < now %}
<span class="badge badge-expired">EXPIRED</span>
{% elif info.expires_at and info.expires_at < now + 86400*3 %}
<span class="badge badge-soon">SOON</span>
{% else %}
<span class="badge badge-active">ACTIVE</span>
{% endif %}
</td>
<td><a href="{{ base_url }}/auto/{{ info.token }}" target="_blank" class="link"><i class="fas fa-link"></i> {{ info.token[:10] }}…</a></td>
<td class="actions">
<form method="POST" action="/owner/extend/{{ username }}">
<input type="number" name="days" value="7" min="1">
<button type="submit" class="btn btn-success btn-sm"><i class="fas fa-clock"></i></button>
</form>
<form method="POST" action="/owner/delete/{{ username }}" onsubmit="return confirm('DELETE {{ username }}?')">
<button type="submit" class="btn btn-danger btn-sm"><i class="fas fa-trash"></i></button>
</form>
</td>
</tr>
{% endfor %}
</tbody>
</table>
</div>
</div>

<!-- PRICING MANAGEMENT -->
<div class="card">
<div class="card-header"><i class="fas fa-tags"></i> PRICING</div>
<form method="POST" action="/owner/pricing">
<div class="form-row" style="margin-bottom:10px;justify-content:flex-start">
<input type="text" name="currency" placeholder="CURRENCY" value="{{ pricing.currency }}" style="max-width:70px">
<input type="text" name="contact" placeholder="CONTACT" value="{{ pricing.contact }}" style="flex:2;font-size:9px">
</div>
<div class="pricing-grid">
{% for plan in pricing.plans %}
<div class="plan-card {% if loop.index == 3 %}hot{% endif %}">
{% if loop.index == 3 %}<div class="hot-tag"><i class="fas fa-star"></i> POPULAR</div>{% endif %}
<input type="text" name="p_name" value="{{ plan.name }}" placeholder="NAME">
<input type="text" name="p_duration" value="{{ plan.duration }}" placeholder="DURATION">
<input type="text" name="p_price" value="{{ plan.price }}" placeholder="PRICE" style="color:var(--primary);font-weight:700">
<input type="text" name="p_features" value="{{ plan.features }}" placeholder="FEATURES" style="font-size:7px">
</div>
{% endfor %}
<div class="plan-card" style="border-style:dashed">
<div style="font-size:8px;color:var(--mt);letter-spacing:2px;margin-bottom:4px">NEW</div>
<input type="text" name="p_name" placeholder="NAME">
<input type="text" name="p_duration" placeholder="DURATION">
<input type="text" name="p_price" placeholder="PRICE" style="color:var(--primary);font-weight:700">
<input type="text" name="p_features" placeholder="FEATURES" style="font-size:7px">
</div>
</div>
<div class="contact-bar"><i class="fas fa-headset"></i> <strong>{{ pricing.contact }}</strong></div>
<button type="submit" class="btn btn-primary" style="margin-top:10px;width:100%;justify-content:center"><i class="fas fa-save"></i> SAVE</button>
</form>
</div>
</div>
</div>
<script>
const c=document.getElementById('bg'),ctx=c.getContext('2d');
let W,H,p=[];
function resize(){W=c.width=innerWidth;H=c.height=innerHeight}
class P{constructor(){this.reset()}reset(){this.x=Math.random()*W;this.y=Math.random()*H;this.vx=(Math.random()-.5)*0.3;this.vy=(Math.random()-.5)*0.3;this.r=Math.random()*1.6+0.4;this.a=Math.random()*0.25+0.06;this.col='34,211,238'}update(){this.x+=this.vx;this.y+=this.vy;if(this.x<0||this.x>W||this.y<0||this.y>H)this.reset()}draw(){ctx.beginPath();ctx.arc(this.x,this.y,this.r,0,Math.PI*2);ctx.fillStyle=`rgba(${this.col},${this.a})`;ctx.fill()}}
function init(){p=[];for(let i=0;i<70;i++)p.push(new P())}
function loop(){ctx.clearRect(0,0,W,H);p.forEach(d=>{d.update();d.draw()});requestAnimationFrame(loop)}
window.addEventListener('resize',()=>{resize();init()});resize();init();loop();
</script>
</body>
</html>"""

HTML_USER = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>KOHLI VPS — DASHBOARD</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{--primary:#22D3EE;--secondary:#2563EB;--accent:#7DD3FC;--light:#0B1220;--dark:#E2E8F0;--card:rgba(15,23,42,0.94);--brd:rgba(148,163,184,0.18);--mt:#94A3B8;--txt:#E2E8F0}
body{font-family:'Inter',sans-serif;background:linear-gradient(135deg,#08111f 0%,#0f172a 55%,#111827 100%);color:var(--txt);min-height:100vh;letter-spacing:.2px}
canvas#bg{position:fixed;inset:0;z-index:0;opacity:0.4;pointer-events:none}
.mesh{position:fixed;inset:0;z-index:0;pointer-events:none;background:radial-gradient(ellipse 60% 50% at 80% 0%,rgba(34,211,238,0.06),transparent),radial-gradient(ellipse 50% 40% at 0% 100%,rgba(37,99,235,0.06),transparent)}
.glass-nav{position:sticky;top:0;z-index:50;backdrop-filter:blur(28px);background:rgba(15,23,42,0.92);border-bottom:2px solid var(--primary);padding:0.6rem 2rem;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px;box-shadow:0 4px 20px rgba(34,211,238,0.06)}
.brand{display:flex;align-items:center;gap:10px}
.brand-icon{width:34px;height:34px;border-radius:8px;background:linear-gradient(135deg,var(--primary),var(--secondary));display:flex;align-items:center;justify-content:center;font-size:14px;font-weight:900;color:#fff;box-shadow:0 8px 24px rgba(34,211,238,0.3);font-family:'Inter',sans-serif}
.brand-text{font-size:18px;font-weight:900;background:linear-gradient(135deg,var(--primary),var(--accent));-webkit-background-clip:text;-webkit-text-fill-color:transparent;font-family:'Inter',sans-serif;letter-spacing:3px}
.user-info{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.user-badge{display:flex;align-items:center;gap:6px;background:rgba(34,211,238,0.04);padding:4px 14px 4px 10px;border-radius:50px;border:2px solid var(--brd)}
.user-badge i{color:var(--primary);font-size:12px}
.user-badge .uname{font-size:12px;font-weight:700;font-family:'Inter',sans-serif;letter-spacing:2px;color:var(--dark)}
.user-badge .expiry{font-size:9px;color:var(--mt);letter-spacing:2px}
.btn{padding:6px 16px;border-radius:6px;border:none;font-size:10px;font-weight:700;letter-spacing:2px;;cursor:pointer;transition:0.3s;font-family:'Inter',sans-serif;text-decoration:none;display:inline-flex;align-items:center;gap:5px}
.btn-danger{background:rgba(34,211,238,0.08);color:var(--primary);border:2px solid rgba(34,211,238,0.2)}
.btn-danger:hover{background:rgba(34,211,238,0.15)}
.btn-success{background:rgba(34,211,238,0.06);color:var(--primary);border:2px solid rgba(34,211,238,0.15)}
.btn-success:hover{background:rgba(34,211,238,0.12)}
.btn-warning{background:rgba(34,211,238,0.05);color:var(--secondary);border:2px solid rgba(34,211,238,0.12)}
.btn-warning:hover{background:rgba(34,211,238,0.1)}
.btn-primary{background:linear-gradient(135deg,var(--primary),var(--secondary));color:#fff;box-shadow:0 4px 16px rgba(34,211,238,0.25)}
.btn-primary:hover{transform:translateY(-2px);box-shadow:0 8px 32px rgba(34,211,238,0.4)}
.btn-outline{background:transparent;border:2px solid var(--brd);color:var(--mt)}
.btn-outline:hover{border-color:var(--primary);color:var(--primary)}
.btn-sm{padding:4px 10px;font-size:9px}
.wrap{position:relative;z-index:1;max-width:1260px;margin:0 auto;padding:12px 20px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px}
.card{background:var(--card);backdrop-filter:blur(20px);border-radius:12px;border:2px solid var(--brd);padding:14px 18px;transition:0.3s;box-shadow:0 4px 16px rgba(34,211,238,0.04)}
.card:hover{border-color:rgba(34,211,238,0.2)}
.card-header{display:flex;align-items:center;gap:8px;font-size:11px;font-weight:700;letter-spacing:.7px;margin-bottom:10px;font-family:'Inter',sans-serif;color:var(--dark)}
.card-header i{color:var(--primary)}
.status-row{display:flex;align-items:center;gap:12px;margin-bottom:10px}
.status-dot{width:10px;height:10px;border-radius:50%;flex-shrink:0}
.status-dot.running{background:var(--primary);box-shadow:0 0 20px rgba(34,211,238,0.4);animation:pulse 2s infinite}
.status-dot.stopped{background:#EF4444;box-shadow:0 0 20px rgba(239,68,68,0.3)}
@keyframes pulse{0%,100%{box-shadow:0 0 20px rgba(34,211,238,0.4)}50%{box-shadow:0 0 40px rgba(34,211,238,0.1)}}
.status-label{font-weight:700;font-size:12px;font-family:'Inter',sans-serif;letter-spacing:2px}
.status-label.running{color:var(--primary)}
.status-label.stopped{color:#EF4444}
.running-file{font-size:10px;font-family:'Orbitron',monospace;color:var(--mt);background:rgba(34,211,238,0.04);padding:4px 12px;border-radius:6px;border:2px solid var(--brd);display:inline-block;margin-bottom:10px}
.running-file span{color:var(--primary)}
.ctrl-group{display:flex;gap:8px;flex-wrap:wrap}
.file-select{flex:1;min-width:120px;padding:8px 12px;background:rgba(34,211,238,0.03);border:2px solid var(--brd);border-radius:6px;color:var(--dark);font-size:11px;font-family:'Inter',sans-serif;outline:none;cursor:pointer;transition:0.3s;letter-spacing:.2px}
.file-select:focus{border-color:var(--primary)}
.file-select option{background:#fff}
.upload-zone{border:2px dashed var(--brd);border-radius:10px;padding:20px;text-align:center;cursor:pointer;transition:0.3s}
.upload-zone:hover{border-color:var(--primary);background:rgba(34,211,238,0.03)}
.upload-zone.drag-over{border-color:var(--primary);background:rgba(34,211,238,0.06)}
.upload-zone i{font-size:28px;color:rgba(34,211,238,0.2);display:block;margin-bottom:6px}
.upload-zone p{font-size:10px;color:var(--mt);font-weight:600;letter-spacing:2px}
.upload-zone input{display:none}
.file-list{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.file-chip{background:rgba(34,211,238,0.03);border:2px solid var(--brd);border-radius:50px;padding:3px 12px 3px 10px;display:inline-flex;align-items:center;gap:6px;font-size:10px;font-weight:600;font-family:'Orbitron',monospace;transition:0.2s}
.file-chip:hover{border-color:rgba(34,211,238,0.3)}
.file-chip i{color:var(--primary);font-size:10px}
.file-chip .actions{display:flex;gap:2px}
.file-chip .actions a,.file-chip .actions button{background:transparent;border:none;color:var(--mt);cursor:pointer;padding:1px 4px;border-radius:4px;font-size:9px;transition:0.2s}
.file-chip .actions a:hover{color:var(--primary)}
.file-chip .actions button:hover{color:#EF4444}
.terminal{background:var(--dark);border-radius:10px;overflow:hidden;border:2px solid rgba(34,211,238,0.08);margin-top:6px}
.term-bar{display:flex;align-items:center;gap:6px;padding:6px 14px;background:rgba(34,211,238,0.06);border-bottom:2px solid rgba(34,211,238,0.08)}
.term-dot{width:8px;height:8px;border-radius:50%}
.term-dot.red{background:#EF4444}
.term-dot.yellow{background:#F59E0B}
.term-dot.green{background:#10B981}
.term-title{margin-left:4px;font-size:9px;color:rgba(255,255,255,0.4);letter-spacing:3px;font-family:'Orbitron',monospace}
.term-body{padding:10px 14px;max-height:150px;overflow-y:auto;font-family:'Orbitron',monospace;font-size:9px;line-height:1.8;white-space:pre-wrap;word-break:break-word;color:#C4B5FD;letter-spacing:0.5px}
.term-body::-webkit-scrollbar{width:3px}
.term-body::-webkit-scrollbar-track{background:transparent}
.term-body::-webkit-scrollbar-thumb{background:rgba(34,211,238,0.3);border-radius:10px}
.install-row{display:flex;gap:8px}
.install-row input{flex:1;padding:8px 12px;border-radius:6px;background:rgba(34,211,238,0.03);border:2px solid var(--brd);color:var(--dark);font-size:10px;font-family:'Orbitron',monospace;outline:none;transition:0.3s;letter-spacing:.2px}
.install-row input:focus{border-color:var(--primary)}
.install-row input::placeholder{color:rgba(30,27,75,0.25);font-family:'Inter',sans-serif;font-size:10px;letter-spacing:2px}
.editor-panel{grid-column:1/-1}.editor-panel textarea{width:100%;min-height:300px;resize:vertical;background:#020617;color:#cbd5e1;border:1px solid var(--brd);border-radius:8px;padding:14px;font:13px/1.55 "JetBrains Mono",monospace;outline:none}.editor-panel textarea:focus{border-color:var(--primary)}.editor-actions{display:flex;align-items:center;gap:14px;margin-top:10px}.editor-actions span,.editor-notice{font-size:10px;color:var(--mt)}.editor-notice{padding:8px 10px;margin-bottom:8px;border-left:3px solid var(--primary);background:rgba(34,211,238,.05)}.metrics-grid{display:grid;grid-template-columns:1fr 1fr 180px;gap:12px;margin-bottom:12px}.metric-card{background:var(--card);border:2px solid var(--brd);border-radius:12px;padding:14px 16px;min-height:145px}.metric-top{display:flex;justify-content:space-between;align-items:center;color:var(--mt);font-size:10px;font-weight:700;letter-spacing:.5px}.metric-top i,.compact-metric i{color:var(--primary)}.metric-top strong{font-size:20px;color:var(--dark)}.metric-card canvas{width:100%!important;height:90px!important;margin-top:8px}.compact-metric{display:flex;flex-direction:column;gap:10px;color:var(--mt);font-size:10px;font-weight:700}.compact-metric strong{font-size:30px;color:var(--primary);margin-top:3px}.compact-metric small{font-size:9px;color:var(--mt);font-weight:500}.github-card{margin-bottom:12px}.github-form{display:flex;gap:8px;flex-wrap:wrap}.github-form input{flex:1;min-width:180px;padding:9px 12px;background:rgba(34,211,238,.03);border:2px solid var(--brd);border-radius:6px;color:var(--dark);outline:none;font-family:Inter,sans-serif}.github-form input:focus{border-color:var(--primary)}.github-status{font-size:9px;color:var(--mt);margin-top:8px}@media(max-width:900px){.metrics-grid{grid-template-columns:1fr 1fr}.compact-metric{grid-column:1/-1;min-height:auto;flex-direction:row;align-items:center;justify-content:space-between}}@media(max-width:900px){.grid{grid-template-columns:1fr}}
@media(max-width:600px){.glass-nav{padding:0.6rem 1rem}.wrap{padding:8px 12px}}
</style>
</head>
<body>
<canvas id="bg"></canvas><div class="mesh"></div>
<nav class="glass-nav">
<div class="brand"><div class="brand-icon">NX</div><span class="brand-text">KOHLI</span></div>
<div class="user-info">
<div class="user-badge"><i class="fas fa-user-astronaut"></i><span class="uname">{{ username }}</span>{% if expires_at %}<span class="expiry">⚡ {{ expires_at|timestamp_to_date }}</span>{% endif %}</div>
<a href="/logout" class="btn btn-danger btn-sm"><i class="fas fa-sign-out-alt"></i> LOGOUT</a>
</div>
</nav>
<div class="wrap">
<div class="metrics-grid"><div class="metric-card"><div class="metric-top"><span><i class="fas fa-microchip"></i> CPU USAGE</span><strong id="cpuValue">0%</strong></div><canvas id="cpuChart"></canvas></div><div class="metric-card"><div class="metric-top"><span><i class="fas fa-memory"></i> RAM USAGE</span><strong id="ramValue">0%</strong></div><canvas id="ramChart"></canvas></div><div class="metric-card compact-metric"><span><i class="fas fa-hard-drive"></i> DISK</span><strong id="diskValue">0%</strong><small id="uptimeValue">UPTIME —</small></div></div>
<div class="grid">
<div class="card">
<div class="card-header"><i class="fas fa-server"></i> SERVER</div>
<div class="status-row"><div class="status-dot {% if running %}running{% else %}stopped{% endif %}"></div><span class="status-label {% if running %}running{% else %}stopped{% endif %}">{% if running %}● RUNNING{% else %}● STOPPED{% endif %}</span></div>
{% if running_file %}<div class="running-file">ACTIVE: <span>{{ running_file }}</span></div>{% endif %}
<div class="ctrl-group">
<select class="file-select" id="fileSelect">
<option value="">— SELECT —</option>
{% for f in files %}<option value="{{ f }}">{{ f }}</option>{% endfor %}
</select>
</div>
<div class="ctrl-group">
<button class="btn btn-success" onclick="startServer()"><i class="fas fa-play"></i> START</button>
<button class="btn btn-danger" onclick="stopServer()"><i class="fas fa-stop"></i> STOP</button>
<button class="btn btn-warning" onclick="restartServer()"><i class="fas fa-sync"></i> RESTART</button>
<button class="btn btn-outline btn-sm" onclick="deleteServer()"><i class="fas fa-trash"></i></button>
</div>
</div>
<div class="card">
<div class="card-header"><i class="fas fa-folder-open"></i> FILE MANAGER <span class="badge-count">EDIT / VIEW / DELETE</span></div>
<div class="upload-zone" id="uploadZone"><i class="fas fa-cloud-upload-alt"></i><p>DRAG OR CLICK</p><input type="file" id="fileInput" multiple></div>
<div class="file-list" id="fileList">
{% for f in files %}
<div class="file-chip"><i class="fas fa-file-code"></i> <span>{{ f }}</span><span class="actions"><button type="button" onclick="openEditor('{{ f }}')" title="Edit"><i class="fas fa-pen"></i></button><a href="/file/view/{{ f }}" target="_blank" title="View"><i class="fas fa-eye"></i></a><a href="/download/{{ f }}" target="_blank" title="Download"><i class="fas fa-download"></i></a><form method="POST" action="/file/delete/{{ f }}" style="display:inline" onsubmit="return confirm('DELETE?')"><button type="submit" title="Delete"><i class="fas fa-times"></i></button></form></span></div>
{% endfor %}
</div>
</div>
</div>
<div class="card github-card"><div class="card-header"><i class="fab fa-github"></i> IMPORT FROM GITHUB</div><div class="github-form"><input id="githubUrl" type="url" placeholder="https://github.com/username/repository"/><input id="githubBranch" type="text" placeholder="BRANCH (OPTIONAL)"/><button class="btn btn-primary" onclick="importGithub()"><i class="fas fa-cloud-download-alt"></i> IMPORT</button></div><div id="githubStatus" class="github-status">Public HTTPS repositories only · Existing files with the same name will be replaced</div></div>
<div class="editor-panel card"> id="editorPanel" style="display:none"><div class="card-header"><i class="fas fa-pen-to-square"></i> EDIT FILE <span id="editorName"></span><button type="button" class="btn btn-outline btn-sm" style="margin-left:auto" onclick="closeEditor()">CLOSE</button></div><div id="editorNotice" class="editor-notice"></div><textarea id="editorText" spellcheck="false"></textarea><div class="editor-actions"><button type="button" class="btn btn-primary" onclick="saveEditor()"><i class="fas fa-save"></i> SAVE FILE</button><span id="editorSize"></span></div></div>
<div class="grid">
<div class="card">
<div class="card-header"><i class="fas fa-cubes"></i> INSTALL</div>
<div class="install-row"><input type="text" id="installCmd" placeholder="PIP INSTALL"><button class="btn btn-primary" onclick="installModule()"><i class="fas fa-download"></i></button></div>
<div class="terminal"><div class="term-bar"><span class="term-dot red"></span><span class="term-dot yellow"></span><span class="term-dot green"></span><span class="term-title">LOG</span></div><div class="term-body" id="installOutput">— READY —</div></div>
</div>
<div class="card">
<div class="card-header"><i class="fas fa-terminal"></i> LOGS <button class="btn btn-outline btn-sm" style="margin-left:auto;padding:2px 10px;font-size:8px" onclick="refreshLogs()"><i class="fas fa-sync"></i></button></div>
<div class="terminal" style="border:none;border-radius:0;margin-top:0"><div class="term-body" id="logOutput" style="max-height:180px">[SYSTEM] WAITING…</div></div>
</div>
</div>
</div>
<script>
const fileSelect=document.getElementById('fileSelect');
const logOutput=document.getElementById('logOutput');
const installOutput=document.getElementById('installOutput');
const uploadZone=document.getElementById('uploadZone');
const fileInput=document.getElementById('fileInput');

function showToast(msg,type='success'){const el=document.createElement('div');el.style.cssText=`position:fixed;top:70px;right:16px;z-index:9999;padding:10px 18px;border-radius:6px;font-size:10px;font-weight:700;letter-spacing:2px;;backdrop-filter:blur(20px);border:2px solid ${type==='success'?'rgba(34,211,238,0.3)':'rgba(239,68,68,0.3)'};background:${type==='success'?'rgba(34,211,238,0.08)':'rgba(239,68,68,0.08)'};color:${type==='success'?'#22D3EE':'#EF4444'};animation:slideIn 0.3s ease`;el.textContent=msg;document.body.appendChild(el);setTimeout(()=>el.remove(),3000)}
const style=document.createElement('style');style.textContent='@keyframes slideIn{from{opacity:0;transform:translateX(30px)}to{opacity:1;transform:translateX(0)}}';document.head.appendChild(style);

function getFile(){return fileSelect.value||prompt('FILENAME:')}
function startServer(){const f=getFile();if(!f)return;fetch('/server/start',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:'file='+encodeURIComponent(f)}).then(r=>r.json()).then(d=>{showToast(d.msg,d.ok);if(d.ok)setTimeout(()=>location.reload(),800)})}
function stopServer(){if(!confirm('STOP PROCESS?'))return;fetch('/server/stop',{method:'POST'}).then(()=>location.reload())}
function restartServer(){const f=getFile();if(!f)return;fetch('/server/restart',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:'file='+encodeURIComponent(f)}).then(r=>r.json()).then(d=>{showToast(d.msg,d.ok);if(d.ok)setTimeout(()=>location.reload(),800)})}
function deleteServer(){if(!confirm('DELETE PROCESS?'))return;fetch('/server/delete',{method:'POST'}).then(()=>location.reload())}
function refreshLogs(){fetch('/logs').then(r=>r.json()).then(d=>{logOutput.innerHTML=d.logs&&d.logs.length?d.logs.join('\\n'):'[SYSTEM] NO OUTPUT';if(d.install&&d.install.length)installOutput.innerHTML=d.install.join('\\n')})}
function installModule(){const cmd=document.getElementById('installCmd').value.trim();if(!cmd)return;installOutput.textContent='INSTALLING…';fetch('/install',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:'command='+encodeURIComponent(cmd)}).then(r=>r.json()).then(d=>{installOutput.textContent=d.msg;showToast(d.msg,d.ok)})}

uploadZone.addEventListener('click',()=>fileInput.click());
uploadZone.addEventListener('dragover',e=>{e.preventDefault();uploadZone.classList.add('drag-over')});
uploadZone.addEventListener('dragleave',()=>uploadZone.classList.remove('drag-over'));
uploadZone.addEventListener('drop',e=>{e.preventDefault();uploadZone.classList.remove('drag-over');if(e.dataTransfer.files.length)uploadFiles(e.dataTransfer.files)});
fileInput.addEventListener('change',function(){if(this.files.length)uploadFiles(this.files);this.value=''});
function uploadFiles(files){const fd=new FormData();for(let f of files)fd.append('files',f);fetch('/upload',{method:'POST',body:fd}).then(()=>location.reload())}

function openEditor(name){const panel=document.getElementById('editorPanel');panel.style.display='block';document.getElementById('editorName').textContent=name;document.getElementById('editorText').value='Loading…';document.getElementById('editorNotice').textContent='';fetch('/file/read/'+encodeURIComponent(name)).then(r=>r.json()).then(d=>{if(!d.ok){showToast(d.msg,'error');closeEditor();return}document.getElementById('editorText').value=d.content;document.getElementById('editorSize').textContent=(d.size/1024).toFixed(1)+' KB';document.getElementById('editorNotice').textContent=d.large?'This file is over 120KB. Online editing is enabled up to 5MB; save carefully.':'Online editor ready.';panel.scrollIntoView({behavior:'smooth',block:'start'})})}
function closeEditor(){document.getElementById('editorPanel').style.display='none'}
function saveEditor(){const name=document.getElementById('editorName').textContent;fetch('/file/save/'+encodeURIComponent(name),{method:'POST',headers:{'Content-Type':'text/plain;charset=UTF-8'},body:document.getElementById('editorText').value}).then(r=>r.json()).then(d=>showToast(d.msg,d.ok))}

const chartLabels=[];const cpuData=[];const ramData=[];let cpuChart,ramChart;
function makeChart(id,data,color){return new Chart(document.getElementById(id),{type:'line',data:{labels:chartLabels,datasets:[{data,borderColor:color,backgroundColor:color.replace('1)','0.12)'),borderWidth:2,fill:true,tension:.35,pointRadius:0}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{x:{display:false},y:{min:0,max:100,display:false}},animation:false}})}
function updateMetrics(){fetch('/metrics').then(r=>r.json()).then(d=>{const now=new Date().toLocaleTimeString([], {minute:'2-digit',second:'2-digit'});if(chartLabels.length>20){chartLabels.shift();cpuData.shift();ramData.shift()}chartLabels.push(now);cpuData.push(d.cpu);ramData.push(d.ram);document.getElementById('cpuValue').textContent=d.cpu+'%';document.getElementById('ramValue').textContent=d.ram+'%';document.getElementById('diskValue').textContent=d.disk+'%';document.getElementById('uptimeValue').textContent='UPTIME '+Math.floor(d.uptime/3600)+'H '+Math.floor(d.uptime/60)%60+'M';if(!cpuChart){cpuChart=makeChart('cpuChart',cpuData,'rgba(34,211,238,1)');ramChart=makeChart('ramChart',ramData,'rgba(37,99,235,1)')}else{cpuChart.update();ramChart.update()}}).catch(()=>{})}
function importGithub(){const url=document.getElementById('githubUrl').value.trim();const branch=document.getElementById('githubBranch').value.trim();const status=document.getElementById('githubStatus');if(!url){status.textContent='Enter a GitHub repository URL';return}status.textContent='IMPORTING REPOSITORY…';fetch('/github/import',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:'repo_url='+encodeURIComponent(url)+'&branch='+encodeURIComponent(branch)}).then(r=>r.json()).then(d=>{status.textContent=d.msg;if(d.ok)setTimeout(()=>location.reload(),900)})}
updateMetrics();setInterval(updateMetrics,3000);

refreshLogs();setInterval(refreshLogs,4000);

const c=document.getElementById('bg'),ctx=c.getContext('2d');
let W,H,p=[];
function resize(){W=c.width=innerWidth;H=c.height=innerHeight}
class P{constructor(){this.reset()}reset(){this.x=Math.random()*W;this.y=Math.random()*H;this.vx=(Math.random()-.5)*0.3;this.vy=(Math.random()-.5)*0.3;this.r=Math.random()*1.6+0.4;this.a=Math.random()*0.25+0.06;this.col='34,211,238'}update(){this.x+=this.vx;this.y+=this.vy;if(this.x<0||this.x>W||this.y<0||this.y>H)this.reset()}draw(){ctx.beginPath();ctx.arc(this.x,this.y,this.r,0,Math.PI*2);ctx.fillStyle=`rgba(${this.col},${this.a})`;ctx.fill()}}
function init(){p=[];for(let i=0;i<70;i++)p.push(new P())}
function loop(){ctx.clearRect(0,0,W,H);p.forEach(d=>{d.update();d.draw()});requestAnimationFrame(loop)}
window.addEventListener('resize',()=>{resize();init()});resize();init();loop();
</script>
</body>
</html>"""

# ============================================
#  MAIN
# ============================================
if __name__ == "__main__":
    print("\n" + "="*70)
    print("🚀 KOHLI VPS PANEL - WHITE PURPLE ULTRA PREMIUM EDITION")
    print("="*70)
    print(f"📍 LOCAL:  http://127.0.0.1:5000")
    print(f"📍 NETWORK: http://0.0.0.0:5000")
    print(f"👤 OWNER:  {OWNER_USER} / {OWNER_PASS}")
    print("="*70 + "\n")
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
