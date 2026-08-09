# -*- coding: utf-8 -*-
"""Seedance WebUI - 前后端分离：多用户 + 积分体系 + 管理员后台"""
import hashlib
import json
import re
import secrets
import shutil
import sqlite3
import threading
import time
import uuid
from pathlib import Path

import requests
import uvicorn
from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, FastAPI, Request, UploadFile, File, Form, Header, HTTPException, Query
import io
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = BASE_DIR / "uploads"
DOWNLOADS_DIR = BASE_DIR / "downloads"
STATIC_DIR = BASE_DIR / "static"
for d in (DATA_DIR, UPLOAD_DIR, DOWNLOADS_DIR):
    d.mkdir(exist_ok=True)

DB_PATH = DATA_DIR / "seedance.db"
SECRET_PATH = DATA_DIR / ".secret_key"

DEFAULT_CONFIG = {"base_url": "https://api.linghuiai.top"}
DEFAULT_FEATURES = {
    "text_mode": True,
    "multi_mode": True,
    "first_last_mode": True,
    "task_center": True,
    "upload_enabled": True,
    "enabled_models": [],
}
DEFAULT_SETTINGS = {"retention_days": "30", "max_concurrent": "3", "platform_key_enc": "", "public_base_url": ""}
RATIOS = ["16:9", "9:16", "1:1", "4:3", "3:4", "2.35:1", "21:9"]
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
VIDEO_EXTS = (".mp4", ".mov", ".webm", ".avi", ".mkv", ".m4v")
AUDIO_EXTS = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".opus")
MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100MB
TASK_TIMEOUT_SECONDS = 600

app = FastAPI(title="Seedance WebUI")


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def now_str():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def init_db():
    conn = get_db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        api_key_enc TEXT DEFAULT '',
        points REAL NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'active',
        role TEXT NOT NULL DEFAULT 'user',
        token TEXT DEFAULT '',
        created_at TEXT,
        updated_at TEXT
    );
    CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY,
        local_id TEXT,
        user_id INTEGER NOT NULL,
        username TEXT,
        model TEXT, prompt TEXT, duration TEXT, resolution TEXT, ratio TEXT, mode TEXT,
        assets_json TEXT DEFAULT '',
        status TEXT, cost REAL, video_url TEXT, local_video TEXT, cover_url TEXT,
        error_message TEXT, keep_forever INTEGER DEFAULT 0,
        created_at TEXT, updated_at TEXT
    );
    CREATE TABLE IF NOT EXISTS point_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, username TEXT, type TEXT, amount REAL, balance REAL,
        task_id TEXT, note TEXT, created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS password_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, username TEXT, changed_by TEXT, created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        admin_id INTEGER, admin_name TEXT, action TEXT, detail TEXT, created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS pricing (
        model_id TEXT PRIMARY KEY,
        cost_per_second REAL
    );
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    );
    """)
    # 迁移：老库补充 assets_json 列
    cols = [r[1] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()]
    if "assets_json" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN assets_json TEXT DEFAULT ''")
    conn.commit()
    cur = conn.execute("SELECT id FROM users WHERE role='admin' LIMIT 1")
    if cur.fetchone() is None:
        conn.execute(
            "INSERT INTO users (username, password_hash, points, status, role, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            ("admin", _hash_password("admin123"), 0, "active", "admin", now_str(), now_str()))
    for k, v in DEFAULT_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?,?)", (k, v))
    conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('base_url', ?)", (DEFAULT_CONFIG["base_url"],))
    conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('features', ?)", (json.dumps(DEFAULT_FEATURES, ensure_ascii=False),))
    conn.commit()
    conn.close()

# ---------- 加密 / 哈希 ----------

def _get_fernet() -> Fernet:
    if SECRET_PATH.exists():
        key = SECRET_PATH.read_bytes().strip()
    else:
        key = Fernet.generate_key()
        SECRET_PATH.write_bytes(key)
    return Fernet(key)


def _encrypt_key(plain: str) -> str:
    if not plain:
        return ""
    return _get_fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def _decrypt_key(enc: str) -> str:
    if not enc:
        return ""
    try:
        return _get_fernet().decrypt(enc.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return ""


def _hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000)
    return f"{salt}${digest.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$", 1)
        calc = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000)
        return secrets.compare_digest(calc.hex(), digest)
    except Exception:
        return False


# ---------- 设置 / 功能 ----------

def get_setting(key: str, default: str = "") -> str:
    conn = get_db()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default
    finally:
        conn.close()


def set_setting(key: str, value: str) -> None:
    conn = get_db()
    try:
        conn.execute("INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        conn.commit()
    finally:
        conn.close()


def get_features() -> dict:
    try:
        f = json.loads(get_setting("features", "{}"))
    except Exception:
        f = {}
    merged = dict(DEFAULT_FEATURES)
    merged.update({k: v for k, v in f.items() if k in DEFAULT_FEATURES})
    if isinstance(f.get("enabled_models"), list):
        merged["enabled_models"] = f["enabled_models"]
    return merged


def save_features(features: dict) -> None:
    set_setting("features", json.dumps(features, ensure_ascii=False))


def get_config() -> dict:
    return {"base_url": get_setting("base_url", DEFAULT_CONFIG["base_url"])}


# ---------- 认证 ----------

def _extract_token(authorization: str | None) -> str:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return ""


def _user_by_token(token: str, role: str | None = None):
    if not token:
        return None
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM users WHERE token=? AND status='active'", (token,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    if role and row["role"] != role:
        return None
    return dict(row)


def require_user(authorization: str | None = Header(None)) -> dict:
    user = _user_by_token(_extract_token(authorization), role="user")
    if not user:
        raise HTTPException(status_code=401, detail="未登录或登录已过期")
    return user


def require_admin(authorization: str | None = Header(None)) -> dict:
    admin = _user_by_token(_extract_token(authorization), role="admin")
    if not admin:
        raise HTTPException(status_code=401, detail="管理员未登录或登录已过期")
    return admin


def require_user_media(authorization: str | None = Header(None), token: str | None = Query(None)) -> dict:
    """视频/文件接口鉴权：兼容 Authorization 头与 ?token= 查询参数（浏览器 <video>/<a> 不带请求头）"""
    tok = _extract_token(authorization) or (token or "").strip()
    user = _user_by_token(tok, role="user")
    if not user:
        raise HTTPException(status_code=401, detail="未登录或登录已过期")
    return user


def require_admin_media(authorization: str | None = Header(None), token: str | None = Query(None)) -> dict:
    tok = _extract_token(authorization) or (token or "").strip()
    admin = _user_by_token(tok, role="admin")
    if not admin:
        raise HTTPException(status_code=401, detail="管理员未登录或登录已过期")
    return admin


def _task_view(t, is_admin: bool = False) -> dict:
    """序列化任务：补充 video_play_url（本地文件存在则用本地下载接口，否则退回平台 URL）"""
    d = dict(t)
    st = d.get("status") or "pending"
    if st in ("success", "completed", "succeeded"):
        st = "succeeded"
    elif st in ("failed", "error"):
        st = "failed"
    d["status"] = st
    d["video_play_url"] = ""
    if d.get("local_video"):
        lpath = Path(d["local_video"])
        if lpath.exists():
            d["video_play_url"] = ("/api/admin/download/" if is_admin else "/api/download/") + d["id"]
        else:
            d["local_video"] = ""
    if not d["video_play_url"] and d.get("video_url"):
        d["video_play_url"] = d["video_url"]
    try:
        d["assets"] = json.loads(d.get("assets_json") or "") if d.get("assets_json") else []
    except Exception:
        d["assets"] = []
    return d


def _ref_image_exists(prompt: str) -> str:
    """兼容旧逻辑：检查提示词里直接粘贴的本站 /uploads 图片是否仍存在"""
    return _ref_material_exists(prompt or "")


def _file_type(ext: str) -> str:
    ext = (ext or "").lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    return ""


def _ref_material_exists(url: str) -> str:
    """检查素材 URL 指向的本站 /uploads 文件是否仍存在，返回失效文件名或空串"""
    import re
    m = re.search(r"/uploads/([A-Za-z0-9._-]+)", url or "")
    if m and not (UPLOAD_DIR / m.group(1)).exists():
        return m.group(1)
    return ""


def _abs_url(request, url: str) -> str:
    """把本站 /uploads 相对路径转成平台可访问的公网 URL。

    优先使用管理员配置的 public_base_url（本地开发时指向云端/隧道域名），
    未配置时回退为当前请求的站点地址（公网部署时自动生效）。
    """
    url = (url or "").strip()
    if not url:
        return ""
    if url.startswith(("http://", "https://")):
        return url
    base = get_setting("public_base_url", "").strip().rstrip("/")
    if not base:
        base = str(request.base_url).rstrip("/")
    return base + ("/" + url.lstrip("/") if url.startswith("/") else "/" + url)


def _parse_materials(raw: str):
    """解析前端传来的素材列表 JSON -> [{url, type, name}]"""
    import json as _json
    if not raw or not raw.strip():
        return []
    try:
        data = _json.loads(raw)
    except Exception:
        return None  # 非法 JSON
    if not isinstance(data, list):
        return None
    out = []
    for it in data:
        if not isinstance(it, dict):
            continue
        url = str(it.get("url") or "").strip()
        ftype = str(it.get("type") or "").strip()
        if not url or ftype not in ("image", "video", "audio"):
            continue
        out.append({"url": url, "type": ftype, "name": str(it.get("name") or "")})
    return out


_ZONE_TAG = {"图": "image", "视": "video", "音": "audio"}


def _resolve_refs(prompt: str, materials: list) -> tuple:
    """把提示词里的 @N / @图N / @视N / @音N 标记映射到素材列表。

    @N 按上传顺序引用（兼容旧式）；@图N/@视N/@音N 按三个参考区（图片/视频/音频）独立编号引用。
    返回 (清理后的提示词, 被引用的素材列表)。
    """
    import re
    zones = {"image": [], "video": [], "audio": []}
    for m in materials:
        if m.get("type") in zones:
            zones[m["type"]].append(m)
    used = set()

    def repl(m):
        tag = m.group(1)
        n = int(m.group(2)) - 1
        if tag is not None:
            t = _ZONE_TAG.get(tag)
            if t is not None and 0 <= n < len(zones[t]):
                used.add(id(zones[t][n]))
                return ""
            return m.group(0)
        if 0 <= n < len(materials):
            used.add(id(materials[n]))
            return ""
        return m.group(0)

    cleaned = re.sub(r"@([图视音])?(\d+)", repl, prompt or "")
    refs = [m for m in materials if id(m) in used]
    return cleaned, refs


def _mask_key(key: str) -> str:
    if len(key) <= 8:
        return "****"
    return key[:4] + "****" + key[-4:]


def _audit(admin: dict, action: str, detail: str) -> None:
    conn = get_db()
    try:
        conn.execute("INSERT INTO audit_logs (admin_id, admin_name, action, detail, created_at) VALUES (?,?,?,?,?)",
                     (admin["id"], admin["username"], action, detail, now_str()))
        conn.commit()
    finally:
        conn.close()


def _points_balance(user_id: int) -> float:
    conn = get_db()
    try:
        row = conn.execute("SELECT points FROM users WHERE id=?", (user_id,)).fetchone()
        return float(row["points"]) if row else 0.0
    finally:
        conn.close()

# ---------- 平台调用 / 模型 ----------

def api_headers(key: str) -> dict:
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _parse_platform_price(price) -> float:
    if not price:
        return 0.0
    import re
    m = re.search(r"[\d.]+", str(price))
    try:
        return float(m.group()) if m else 0.0
    except Exception:
        return 0.0


def _is_configured(model_id: str) -> bool:
    conn = get_db()
    try:
        row = conn.execute("SELECT 1 FROM pricing WHERE model_id=?", (model_id,)).fetchone()
        return row is not None
    finally:
        conn.close()


def _model_cost(model_id: str, platform_price) -> float:
    conn = get_db()
    try:
        row = conn.execute("SELECT cost_per_second FROM pricing WHERE model_id=?", (model_id,)).fetchone()
        if row:
            return float(row["cost_per_second"])
    finally:
        conn.close()
    return _parse_platform_price(platform_price)


MODEL_CACHE = {"ts": 0.0, "models": None}
MODEL_CACHE_TTL = 300.0


@app.get("/api/models")
def list_models(force: int = 0, _auth: dict = Depends(require_user)):
    now = time.time()
    if not force and MODEL_CACHE["models"] and now - MODEL_CACHE["ts"] < MODEL_CACHE_TTL:
        return {"ok": True, "models": MODEL_CACHE["models"], "cached": True}
    cfg = get_config()
    key = _get_platform_key()
    if not key:
        return JSONResponse({"ok": False, "error": "平台 Key 未配置，请联系管理员"}, status_code=400)
    try:
        r = requests.get(f"{cfg['base_url']}/v1/models", headers=api_headers(key), timeout=25)
        if r.status_code != 200:
            return JSONResponse({"ok": False, "error": f"获取模型失败: {r.status_code} {r.text[:200]}"}, status_code=502)
        data = r.json().get("data", [])
        features = get_features()
        enabled = features.get("enabled_models") or []
        result = []
        for m in data:
            model_id = m.get("id")
            result.append({
                "id": model_id,
                "description": m.get("description", ""),
                "type": m.get("type", ""),
                "platform_price": m.get("price", ""),
                "cost_per_second": _model_cost(model_id, m.get("price")),
                "configured": _is_configured(model_id),
                "enabled": (not enabled) or (model_id in enabled),
            })
        MODEL_CACHE["models"] = result
        MODEL_CACHE["ts"] = time.time()
        return {"ok": True, "models": result, "cached": False}
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"请求异常: {e}"}, status_code=502)


# ---------- 用户登录 / 资料 ----------

@app.get("/api/features")
def read_features(_auth: dict = Depends(require_user)):
    return {"ok": True, "features": get_features()}


@app.post("/api/login")
def login(username: str = Form(...), password: str = Form(...)):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM users WHERE username=? AND role='user'", (username.strip(),)).fetchone()
    finally:
        conn.close()
    if not row or not _verify_password(password, row["password_hash"]):
        return JSONResponse({"ok": False, "error": "账号或密码错误"}, status_code=401)
    if row["status"] != "active":
        return JSONResponse({"ok": False, "error": "账号已被停用，请联系管理员"}, status_code=403)
    token = secrets.token_hex(24)
    conn = get_db()
    try:
        conn.execute("UPDATE users SET token=?, updated_at=? WHERE id=?", (token, now_str(), row["id"]))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "token": token, "username": row["username"], "role": "user"}


@app.post("/api/logout")
def logout(_auth: dict = Depends(require_user)):
    conn = get_db()
    try:
        conn.execute("UPDATE users SET token='' WHERE id=?", (_auth["id"],))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@app.get("/api/session")
def session(authorization: str | None = Header(None)):
    user = _user_by_token(_extract_token(authorization), role="user")
    if user:
        return {"ok": True, "logged_in": True, "username": user["username"], "role": "user"}
    return {"ok": True, "logged_in": False}


@app.get("/api/me")
def me(_auth: dict = Depends(require_user)):
    return {
        "ok": True,
        "username": _auth["username"],
        "points": round(float(_auth["points"]), 1),
        "ratios": RATIOS,
        "retention_days": int(get_setting("retention_days", "30") or 30),
    }


@app.get("/api/points_logs")
def points_logs(_auth: dict = Depends(require_user)):
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM point_logs WHERE user_id=? ORDER BY id DESC LIMIT 100", (_auth["id"],)).fetchall()
    finally:
        conn.close()
    return {"ok": True, "logs": [dict(r) for r in rows]}


@app.post("/api/password")
def change_password(new_password: str = Form(...), _auth: dict = Depends(require_user)):
    new_password = (new_password or "").strip()
    if len(new_password) < 4:
        return JSONResponse({"ok": False, "error": "密码至少 4 位"}, status_code=400)
    conn = get_db()
    try:
        conn.execute("UPDATE users SET password_hash=?, token='', updated_at=? WHERE id=?",
                     (_hash_password(new_password), now_str(), _auth["id"]))
        conn.execute("INSERT INTO password_logs (user_id, username, changed_by, created_at) VALUES (?,?,?,?)",
                     (_auth["id"], _auth["username"], "self", now_str()))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


# ---------- 生成 / 积分 ----------

def _compute_cost(model_id: str, duration: str) -> float:
    secs = 0
    try:
        secs = int(str(duration).replace("s", ""))
    except Exception:
        secs = 0
    if secs <= 0:
        return 0.0
    platform_price = 0.0
    if MODEL_CACHE["models"]:
        for m in MODEL_CACHE["models"]:
            if m["id"] == model_id:
                platform_price = _parse_platform_price(m.get("platform_price", ""))
                break
    if platform_price <= 0:
        try:
            cfg = get_config()
            key = _get_platform_key()
            r = requests.get(f"{cfg['base_url']}/v1/models", headers=api_headers(key), timeout=20)
            if r.status_code == 200:
                for m in r.json().get("data", []):
                    if m.get("id") == model_id:
                        platform_price = _parse_platform_price(m.get("price", ""))
                        break
        except Exception:
            pass
    cost = _model_cost(model_id, platform_price)
    return round(cost * secs, 1)


def _get_platform_key() -> str:
    enc = get_setting("platform_key_enc", "")
    return _decrypt_key(enc) if enc else ""


def _username_of(user_id: int) -> str:
    conn = get_db()
    try:
        row = conn.execute("SELECT username FROM users WHERE id=?", (user_id,)).fetchone()
        return row["username"] if row else ""
    finally:
        conn.close()


def _freeze_points(user_id: int, amount: float, task_id: str, note: str) -> bool:
    if amount <= 0:
        return True
    conn = get_db()
    try:
        cur = conn.execute(
            "UPDATE users SET points = points - ?, updated_at=? WHERE id=? AND points >= ? AND status='active'",
            (amount, now_str(), user_id, amount))
        if cur.rowcount == 0:
            return False
        balance = _points_balance(user_id)
        conn.execute(
            "INSERT INTO point_logs (user_id, username, type, amount, balance, task_id, note, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (user_id, _username_of(user_id), "freeze", amount, balance, task_id, note, now_str()))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        conn.close()


def _refund(user_id: int, amount: float, task_id: str, note: str) -> None:
    if amount <= 0:
        return
    conn = get_db()
    try:
        conn.execute("UPDATE users SET points = points + ?, updated_at=? WHERE id=?",
                     (amount, now_str(), user_id))
        balance = _points_balance(user_id)
        conn.execute(
            "INSERT INTO point_logs (user_id, username, type, amount, balance, task_id, note, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (user_id, _username_of(user_id), "refund", amount, balance, task_id, note, now_str()))
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        conn.close()


def _confirm_freeze(user_id: int, task_id: str) -> None:
    conn = get_db()
    try:
        row = conn.execute("SELECT cost FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row:
            conn.execute(
                "INSERT INTO point_logs (user_id, username, type, amount, balance, task_id, note, created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (user_id, _username_of(user_id), "confirm", float(row["cost"]), _points_balance(user_id),
                 task_id, "任务完成，扣费确认", now_str()))
            conn.commit()
    except Exception:
        conn.rollback()
    finally:
        conn.close()


def _set_task_failed(task_id: str, error: str) -> None:
    conn = get_db()
    try:
        conn.execute("UPDATE tasks SET status='failed', error_message=?, updated_at=? WHERE id=? OR local_id=?",
                     (error, now_str(), task_id, task_id))
        conn.commit()
    finally:
        conn.close()


def _get_task_by_id(task_id: str):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM tasks WHERE id=? OR local_id=?", (task_id, task_id)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


_USER_LOCKS = {}
_USER_LOCKS_GUARD = threading.Lock()


def _user_lock(user_id: int) -> threading.Lock:
    with _USER_LOCKS_GUARD:
        if user_id not in _USER_LOCKS:
            _USER_LOCKS[user_id] = threading.Lock()
        return _USER_LOCKS[user_id]

@app.post("/api/generate")
def generate(model: str = Form(...), prompt: str = Form(...), duration: str = Form("5s"),
             resolution: str = Form(""), ratio: str = Form(""), mode: str = Form("text"),
             materials: str = Form("[]"), first_frame: str = Form(""), last_frame: str = Form(""),
             request: Request = None,
             _auth: dict = Depends(require_user)):
    user = _auth
    cfg = get_config()
    prompt = (prompt or "").strip()
    if not model:
        return JSONResponse({"ok": False, "error": "模型不能为空"}, status_code=400)
    miss = _ref_image_exists(prompt)
    if miss:
        return JSONResponse({"ok": False, "error": f"参考图片 {miss} 已失效（实例重启可能清空上传文件），请重新上传"}, status_code=400)
    # ---------- 多模态素材解析 ----------
    mats = _parse_materials(materials)
    if mats is None:
        return JSONResponse({"ok": False, "error": "素材数据格式错误"}, status_code=400)
    for m in mats:
        miss = _ref_material_exists(m["url"])
        if miss:
            return JSONResponse({"ok": False, "error": f"参考素材 {miss} 已失效（实例重启可能清空上传文件），请重新上传"}, status_code=400)
    for f in (first_frame, last_frame):
        if f:
            miss = _ref_material_exists(f)
            if miss:
                return JSONResponse({"ok": False, "error": f"参考图片 {miss} 已失效（实例重启可能清空上传文件），请重新上传"}, status_code=400)
    cleaned_prompt, refs = _resolve_refs(prompt, mats)
    if not cleaned_prompt:
        return JSONResponse({"ok": False, "error": "提示词不能为空"}, status_code=400)
    leftover = re.search(r"@[图视音]?\d+", cleaned_prompt or "")
    if leftover:
        return JSONResponse({"ok": False, "error": f"提示词中的 {leftover.group(0)} 引用了不存在的素材，请检查编号"}, status_code=400)
    if mode == "multi" and mats and not refs:
        return JSONResponse({"ok": False, "error": "请在提示词中用 @图1/@视1/@音1 引用已上传的素材，模型才能参考它们"}, status_code=400)
    if mode == "first_last" and not first_frame and not last_frame:
        return JSONResponse({"ok": False, "error": "请至少上传首帧或尾帧图片"}, status_code=400)
    key = _get_platform_key()
    if not key:
        return JSONResponse({"ok": False, "error": "平台 Key 未配置，请联系管理员"}, status_code=400)
    if ratio and ratio not in RATIOS:
        return JSONResponse({"ok": False, "error": "不支持的比例"}, status_code=400)
    cost = _compute_cost(model, duration)
    local_id = str(uuid.uuid4())
    ts = now_str()
    with _user_lock(user["id"]):
        conn = get_db()
        try:
            running = conn.execute(
                "SELECT COUNT(*) AS c FROM tasks WHERE user_id=? AND status IN ('pending','running')",
                (user["id"],)).fetchone()["c"]
        finally:
            conn.close()
        try:
            max_conc = int(get_setting("max_concurrent", "3"))
        except Exception:
            max_conc = 3
        if running >= max_conc:
            return JSONResponse({"ok": False, "error": "并发任务数已达上限，请稍后再试"}, status_code=429)
        if not _freeze_points(user["id"], cost, local_id, "生成视频：" + model + " x " + duration):
            bal = float(user["points"])
            return JSONResponse({"ok": False, "error": "积分不足：本次需要 " + str(cost) + " 积分，当前剩余 " + str(bal) + " 积分"}, status_code=402)
        # 构造平台 assets（公网 URL）：首尾帧在前，随后按引用顺序附加参考素材
        assets = []
        if mode == "first_last":
            if first_frame:
                assets.append({"url": _abs_url(request, first_frame), "type": "image", "role": "first_frame"})
            if last_frame:
                assets.append({"url": _abs_url(request, last_frame), "type": "image", "role": "last_frame"})
        for m in refs:
            url = _abs_url(request, m["url"])
            if m["type"] == "image":
                assets.append({"url": url, "type": "image", "role": "reference"})
            elif m["type"] == "video":
                assets.append({"url": url, "type": "video", "role": "reference"})
            elif m["type"] == "audio":
                assets.append({"url": url, "type": "audio", "role": "audio"})
        assets_json = json.dumps(assets, ensure_ascii=False) if assets else ""
        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO tasks (id, local_id, user_id, username, model, prompt, duration, resolution, ratio, mode, assets_json, "
                "status, cost, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (local_id, local_id, user["id"], user["username"], model, prompt, duration, resolution, ratio, mode,
                 assets_json, "pending", cost, ts, ts))
            conn.commit()
        finally:
            conn.close()
    body = {"model": model, "prompt": cleaned_prompt}
    if duration:
        body["duration"] = duration
    if resolution:
        body["resolution"] = resolution
    if ratio:
        body["aspect_ratio"] = ratio
    if mode == "multi" and assets:
        body["referenceMode"] = "multimodal"
        body["modeType"] = "image2video" if any(a["type"] == "image" for a in assets) else "text2video"
        body["assets"] = assets
    elif mode == "first_last" and assets:
        body["referenceMode"] = "first_last_frame"
        body["modeType"] = "frames2video"
        body["assets"] = assets
    try:
        r = requests.post(f"{cfg['base_url']}/v1/video/generations",
                          headers=api_headers(key), json=body, timeout=30)
        if r.status_code != 200:
            _refund(user["id"], cost, local_id, f"平台拒绝：{r.text[:120]}")
            _set_task_failed(local_id, f"创建任务失败: {r.text[:300]}")
            return JSONResponse({"ok": False, "error": f"创建任务失败: {r.text[:300]}"}, status_code=502)
        data = r.json()
        task_id = data.get("taskId") or local_id
        conn = get_db()
        try:
            conn.execute("UPDATE tasks SET id=?, updated_at=? WHERE local_id=?",
                         (task_id, ts, local_id))
            conn.commit()
        finally:
            conn.close()
        task = _get_task_by_id(task_id)
        return {"ok": True, "task": task}
    except Exception as e:
        _refund(user["id"], cost, local_id, f"请求异常：{str(e)[:120]}")
        _set_task_failed(local_id, f"请求异常: {e}")
        return JSONResponse({"ok": False, "error": f"请求异常: {e}"}, status_code=502)


@app.get("/api/tasks")
def list_tasks(refresh: int = 0, _auth: dict = Depends(require_user)):
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM tasks WHERE user_id=? ORDER BY created_at DESC LIMIT 200",
                            (_auth["id"],)).fetchall()
    finally:
        conn.close()
    return {"ok": True, "tasks": [_task_view(r) for r in rows]}


@app.get("/api/task/{task_id}")
def get_task(task_id: str, _auth: dict = Depends(require_user)):
    t = _get_task_by_id(task_id)
    if not t or t["user_id"] != _auth["id"]:
        return JSONResponse({"ok": False, "error": "任务不存在"}, status_code=404)
    return {"ok": True, "task": _task_view(t)}


@app.get("/api/download/{task_id}")
def download_video(task_id: str, _auth: dict = Depends(require_user_media)):
    t = _get_task_by_id(task_id)
    if not t or t["user_id"] != _auth["id"]:
        return JSONResponse({"ok": False, "error": "视频不存在"}, status_code=404)
    path = Path(t["local_video"]) if t.get("local_video") else None
    if path and path.exists():
        return FileResponse(path, filename=f"{t['id']}.mp4")
    if t.get("video_url"):
        # 本地文件缺失（实例重启/清理）时退回平台地址
        return RedirectResponse(t["video_url"])
    return JSONResponse({"ok": False, "error": "视频文件已清理"}, status_code=404)


@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...), _auth: dict = Depends(require_user)):
    ext = Path(file.filename or "img.png").suffix.lower()
    ftype = _file_type(ext)
    if not ftype:
        return JSONResponse({"ok": False, "error": "仅支持图片/视频/音频素材文件 (图片: png/jpg/jpeg/webp/gif/bmp；视频: mp4/mov/webm/avi/mkv/m4v；音频: mp3/wav/m4a/aac/ogg/flac/opus)"}, status_code=400)
    name = f"{uuid.uuid4().hex}{ext}"
    dest = UPLOAD_DIR / name
    written = 0
    with dest.open("wb") as f:
        while True:
            chunk = file.file.read(1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > MAX_UPLOAD_BYTES:
                f.close()
                dest.unlink(missing_ok=True)
                return JSONResponse({"ok": False, "error": "素材文件过大，上限 100MB"}, status_code=400)
            f.write(chunk)
    return {"ok": True, "url": f"/uploads/{name}", "filename": name, "type": ftype, "size": dest.stat().st_size}


# ---------- 后台调度器：统一刷新任务 + 视频落盘 + 清理 ----------

def refresh_pending_tasks() -> None:
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM tasks WHERE status IN ('pending','running')").fetchall()
    finally:
        conn.close()
    cfg = get_config()
    for t in rows:
        try:
            ts = time.mktime(time.strptime(t["updated_at"], "%Y-%m-%d %H:%M:%S"))
        except Exception:
            ts = time.time()
        if time.time() - ts > TASK_TIMEOUT_SECONDS:
            if t["status"] in ("pending", "running"):
                _refund(t["user_id"], float(t["cost"]), t["id"], "任务超时未完成，自动退还积分")
                _set_task_failed(t["id"], "任务超时，已自动退还积分")
            continue
        user_key = _get_platform_key()
        if not user_key:
            continue
        try:
            r = requests.get(f"{cfg['base_url']}/v1/video/generations/{t['id']}",
                             headers=api_headers(user_key), timeout=15)
            if r.status_code != 200:
                continue
            d = r.json()
            status = d.get("status", t["status"])
            conn = get_db()
            try:
                conn.execute(
                    "UPDATE tasks SET status=?, video_url=?, cover_url=?, error_message=?, updated_at=? WHERE id=?",
                    (status, d.get("videoUrl") or t["video_url"], d.get("coverUrl") or t["cover_url"],
                     d.get("errorMessage") or t["error_message"], now_str(), t["id"]))
                conn.commit()
            finally:
                conn.close()
            if status in ("succeeded", "success", "completed"):
                _confirm_freeze(t["user_id"], t["id"])
                if d.get("videoUrl") and not t["local_video"]:
                    _download_local(t["id"], d["videoUrl"])
            elif status in ("failed", "error"):
                _refund(t["user_id"], float(t["cost"]), t["id"], "任务失败，自动退还积分")
        except Exception:
            continue


def _download_local(task_id: str, url: str) -> None:
    try:
        r = requests.get(url, timeout=120)
        if r.status_code == 200 and r.content:
            dest = DOWNLOADS_DIR / f"{task_id}.mp4"
            dest.write_bytes(r.content)
            conn = get_db()
            try:
                conn.execute("UPDATE tasks SET local_video=? WHERE id=?", (str(dest), task_id))
                conn.commit()
            finally:
                conn.close()
    except Exception:
        pass


def cleanup_expired_videos() -> None:
    try:
        days = int(get_setting("retention_days", "30"))
    except Exception:
        days = 30
    cutoff = time.time() - days * 86400
    conn = get_db()
    try:
        rows = conn.execute("SELECT id, keep_forever, local_video FROM tasks WHERE local_video IS NOT NULL AND local_video != ''").fetchall()
    finally:
        conn.close()
    for r in rows:
        if r["keep_forever"]:
            continue
        path = Path(r["local_video"])
        if path.exists() and path.stat().st_mtime < cutoff:
            try:
                path.unlink()
                conn = get_db()
                try:
                    conn.execute("UPDATE tasks SET local_video='' WHERE id=?", (r["id"],))
                    conn.commit()
                finally:
                    conn.close()
            except Exception:
                pass


def scheduler_loop() -> None:
    last_cleanup = 0.0
    while True:
        time.sleep(15)
        try:
            refresh_pending_tasks()
        except Exception:
            pass
        if time.time() - last_cleanup > 3600:
            try:
                cleanup_expired_videos()
            except Exception:
                pass
            last_cleanup = time.time()


@app.on_event("startup")
def start_scheduler():
    threading.Thread(target=scheduler_loop, daemon=True).start()

# ---------- 管理后台 ----------

@app.post("/api/admin/login")
def admin_login(username: str = Form(...), password: str = Form(...)):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM users WHERE username=? AND role='admin'", (username.strip(),)).fetchone()
    finally:
        conn.close()
    if not row or not _verify_password(password, row["password_hash"]):
        return JSONResponse({"ok": False, "error": "管理员账号或密码错误"}, status_code=401)
    token = secrets.token_hex(24)
    conn = get_db()
    try:
        conn.execute("UPDATE users SET token=?, updated_at=? WHERE id=?", (token, now_str(), row["id"]))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "token": token, "username": row["username"]}


@app.post("/api/admin/logout")
def admin_logout(_auth: dict = Depends(require_admin)):
    conn = get_db()
    try:
        conn.execute("UPDATE users SET token='' WHERE id=?", (_auth["id"],))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@app.get("/api/admin/session")
def admin_session(authorization: str | None = Header(None)):
    admin = _user_by_token(_extract_token(authorization), role="admin")
    if admin:
        return {"ok": True, "logged_in": True, "username": admin["username"]}
    return {"ok": True, "logged_in": False}


@app.get("/api/admin/stats")
def admin_stats(_auth: dict = Depends(require_admin)):
    conn = get_db()
    try:
        users = conn.execute("SELECT COUNT(*) AS c FROM users WHERE role='user'").fetchone()["c"]
        tasks = conn.execute("SELECT COUNT(*) AS c FROM tasks").fetchone()["c"]
        total_cost = conn.execute("SELECT COALESCE(SUM(cost),0) AS s FROM tasks").fetchone()["s"]
        banned = conn.execute("SELECT COUNT(*) AS c FROM users WHERE role='user' AND status='banned'").fetchone()["c"]
    finally:
        conn.close()
    return {"ok": True, "users": users, "tasks": tasks, "total_cost": round(total_cost, 1), "banned": banned}


@app.get("/api/admin/users")
def admin_users(_auth: dict = Depends(require_admin)):
    conn = get_db()
    try:
        rows = conn.execute("SELECT id, username, points, status, role, created_at, updated_at FROM users WHERE role='user' ORDER BY id DESC").fetchall()
    finally:
        conn.close()
    return {"ok": True, "users": [dict(r) for r in rows]}


@app.post("/api/admin/users")
def admin_create_user(username: str = Form(...), password: str = Form(...), points: str = Form("0"),
                      _auth: dict = Depends(require_admin)):
    username = (username or "").strip()
    password = (password or "").strip()
    if len(username) < 2 or len(username) > 20:
        return JSONResponse({"ok": False, "error": "账号长度需 2-20 位"}, status_code=400)
    if len(password) < 4:
        return JSONResponse({"ok": False, "error": "密码至少 4 位"}, status_code=400)
    try:
        pts = max(0.0, float(points))
    except Exception:
        pts = 0.0
    conn = get_db()
    try:
        exists = conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone()
        if exists:
            return JSONResponse({"ok": False, "error": "账号已存在"}, status_code=400)
        conn.execute(
            "INSERT INTO users (username, password_hash, points, status, role, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (username, _hash_password(password), pts, "active", "user", now_str(), now_str()))
        conn.commit()
    finally:
        conn.close()
    _audit(_auth, "create_user", f"创建账号 {username}，初始积分 {pts}")
    return {"ok": True}


@app.post("/api/admin/users/{user_id}/points")
def admin_grant_points(user_id: int, amount: str = Form(...), _auth: dict = Depends(require_admin)):
    try:
        amt = float(amount)
    except Exception:
        return JSONResponse({"ok": False, "error": "积分必须是数字"}, status_code=400)
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM users WHERE id=? AND role='user'", (user_id,)).fetchone()
        if not row:
            return JSONResponse({"ok": False, "error": "用户不存在"}, status_code=404)
        new_bal = float(row["points"]) + amt
        if new_bal < 0:
            return JSONResponse({"ok": False, "error": "积分不能扣为负数"}, status_code=400)
        conn.execute("UPDATE users SET points=?, updated_at=? WHERE id=?", (new_bal, now_str(), user_id))
        conn.execute(
            "INSERT INTO point_logs (user_id, username, type, amount, balance, note, created_at) VALUES (?,?,?,?,?,?,?)",
            (user_id, row["username"], "admin_adjust", amt, new_bal, f"管理员调整：{_auth['username']}", now_str()))
        conn.commit()
    finally:
        conn.close()
    _audit(_auth, "grant_points", f"给账号 {row['username']} 调整积分 {amt}，余额 {new_bal}")
    return {"ok": True}


@app.post("/api/admin/users/{user_id}/ban")
def admin_ban_user(user_id: int, _auth: dict = Depends(require_admin)):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM users WHERE id=? AND role='user'", (user_id,)).fetchone()
        if not row:
            return JSONResponse({"ok": False, "error": "用户不存在"}, status_code=404)
        conn.execute("UPDATE users SET status='banned', token='', updated_at=? WHERE id=?", (now_str(), user_id))
        conn.commit()
    finally:
        conn.close()
    _audit(_auth, "ban_user", f"拉黑账号 {row['username']}")
    return {"ok": True}


@app.post("/api/admin/users/{user_id}/unban")
def admin_unban_user(user_id: int, _auth: dict = Depends(require_admin)):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM users WHERE id=? AND role='user'", (user_id,)).fetchone()
        if not row:
            return JSONResponse({"ok": False, "error": "用户不存在"}, status_code=404)
        conn.execute("UPDATE users SET status='active', updated_at=? WHERE id=?", (now_str(), user_id))
        conn.commit()
    finally:
        conn.close()
    _audit(_auth, "unban_user", f"解封账号 {row['username']}")
    return {"ok": True}


@app.post("/api/admin/users/{user_id}/delete")
def admin_delete_user(user_id: int, _auth: dict = Depends(require_admin)):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM users WHERE id=? AND role='user'", (user_id,)).fetchone()
        if not row:
            return JSONResponse({"ok": False, "error": "用户不存在"}, status_code=404)
        conn.execute("DELETE FROM tasks WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM point_logs WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM password_logs WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))
        conn.commit()
    finally:
        conn.close()
    _audit(_auth, "delete_user", f"删除账号 {row['username']}")
    return {"ok": True}


@app.get("/api/admin/password_logs")
def admin_password_logs(_auth: dict = Depends(require_admin)):
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM password_logs ORDER BY id DESC LIMIT 200").fetchall()
    finally:
        conn.close()
    return {"ok": True, "logs": [dict(r) for r in rows]}

@app.get("/api/admin/tasks")
def admin_tasks(user_id: int = 0, _auth: dict = Depends(require_admin)):
    conn = get_db()
    try:
        if user_id:
            rows = conn.execute("SELECT * FROM tasks WHERE user_id=? ORDER BY created_at DESC LIMIT 500", (user_id,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM tasks ORDER BY created_at DESC LIMIT 1000").fetchall()
    finally:
        conn.close()
    return {"ok": True, "tasks": [_task_view(r, is_admin=True) for r in rows]}


@app.post("/api/admin/tasks/{task_id}/keep")
def admin_keep_task(task_id: str, keep: str = Form("1"), _auth: dict = Depends(require_admin)):
    conn = get_db()
    try:
        conn.execute("UPDATE tasks SET keep_forever=? WHERE id=?", (1 if keep == "1" else 0, task_id))
        conn.commit()
    finally:
        conn.close()
    _audit(_auth, "keep_task", f"任务 {task_id} 保留状态 -> {keep}")
    return {"ok": True}



@app.get("/api/admin/backup")
def admin_backup(_auth: dict = Depends(require_admin)):
    """一键备份：数据库 + 密钥 + 上传素材 + 本地视频，打包 zip 下载"""
    import zipfile
    buf = io.BytesIO()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for d in (DATA_DIR, UPLOAD_DIR, DOWNLOADS_DIR):
            for f in d.iterdir():
                if f.is_file():
                    z.write(f, f"{d.name}/{f.name}")
    buf.seek(0)
    return Response(content=buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="seedance-backup-{stamp}.zip"'})


@app.post("/api/admin/restore")
def admin_restore(file: UploadFile = File(...), _auth: dict = Depends(require_admin)):
    """从备份 zip 恢复（覆盖当前数据/素材/视频）"""
    import zipfile, io as _io
    try:
        data = file.file.read()
        z = zipfile.ZipFile(_io.BytesIO(data))
    except Exception:
        return JSONResponse({"ok": False, "error": "不是有效的备份文件"}, status_code=400)
    names = z.namelist()
    has_db = any(n.startswith("data/") for n in names)
    if not has_db:
        return JSONResponse({"ok": False, "error": "备份文件缺少数据库（data/）"}, status_code=400)
    # 校验路径安全
    allowed = {"data/", "uploads/", "downloads/"}
    for n in names:
        prefix = n.split("/", 1)[0] + "/"
        if prefix not in allowed or ".." in n:
            return JSONResponse({"ok": False, "error": f"备份包含非法路径: {n}"}, status_code=400)
    # 写入临时目录后整体替换（避免半途损坏）
    tmp = BASE_DIR / f".restore_tmp_{time.time()}"
    tmp.mkdir()
    try:
        for n in names:
            if n.endswith("/"):
                continue
            target = (tmp / n).parent
            target.mkdir(parents=True, exist_ok=True)
            with z.open(n) as src, (tmp / n).open("wb") as dst:
                dst.write(src.read())
        # 覆盖正式目录（备份里有密钥则一并替换，否则保留现有密钥以维持加密数据可读）
        replace_key = (tmp / "data" / ".secret_key").exists()
        for d in (DATA_DIR, UPLOAD_DIR, DOWNLOADS_DIR):
            for f in d.iterdir():
                if f.is_file() and (f.name != ".secret_key" or replace_key):
                    try:
                        f.unlink()
                    except Exception:
                        pass
        for sub in ("data", "uploads", "downloads"):
            src_dir = tmp / sub
            if src_dir.exists():
                for f in src_dir.iterdir():
                    if f.is_file():
                        shutil.copy2(f, BASE_DIR / sub / f.name)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    _audit(_auth, "restore", "从备份恢复数据")
    return {"ok": True}

@app.post("/api/admin/tasks/delete")
def admin_delete_task(task_id: str = Form(...), _auth: dict = Depends(require_admin)):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            return JSONResponse({"ok": False, "error": "任务不存在"}, status_code=404)
        path = Path(row["local_video"]) if row["local_video"] else None
        conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        conn.commit()
    finally:
        conn.close()
    if path and path.exists():
        try:
            path.unlink()
        except Exception:
            pass
    _audit(_auth, "delete_task", f"删除任务 {task_id}")
    return {"ok": True}


@app.get("/api/admin/download/{task_id}")
def admin_download(task_id: str, _auth: dict = Depends(require_admin_media)):
    t = _get_task_by_id(task_id)
    if not t:
        return JSONResponse({"ok": False, "error": "任务不存在"}, status_code=404)
    path = Path(t["local_video"]) if t.get("local_video") else None
    if path and path.exists():
        return FileResponse(path, filename=f"{t['id']}.mp4")
    if t.get("video_url"):
        return RedirectResponse(t["video_url"])
    return JSONResponse({"ok": False, "error": "本地视频文件已清理"}, status_code=404)


@app.get("/api/admin/features")
def admin_get_features(_auth: dict = Depends(require_admin)):
    return {"ok": True, "features": get_features()}


@app.post("/api/admin/features")
def admin_set_features(
    text_mode: str = Form("1"), multi_mode: str = Form("1"), first_last_mode: str = Form("1"),
    task_center: str = Form("1"), upload_enabled: str = Form("1"), enabled_models: str = Form(""),
    _auth: dict = Depends(require_admin),
):
    def _b(v: str) -> bool:
        return v.strip().lower() in ("1", "true", "on", "yes")
    models = [m.strip() for m in enabled_models.split(",") if m.strip()] if enabled_models else []
    features = {
        "text_mode": _b(text_mode), "multi_mode": _b(multi_mode),
        "first_last_mode": _b(first_last_mode), "task_center": _b(task_center),
        "upload_enabled": _b(upload_enabled), "enabled_models": models,
    }
    save_features(features)
    MODEL_CACHE["ts"] = 0.0
    _audit(_auth, "set_features", f"更新前端功能开关：{json.dumps(features, ensure_ascii=False)}")
    return {"ok": True, "features": features}


@app.get("/api/admin/models")
def admin_models(force: int = 0, _auth: dict = Depends(require_admin)):
    now = time.time()
    if not force and MODEL_CACHE["models"] and now - MODEL_CACHE["ts"] < MODEL_CACHE_TTL:
        return {"ok": True, "models": MODEL_CACHE["models"], "cached": True}
    cfg = get_config()
    key = _get_platform_key()
    if not key:
        return JSONResponse({"ok": False, "error": "请在系统设置中配置平台 Key"}, status_code=400)
    try:
        r = requests.get(f"{cfg['base_url']}/v1/models", headers=api_headers(key), timeout=25)
        if r.status_code != 200:
            return JSONResponse({"ok": False, "error": f"获取模型失败: {r.status_code} {r.text[:200]}"}, status_code=502)
        data = r.json().get("data", [])
        features = get_features()
        enabled = features.get("enabled_models") or []
        result = []
        for m in data:
            model_id = m.get("id")
            result.append({
                "id": model_id,
                "description": m.get("description", ""),
                "type": m.get("type", ""),
                "platform_price": m.get("price", ""),
                "cost_per_second": _model_cost(model_id, m.get("price")),
                "configured": _is_configured(model_id),
                "enabled": (not enabled) or (model_id in enabled),
            })
        MODEL_CACHE["models"] = result
        MODEL_CACHE["ts"] = time.time()
        return {"ok": True, "models": result, "cached": False}
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"请求异常: {e}"}, status_code=502)


@app.get("/api/admin/pricing")
def admin_get_pricing(_auth: dict = Depends(require_admin)):
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM pricing ORDER BY model_id").fetchall()
    finally:
        conn.close()
    return {"ok": True, "pricing": {r["model_id"]: r["cost_per_second"] for r in rows}}


@app.post("/api/admin/pricing")
def admin_set_pricing(model_id: str = Form(...), cost_per_second: str = Form(...),
                      _auth: dict = Depends(require_admin)):
    try:
        cost = float(cost_per_second)
    except Exception:
        return JSONResponse({"ok": False, "error": "积分必须是数字"}, status_code=400)
    if cost < 0:
        return JSONResponse({"ok": False, "error": "积分不能为负"}, status_code=400)
    conn = get_db()
    try:
        if cost == 0:
            conn.execute("DELETE FROM pricing WHERE model_id=?", (model_id.strip(),))
        else:
            conn.execute("INSERT INTO pricing (model_id, cost_per_second) VALUES (?,?) "
                         "ON CONFLICT(model_id) DO UPDATE SET cost_per_second=excluded.cost_per_second",
                         (model_id.strip(), cost))
        conn.commit()
    finally:
        conn.close()
    MODEL_CACHE["ts"] = 0.0
    _audit(_auth, "set_pricing", f"模型 {model_id} 积分/秒 -> {cost}")
    return {"ok": True}


@app.post("/api/admin/pricing/reset")
def admin_reset_pricing(_auth: dict = Depends(require_admin)):
    conn = get_db()
    try:
        conn.execute("DELETE FROM pricing")
        conn.commit()
    finally:
        conn.close()
    MODEL_CACHE["ts"] = 0.0
    _audit(_auth, "reset_pricing", "重置全部模型积分为平台默认")
    return {"ok": True}


@app.get("/api/admin/settings")
def admin_get_settings(_auth: dict = Depends(require_admin)):
    conn = get_db()
    try:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    finally:
        conn.close()
    s = {r["key"]: r["value"] for r in rows}
    if s.get("platform_key_enc"):
        pk = _decrypt_key(s["platform_key_enc"])
        s["platform_key_masked"] = _mask_key(pk)
    return {"ok": True, "settings": s}


@app.post("/api/admin/settings")
def admin_set_settings(retention_days: str = Form(""), max_concurrent: str = Form(""),
                       base_url: str = Form(""), platform_key: str = Form(""),
                       public_base_url: str = Form(""),
                       _auth: dict = Depends(require_admin)):
    if retention_days:
        try:
            v = int(retention_days)
            if v < 1:
                raise ValueError
            set_setting("retention_days", str(v))
        except Exception:
            return JSONResponse({"ok": False, "error": "保留天数必须是正整数"}, status_code=400)
    if max_concurrent:
        try:
            v = int(max_concurrent)
            if v < 1:
                raise ValueError
            set_setting("max_concurrent", str(v))
        except Exception:
            return JSONResponse({"ok": False, "error": "并发上限必须是正整数"}, status_code=400)
    if base_url:
        b = base_url.strip().rstrip("/")
        if not b.startswith(("http://", "https://")):
            return JSONResponse({"ok": False, "error": "接入地址需以 http(s):// 开头"}, status_code=400)
        set_setting("base_url", b)
    if platform_key:
        set_setting("platform_key_enc", _encrypt_key(platform_key.strip()))
        MODEL_CACHE["ts"] = 0.0
    if public_base_url:
        pb = public_base_url.strip().rstrip("/")
        if pb and not pb.startswith(("http://", "https://")):
            return JSONResponse({"ok": False, "error": "公网访问地址需以 http(s):// 开头"}, status_code=400)
        set_setting("public_base_url", pb)
    _audit(_auth, "set_settings", f"更新系统设置：retention={retention_days}, max_concurrent={max_concurrent}, base_url=已更新" if base_url else f"更新系统设置：retention={retention_days}, max_concurrent={max_concurrent}")
    return {"ok": True}


@app.post("/api/admin/password")
def admin_change_password(new_password: str = Form(...), _auth: dict = Depends(require_admin)):
    new_password = (new_password or "").strip()
    if len(new_password) < 4:
        return JSONResponse({"ok": False, "error": "密码至少 4 位"}, status_code=400)
    conn = get_db()
    try:
        conn.execute("UPDATE users SET password_hash=?, token='', updated_at=? WHERE id=?",
                     (_hash_password(new_password), now_str(), _auth["id"]))
        conn.commit()
    finally:
        conn.close()
    _audit(_auth, "change_admin_password", "管理员修改了自己的密码")
    return {"ok": True}


@app.get("/api/admin/logs")
def admin_logs(_auth: dict = Depends(require_admin)):
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT 200").fetchall()
    finally:
        conn.close()
    return {"ok": True, "logs": [dict(r) for r in rows]}


# ---------- 页面 ----------

@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/admin")
def admin_page():
    return FileResponse(STATIC_DIR / "admin.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")


init_db()

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8100)
