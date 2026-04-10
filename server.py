#!/usr/bin/env python3
"""
Henriqueta Babysitter – Servidor completo
Funcionalidades:
- Site público bonito com animações e secções
- Login com Google OAuth 2.0
- Sistema de agendamentos
- Chatbot IA (Anthropic)
- Painel admin escondido e completo
- Rate limiting
- Avaliações com aprovação
"""

import http.server
import sqlite3
import hashlib
import secrets
import json
import os
import time
import urllib.request
import urllib.parse
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlparse, urlencode
from datetime import datetime, timedelta
from collections import defaultdict

# ═══════════════════════════════════════════════════════════════
#  CONFIGURAÇÃO — edita aqui
# ═══════════════════════════════════════════════════════════════
BABYSITTER_NAME  = "Henriqueta Machava"
BABYSITTER_BIO   = ("Olá! Sou a Henriqueta, tenho 14 anos e adoro cuidar de crianças. "
                    "Tenho experiência com bebés e crianças até 10 anos, curso de "
                    "culinária e muita paciência e carinho. 💛")
BABYSITTER_PHOTO = ""
WHATSAPP_NUMBER  = "351965813670"
HOURLY_RATE      = "10€/hora"
LOCATION         = "Lisboa, Portugal"

# Admin — URL secreta (muda para algo que só tu sabes)
ADMIN_SECRET_PATH = "/gestao-hm-2024"
ADMIN_PASSWORD    = "Henriqueta2011"

# Google OAuth (preenche após criar no Google Cloud Console)
GOOGLE_CLIENT_ID     = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI  = os.environ.get("GOOGLE_REDIRECT_URI", "http://localhost:8080/auth/google/callback")

# Anthropic API para chatbot
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "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")

# Servidor
DB_PATH = os.environ.get("DB_PATH", "babysitter.db")
HOST    = "0.0.0.0"
PORT    = int(os.environ.get("PORT", 8080))

# Rate limiting
RATE_LIMIT_REQUESTS = 60   # máximo de pedidos
RATE_LIMIT_WINDOW   = 60   # por X segundos
rate_limit_store = defaultdict(list)
# ═══════════════════════════════════════════════════════════════


# ── Base de dados ─────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT,
                email      TEXT UNIQUE,
                photo      TEXT,
                provider   TEXT DEFAULT 'google',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                token      TEXT PRIMARY KEY,
                user_id    INTEGER,
                is_admin   INTEGER DEFAULT 0,
                expires_at TEXT NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS reviews (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                family     TEXT NOT NULL,
                stars      INTEGER NOT NULL CHECK(stars BETWEEN 1 AND 5),
                comment    TEXT NOT NULL,
                approved   INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS bookings (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      INTEGER,
                parent_name  TEXT NOT NULL,
                address      TEXT NOT NULL,
                child_name   TEXT NOT NULL,
                child_age    INTEGER NOT NULL,
                date         TEXT NOT NULL,
                time_slot    TEXT NOT NULL,
                hours        INTEGER NOT NULL,
                notes        TEXT,
                status       TEXT DEFAULT 'pending',
                created_at   TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS chat_escalations (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_name  TEXT,
                user_email TEXT,
                question   TEXT NOT NULL,
                assigned_to INTEGER,
                resolved   INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS staff (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT NOT NULL,
                email      TEXT,
                available  INTEGER DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        db.commit()


# ── Rate Limiting ─────────────────────────────────────────────

def is_rate_limited(ip: str) -> bool:
    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW
    rate_limit_store[ip] = [t for t in rate_limit_store[ip] if t > window_start]
    if len(rate_limit_store[ip]) >= RATE_LIMIT_REQUESTS:
        return True
    rate_limit_store[ip].append(now)
    return False


# ── Sessões ───────────────────────────────────────────────────

def create_session(user_id=None, is_admin=False) -> str:
    token = secrets.token_hex(32)
    expires = (datetime.utcnow() + timedelta(hours=8)).isoformat()
    with get_db() as db:
        db.execute("INSERT INTO sessions VALUES (?,?,?,?)",
                   (token, user_id, 1 if is_admin else 0, expires))
        db.commit()
    return token


def get_session(handler):
    cookies = SimpleCookie(handler.headers.get("Cookie", ""))
    if "sid" not in cookies:
        return None
    token = cookies["sid"].value
    with get_db() as db:
        row = db.execute(
            "SELECT s.*, u.name, u.email, u.photo FROM sessions s "
            "LEFT JOIN users u ON u.id = s.user_id "
            "WHERE s.token=? AND s.expires_at>?",
            (token, datetime.utcnow().isoformat())
        ).fetchone()
    return dict(row) if row else None


def destroy_session(handler):
    cookies = SimpleCookie(handler.headers.get("Cookie", ""))
    if "sid" in cookies:
        with get_db() as db:
            db.execute("DELETE FROM sessions WHERE token=?",
                       (cookies["sid"].value,))
            db.commit()


# ── Google OAuth ──────────────────────────────────────────────

def google_auth_url():
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
    }
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)


def google_exchange_code(code: str) -> dict:
    data = urlencode({
        "code": code,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "grant_type": "authorization_code",
    }).encode()
    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token", data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except Exception:
        return {}


def google_get_user(access_token: str) -> dict:
    req = urllib.request.Request(
        "https://www.googleapis.com/oauth2/v2/userinfo",
        headers={"Authorization": f"Bearer {access_token}"}
    )
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except Exception:
        return {}


def get_or_create_user(guser: dict) -> int:
    email = guser.get("email", "")
    with get_db() as db:
        row = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        if row:
            db.execute("UPDATE users SET name=?, photo=? WHERE email=?",
                       (guser.get("name"), guser.get("picture"), email))
            db.commit()
            return row["id"]
        cur = db.execute(
            "INSERT INTO users (name, email, photo) VALUES (?,?,?)",
            (guser.get("name"), email, guser.get("picture"))
        )
        db.commit()
        return cur.lastrowid


# ── Chatbot ───────────────────────────────────────────────────

def ask_chatbot(question: str, history: list) -> str:
    if not ANTHROPIC_API_KEY:
        return "De momento o chatbot está em manutenção. Por favor contacta-nos pelo WhatsApp! 💬"

    system = f"""És a assistente virtual da {BABYSITTER_NAME}, babysitter em {LOCATION}.
Respondes em português europeu, de forma simpática e profissional.
Informações:
- Preço: {HOURLY_RATE}
- Localização: {LOCATION}
- Serviços: cuidado de bebés (0-2 anos), recreação, pernoite, refeições saudáveis
- Contacto: WhatsApp +{WHATSAPP_NUMBER}
- Agendamento: disponível no site na secção "Agendar"

Se não conseguires responder, diz: "ESCALAR: [motivo]" para encaminhar para um funcionário."""

    messages = history[-6:] + [{"role": "user", "content": question}]

    data = json.dumps({
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 400,
        "system": system,
        "messages": messages
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=data,
        headers={
            "Content-Type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01"
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            result = json.loads(r.read())
            return result["content"][0]["text"]
    except Exception as e:
        return "Não consegui responder agora. Fala connosco pelo WhatsApp! 💬"


# ── Helpers ───────────────────────────────────────────────────

def read_body(handler) -> dict:
    length = int(handler.headers.get("Content-Length", 0))
    return parse_qs(handler.rfile.read(length).decode())


def read_json(handler) -> dict:
    length = int(handler.headers.get("Content-Length", 0))
    try:
        return json.loads(handler.rfile.read(length).decode())
    except Exception:
        return {}


def field(data, name):
    return data.get(name, [""])[0].strip()


def stars_html(n):
    return "★" * int(n) + "☆" * (5 - int(n))


def avg_stars(reviews):
    if not reviews:
        return 0
    return sum(r["stars"] for r in reviews) / len(reviews)


# ══════════════════════════════════════════════════════════════
#  CSS & JS
# ══════════════════════════════════════════════════════════════

STYLE = """
@import url('https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;600;700&family=DM+Sans:wght@300;400;500;600&display=swap');

:root {
  --pink:     #f472b6;
  --pink-d:   #db2777;
  --pink-l:   #fce7f3;
  --cream:    #fffbf5;
  --dark:     #1a1a2e;
  --gray:     #6b7280;
  --light:    #f9fafb;
  --white:    #ffffff;
  --shadow:   0 4px 24px rgba(244,114,182,.15);
  --radius:   16px;
  --transition: all .3s cubic-bezier(.4,0,.2,1);
}

* { box-sizing: border-box; margin: 0; padding: 0; }
html { scroll-behavior: smooth; }
body { font-family: 'DM Sans', sans-serif; background: var(--cream);
       color: var(--dark); overflow-x: hidden; }

/* ── SCROLLBAR ── */
::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: var(--cream); }
::-webkit-scrollbar-thumb { background: var(--pink); border-radius: 99px; }

/* ── NAV ── */
nav {
  position: fixed; top: 0; left: 0; right: 0; z-index: 100;
  padding: 1rem 2rem;
  display: flex; justify-content: space-between; align-items: center;
  background: rgba(255,251,245,.85);
  backdrop-filter: blur(20px);
  border-bottom: 1px solid rgba(244,114,182,.15);
  transition: var(--transition);
}
nav.scrolled { box-shadow: 0 2px 20px rgba(244,114,182,.1); }
.nav-brand { font-family: 'Playfair Display', serif;
             font-size: 1.3rem; color: var(--pink-d);
             text-decoration: none; font-weight: 700; }
.nav-links { display: flex; gap: 2rem; align-items: center; }
.nav-links a { color: var(--dark); text-decoration: none;
               font-size: .9rem; font-weight: 500; opacity: .7;
               transition: var(--transition); position: relative; }
.nav-links a::after { content: ''; position: absolute; bottom: -2px;
                      left: 0; width: 0; height: 2px;
                      background: var(--pink); transition: var(--transition); }
.nav-links a:hover { opacity: 1; }
.nav-links a:hover::after { width: 100%; }
.nav-avatar { width: 36px; height: 36px; border-radius: 50%;
              object-fit: cover; border: 2px solid var(--pink); }

/* ── HERO ── */
.hero {
  min-height: 100vh;
  display: flex; flex-direction: column;
  justify-content: center; align-items: center;
  text-align: center; padding: 6rem 2rem 4rem;
  position: relative; overflow: hidden;
}
.hero-bg {
  position: absolute; inset: 0; z-index: 0;
  background: radial-gradient(ellipse 80% 60% at 50% 0%, #fce7f3 0%, transparent 70%),
              radial-gradient(ellipse 40% 40% at 80% 80%, #fff0f7 0%, transparent 60%);
}
.hero-blob {
  position: absolute; border-radius: 50%; filter: blur(60px); opacity: .4;
  animation: float 8s ease-in-out infinite;
}
.blob1 { width: 400px; height: 400px; background: #f9a8d4;
         top: -100px; right: -100px; animation-delay: 0s; }
.blob2 { width: 300px; height: 300px; background: #fbcfe8;
         bottom: -50px; left: -80px; animation-delay: 3s; }
@keyframes float {
  0%,100% { transform: translateY(0) scale(1); }
  50%      { transform: translateY(-30px) scale(1.05); }
}
.hero-content { position: relative; z-index: 1; }
.hero-photo-wrap {
  width: 160px; height: 160px; margin: 0 auto 1.5rem;
  border-radius: 50%; padding: 4px;
  background: linear-gradient(135deg, var(--pink), var(--pink-d));
  box-shadow: 0 8px 32px rgba(244,114,182,.4);
  animation: fadeUp .8s ease both;
}
.hero-photo { width: 100%; height: 100%; border-radius: 50%;
              object-fit: cover; background: var(--pink-l);
              display: flex; align-items: center; justify-content: center;
              font-size: 5rem; }
.hero h1 {
  font-family: 'Playfair Display', serif;
  font-size: clamp(2.5rem, 6vw, 4rem);
  font-weight: 700; line-height: 1.1;
  animation: fadeUp .8s .1s ease both;
}
.hero h1 span { color: var(--pink-d); }
.hero-sub { font-size: 1.1rem; color: var(--gray); margin: 1rem 0 2rem;
            animation: fadeUp .8s .2s ease both; }
.hero-badges { display: flex; flex-wrap: wrap; gap: .6rem;
               justify-content: center; margin-bottom: 2rem;
               animation: fadeUp .8s .3s ease both; }
.badge {
  background: var(--white); border: 1px solid rgba(244,114,182,.3);
  color: var(--dark); border-radius: 99px;
  padding: .4rem 1rem; font-size: .82rem; font-weight: 500;
  box-shadow: 0 2px 8px rgba(244,114,182,.1);
}
.hero-cta { display: flex; gap: 1rem; justify-content: center;
            flex-wrap: wrap; animation: fadeUp .8s .4s ease both; }

/* ── BOTÕES ── */
.btn {
  display: inline-flex; align-items: center; gap: .5rem;
  padding: .85rem 2rem; border-radius: 99px; font-size: .95rem;
  font-weight: 600; text-decoration: none; border: none; cursor: pointer;
  transition: var(--transition); font-family: 'DM Sans', sans-serif;
}
.btn:hover { transform: translateY(-3px); box-shadow: 0 8px 24px rgba(0,0,0,.15); }
.btn:active { transform: translateY(-1px); }
.btn-primary { background: linear-gradient(135deg, var(--pink), var(--pink-d));
               color: #fff; box-shadow: var(--shadow); }
.btn-primary:hover { box-shadow: 0 12px 32px rgba(244,114,182,.45); }
.btn-whatsapp { background: #25d366; color: #fff; }
.btn-whatsapp:hover { background: #1ebe5d; box-shadow: 0 8px 24px rgba(37,211,102,.35); }
.btn-outline { background: transparent; color: var(--pink-d);
               border: 2px solid var(--pink); }
.btn-outline:hover { background: var(--pink-l); }
.btn-google { background: var(--white); color: var(--dark);
              border: 1.5px solid #e5e7eb; box-shadow: 0 2px 8px rgba(0,0,0,.08); }
.btn-google:hover { box-shadow: 0 6px 20px rgba(0,0,0,.12); }
.btn-danger { background: #fee2e2; color: #991b1b; }
.btn-danger:hover { background: #fecaca; }
.btn-success { background: #d1fae5; color: #065f46; }
.btn-success:hover { background: #a7f3d0; }
.btn-sm { padding: .45rem 1rem; font-size: .82rem; border-radius: 8px; }

/* ── SECÇÕES ── */
.section {
  padding: 5rem 2rem; max-width: 1100px; margin: 0 auto;
}
.section-label {
  font-size: .8rem; font-weight: 600; letter-spacing: .15em;
  color: var(--pink); text-transform: uppercase; margin-bottom: .5rem;
}
.section-title {
  font-family: 'Playfair Display', serif;
  font-size: clamp(1.8rem, 4vw, 2.5rem);
  font-weight: 700; margin-bottom: 1rem;
}
.section-sub { color: var(--gray); max-width: 560px; line-height: 1.7; }
.section-header { margin-bottom: 3rem; }

/* ── DIVIDER ── */
.wave-divider { line-height: 0; overflow: hidden; }
.wave-divider svg { display: block; width: 100%; }

/* ── SERVIÇOS ── */
#servicos { background: var(--white); }
.services-grid {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 1.5rem;
}
.service-card {
  background: var(--cream); border-radius: var(--radius);
  padding: 2rem 1.5rem; text-align: center;
  border: 1px solid rgba(244,114,182,.1);
  transition: var(--transition); cursor: default;
}
.service-card:hover {
  transform: translateY(-6px);
  box-shadow: 0 16px 40px rgba(244,114,182,.15);
  border-color: var(--pink);
}
.service-icon { font-size: 2.5rem; margin-bottom: 1rem; }
.service-card h3 { font-weight: 600; margin-bottom: .5rem; }
.service-card p { font-size: .88rem; color: var(--gray); line-height: 1.6; }

/* ── AGENDAR ── */
#agendar { background: var(--cream); }
.booking-form {
  background: var(--white); border-radius: var(--radius);
  padding: 2.5rem; box-shadow: 0 4px 32px rgba(244,114,182,.1);
  max-width: 640px;
}
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
.form-full { grid-column: 1/-1; }
label { display: block; font-size: .85rem; font-weight: 600;
        margin-bottom: .4rem; color: var(--dark); }
input, select, textarea {
  width: 100%; padding: .75rem 1rem; border-radius: 10px;
  border: 1.5px solid #e5e7eb; font-size: .95rem;
  font-family: 'DM Sans', sans-serif; background: var(--cream);
  transition: var(--transition); color: var(--dark);
}
input:focus, select:focus, textarea:focus {
  outline: none; border-color: var(--pink);
  box-shadow: 0 0 0 3px rgba(244,114,182,.15);
}
textarea { resize: vertical; min-height: 100px; }

/* ── AVALIAÇÕES ── */
#avaliacoes { background: var(--white); }
.reviews-grid {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 1.5rem; margin-bottom: 3rem;
}
.review-card {
  background: var(--cream); border-radius: var(--radius);
  padding: 1.5rem; border: 1px solid rgba(244,114,182,.1);
  transition: var(--transition);
}
.review-card:hover { transform: translateY(-4px);
                     box-shadow: 0 12px 32px rgba(244,114,182,.12); }
.review-stars { color: #f59e0b; font-size: 1.1rem; margin-bottom: .5rem; }
.review-name { font-weight: 600; margin-bottom: .5rem; }
.review-text { font-size: .9rem; color: var(--gray); line-height: 1.6; }
.review-date { font-size: .75rem; color: #d1d5db; margin-top: .75rem; }

/* ── CHATBOT ── */
#chatbot { background: var(--cream); }
.chat-widget {
  max-width: 560px; background: var(--white);
  border-radius: var(--radius); overflow: hidden;
  box-shadow: 0 4px 32px rgba(244,114,182,.12);
}
.chat-header {
  background: linear-gradient(135deg, var(--pink), var(--pink-d));
  padding: 1.2rem 1.5rem; display: flex; align-items: center; gap: .75rem;
}
.chat-header-avatar { font-size: 1.8rem; }
.chat-header h3 { color: #fff; font-size: 1rem; }
.chat-header p { color: rgba(255,255,255,.8); font-size: .8rem; }
.chat-messages {
  height: 320px; overflow-y: auto; padding: 1.2rem;
  display: flex; flex-direction: column; gap: .75rem;
}
.chat-msg { max-width: 80%; padding: .7rem 1rem;
            border-radius: 16px; font-size: .9rem; line-height: 1.5; }
.chat-msg.bot { background: var(--pink-l); color: var(--dark);
                border-bottom-left-radius: 4px; align-self: flex-start; }
.chat-msg.user { background: linear-gradient(135deg, var(--pink), var(--pink-d));
                 color: #fff; border-bottom-right-radius: 4px;
                 align-self: flex-end; }
.chat-msg.typing { opacity: .6; font-style: italic; }
.chat-input-row {
  display: flex; gap: .75rem; padding: 1rem 1.2rem;
  border-top: 1px solid #f3f4f6;
}
.chat-input-row input { flex: 1; border-radius: 99px; padding: .65rem 1.2rem; }
.chat-input-row button {
  background: linear-gradient(135deg, var(--pink), var(--pink-d));
  color: #fff; border: none; border-radius: 99px;
  padding: .65rem 1.2rem; cursor: pointer; font-size: .9rem;
  transition: var(--transition);
}
.chat-input-row button:hover { transform: scale(1.05); }

/* ── MENSAGENS ── */
.msg { padding: .85rem 1.2rem; border-radius: 10px;
       margin-bottom: 1.2rem; font-size: .9rem; }
.msg-ok  { background: #d1fae5; color: #065f46; }
.msg-err { background: #fee2e2; color: #991b1b; }
.msg-info { background: var(--pink-l); color: var(--pink-d); }

/* ── ADMIN ── */
.admin-layout { display: flex; min-height: 100vh; }
.admin-sidebar {
  width: 240px; background: var(--dark); padding: 2rem 0;
  position: fixed; top: 0; bottom: 0; overflow-y: auto;
  display: flex; flex-direction: column;
}
.admin-logo { padding: 0 1.5rem 2rem;
              font-family: 'Playfair Display', serif;
              color: var(--pink); font-size: 1.1rem; font-weight: 700; }
.admin-nav a {
  display: flex; align-items: center; gap: .75rem;
  padding: .85rem 1.5rem; color: rgba(255,255,255,.6);
  text-decoration: none; font-size: .9rem; transition: var(--transition);
}
.admin-nav a:hover, .admin-nav a.active {
  background: rgba(244,114,182,.15); color: #fff;
  border-left: 3px solid var(--pink);
}
.admin-main { margin-left: 240px; padding: 2rem; flex: 1; background: #f8fafc; }
.admin-header { margin-bottom: 2rem; }
.admin-header h1 { font-family: 'Playfair Display', serif;
                   font-size: 1.6rem; color: var(--dark); }
.stats-grid { display: grid;
              grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
              gap: 1.2rem; margin-bottom: 2rem; }
.stat-card {
  background: var(--white); border-radius: var(--radius);
  padding: 1.5rem; border-left: 4px solid var(--pink);
  box-shadow: 0 2px 12px rgba(0,0,0,.05);
}
.stat-card .stat-value { font-size: 2rem; font-weight: 700; color: var(--pink-d); }
.stat-card .stat-label { font-size: .82rem; color: var(--gray); margin-top: .25rem; }
.admin-table { width: 100%; border-collapse: collapse;
               background: var(--white); border-radius: var(--radius);
               overflow: hidden; box-shadow: 0 2px 12px rgba(0,0,0,.05); }
.admin-table th { background: #f1f5f9; padding: .85rem 1rem;
                  text-align: left; font-size: .82rem;
                  color: var(--gray); font-weight: 600;
                  text-transform: uppercase; letter-spacing: .05em; }
.admin-table td { padding: .85rem 1rem; border-top: 1px solid #f1f5f9;
                  font-size: .88rem; vertical-align: middle; }
.admin-table tr:hover td { background: #fafafa; }
.status-badge {
  display: inline-block; padding: .25rem .75rem;
  border-radius: 99px; font-size: .75rem; font-weight: 600;
}
.status-pending  { background: #fef3c7; color: #92400e; }
.status-accepted { background: #d1fae5; color: #065f46; }
.status-rejected { background: #fee2e2; color: #991b1b; }
.status-resolved { background: #dbeafe; color: #1e40af; }

/* ── LOGIN ── */
.login-page {
  min-height: 100vh; display: flex; align-items: center;
  justify-content: center; background: var(--cream);
  padding: 2rem;
}
.login-card {
  background: var(--white); border-radius: var(--radius);
  padding: 3rem; max-width: 420px; width: 100%;
  box-shadow: 0 8px 40px rgba(244,114,182,.12);
  text-align: center;
}
.login-card h1 { font-family: 'Playfair Display', serif;
                 font-size: 1.8rem; margin-bottom: .5rem; }
.login-card p { color: var(--gray); margin-bottom: 2rem; font-size: .9rem; }
.divider { display: flex; align-items: center; gap: 1rem;
           color: #d1d5db; font-size: .82rem; margin: 1.5rem 0; }
.divider::before, .divider::after {
  content: ''; flex: 1; height: 1px; background: #e5e7eb;
}

/* ── FOOTER ── */
footer {
  background: var(--dark); color: rgba(255,255,255,.6);
  text-align: center; padding: 2rem;
  font-size: .85rem;
}
footer a { color: var(--pink); text-decoration: none; }

/* ── ANIMAÇÕES ── */
@keyframes fadeUp {
  from { opacity: 0; transform: translateY(24px); }
  to   { opacity: 1; transform: translateY(0); }
}
.fade-up { animation: fadeUp .7s ease both; }
.fade-up-1 { animation-delay: .1s; }
.fade-up-2 { animation-delay: .2s; }
.fade-up-3 { animation-delay: .3s; }
.fade-up-4 { animation-delay: .4s; }

/* ── RESPONSIVE ── */
@media (max-width: 768px) {
  .nav-links { gap: 1rem; }
  .nav-links a:not(.btn) { display: none; }
  .form-grid { grid-template-columns: 1fr; }
  .admin-sidebar { display: none; }
  .admin-main { margin-left: 0; }
  .hero h1 { font-size: 2.2rem; }
}
"""

SCRIPT = """
// Nav scroll
window.addEventListener('scroll', () => {
  document.querySelector('nav')?.classList.toggle('scrolled', scrollY > 20);
});

// Intersection observer para animações
const obs = new IntersectionObserver(entries => {
  entries.forEach(e => {
    if (e.isIntersecting) { e.target.classList.add('visible'); }
  });
}, { threshold: .1 });
document.querySelectorAll('.service-card, .review-card, .stat-card')
  .forEach(el => obs.observe(el));

// Chatbot
const chatMessages = document.getElementById('chat-messages');
const chatInput    = document.getElementById('chat-input');
let chatHistory = [];

function appendMsg(text, role) {
  const div = document.createElement('div');
  div.className = 'chat-msg ' + role;
  div.textContent = text;
  chatMessages?.appendChild(div);
  chatMessages?.scrollTo(0, chatMessages.scrollHeight);
  return div;
}

async function sendChat() {
  const text = chatInput?.value?.trim();
  if (!text) return;
  chatInput.value = '';
  appendMsg(text, 'user');
  chatHistory.push({ role: 'user', content: text });
  const typing = appendMsg('A escrever...', 'bot typing');
  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text, history: chatHistory.slice(-6) })
    });
    const data = await res.json();
    typing.remove();
    appendMsg(data.reply, 'bot');
    chatHistory.push({ role: 'assistant', content: data.reply });
  } catch {
    typing.textContent = 'Erro ao responder. Tenta novamente!';
    typing.classList.remove('typing');
  }
}

document.getElementById('chat-send')?.addEventListener('click', sendChat);
document.getElementById('chat-input')?.addEventListener('keydown', e => {
  if (e.key === 'Enter') sendChat();
});
"""


# ══════════════════════════════════════════════════════════════
#  TEMPLATES
# ══════════════════════════════════════════════════════════════

def layout(title, body, session=None, include_chat_js=False):
    user = session
    nav_user = ""
    if user and user.get("name"):
        photo = user.get("photo", "")
        if photo:
            nav_user = f'<img class="nav-avatar" src="{photo}" alt="">'
        else:
            nav_user = f'<span style="font-weight:600;font-size:.9rem">{user["name"].split()[0]}</span>'
        nav_user += ' <a href="/logout" class="btn btn-outline" style="padding:.4rem 1rem;font-size:.82rem">Sair</a>'
    else:
        nav_user = '<a href="/login" class="btn btn-primary" style="padding:.5rem 1.2rem;font-size:.85rem">Entrar</a>'

    return f"""<!DOCTYPE html>
<html lang="pt-PT">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{title}</title>
  <style>{STYLE}</style>
</head>
<body>
<nav id="nav">
  <a class="nav-brand" href="/">🍼 {BABYSITTER_NAME.split()[0]}</a>
  <div class="nav-links">
    <a href="/#servicos">Serviços</a>
    <a href="/#agendar">Agendar</a>
    <a href="/#avaliacoes">Avaliações</a>
    <a href="/#chatbot">Ajuda</a>
    {nav_user}
  </div>
</nav>
{body}
<footer>
  <p>© 2024 {BABYSITTER_NAME} · Lisboa, Portugal ·
  <a href="https://wa.me/{WHATSAPP_NUMBER}">WhatsApp</a></p>
</footer>
<script>{SCRIPT}</script>
</body>
</html>"""


def page_home(session=None, booking_msg="", booking_type="ok", review_msg="", review_type="ok"):
    with get_db() as db:
        reviews = [dict(r) for r in db.execute(
            "SELECT * FROM reviews WHERE approved=1 ORDER BY id DESC LIMIT 9"
        ).fetchall()]

    avg = avg_stars(reviews)
    avg_txt = f"{avg:.1f} ★" if reviews else "Sem avaliações"

    photo_html = (f'<img src="{BABYSITTER_PHOTO}" alt="Foto" style="width:100%;height:100%;border-radius:50%;object-fit:cover">'
                  if BABYSITTER_PHOTO else '<div class="hero-photo">👩</div>')

    wa_url = f"https://wa.me/{WHATSAPP_NUMBER}?text=Olá%20{BABYSITTER_NAME.split()[0]}!%20Vi%20o%20teu%20site."

    # Formulário de agendamento
    user_fields = ""
    if session and session.get("name"):
        user_fields = f"""
        <input type="hidden" name="user_id" value="{session.get('user_id','')}">"""

    booking_msg_html = f'<div class="msg msg-{booking_type}">{booking_msg}</div>' if booking_msg else ""
    review_msg_html  = f'<div class="msg msg-{review_type}">{review_msg}</div>' if review_msg else ""

    reviews_html = "".join(f"""
    <div class="review-card fade-up">
      <div class="review-stars">{stars_html(r['stars'])}</div>
      <div class="review-name">{r['family']}</div>
      <div class="review-text">{r['comment']}</div>
      <div class="review-date">{r['created_at'][:10]}</div>
    </div>""" for r in reviews) or \
        "<p style='color:var(--gray)'>Sê a primeira família a avaliar! 😊</p>"

    return layout(f"{BABYSITTER_NAME} – Babysitter em Lisboa", f"""

<!-- HERO -->
<section class="hero" id="inicio">
  <div class="hero-bg"></div>
  <div class="hero-blob blob1"></div>
  <div class="hero-blob blob2"></div>
  <div class="hero-content">
    <div class="hero-photo-wrap">{photo_html}</div>
    <h1 class="fade-up">A melhor babysitter<br><span>de Lisboa</span></h1>
    <p class="hero-sub fade-up fade-up-1">
      📍 {LOCATION} &nbsp;·&nbsp; 💰 {HOURLY_RATE} &nbsp;·&nbsp; ⭐ {avg_txt}
    </p>
    <div class="hero-badges fade-up fade-up-2">
      <span class="badge">🍳 Curso de Culinária</span>
      <span class="badge">🍼 Bebés & Crianças</span>
      <span class="badge">💛 Muito Carinho</span>
      <span class="badge">✅ Referenciada</span>
    </div>
    <div class="hero-cta fade-up fade-up-3">
      <a href="/#agendar" class="btn btn-primary">📅 Agendar Agora</a>
      <a href="{wa_url}" target="_blank" class="btn btn-whatsapp">💬 WhatsApp</a>
    </div>
  </div>
</section>

<!-- SOBRE -->
<div class="wave-divider">
  <svg viewBox="0 0 1440 60" xmlns="http://www.w3.org/2000/svg">
    <path d="M0,30 C360,60 1080,0 1440,30 L1440,60 L0,60 Z" fill="#ffffff"/>
  </svg>
</div>

<section id="sobre" style="background:var(--white);padding:4rem 2rem">
  <div style="max-width:700px;margin:0 auto;text-align:center">
    <div class="section-label">Sobre mim</div>
    <h2 class="section-title">{BABYSITTER_NAME}</h2>
    <p style="color:var(--gray);line-height:1.8;font-size:1.05rem">{BABYSITTER_BIO}</p>
  </div>
</section>

<!-- SERVIÇOS -->
<div class="wave-divider">
  <svg viewBox="0 0 1440 60" xmlns="http://www.w3.org/2000/svg">
    <path d="M0,30 C360,0 1080,60 1440,30 L1440,60 L0,60 Z" fill="var(--cream)"/>
  </svg>
</div>

<section id="servicos" style="background:var(--cream);padding:5rem 2rem">
  <div style="max-width:1000px;margin:0 auto">
    <div class="section-header">
      <div class="section-label">O que ofereço</div>
      <h2 class="section-title">Serviços</h2>
    </div>
    <div class="services-grid">
      <div class="service-card">
        <div class="service-icon">🍼</div>
        <h3>Bebés</h3>
        <p>Cuidados especializados para bebés de 0 a 2 anos com todo o carinho</p>
      </div>
      <div class="service-card">
        <div class="service-icon">🎨</div>
        <h3>Recreação</h3>
        <p>Atividades educativas e brincadeiras criativas para o desenvolvimento</p>
      </div>
      <div class="service-card">
        <div class="service-icon">🍳</div>
        <h3>Refeições</h3>
        <p>Preparação de refeições saudáveis e equilibradas para as crianças</p>
      </div>
      <div class="service-card">
        <div class="service-icon">🌙</div>
        <h3>Pernoite</h3>
        <p>Disponível para cuidados noturnos com toda a segurança</p>
      </div>
      <div class="service-card">
        <div class="service-icon">📚</div>
        <h3>Apoio Escolar</h3>
        <p>Ajuda com os trabalhos de casa e leitura para crianças em idade escolar</p>
      </div>
      <div class="service-card">
        <div class="service-icon">🚶</div>
        <h3>Passeios</h3>
        <p>Acompanhamento em passeios e atividades ao ar livre em Lisboa</p>
      </div>
    </div>
  </div>
</section>

<!-- AGENDAR -->
<div class="wave-divider">
  <svg viewBox="0 0 1440 60" xmlns="http://www.w3.org/2000/svg">
    <path d="M0,30 C360,60 1080,0 1440,30 L1440,60 L0,60 Z" fill="#ffffff"/>
  </svg>
</div>

<section id="agendar" style="background:var(--white);padding:5rem 2rem">
  <div style="max-width:700px;margin:0 auto">
    <div class="section-header">
      <div class="section-label">Marcação</div>
      <h2 class="section-title">Agendar Serviço</h2>
      <p class="section-sub">Preenche o formulário e entrarei em contacto para confirmar.</p>
    </div>
    <div class="booking-form">
      {booking_msg_html}
      <form method="POST" action="/agendar">
        {user_fields}
        <div class="form-grid">
          <div>
            <label>O teu nome *</label>
            <input type="text" name="parent_name" placeholder="Maria Silva" required maxlength="100">
          </div>
          <div>
            <label>Morada *</label>
            <input type="text" name="address" placeholder="Rua exemplo, Lisboa" required maxlength="200">
          </div>
          <div>
            <label>Nome da criança *</label>
            <input type="text" name="child_name" placeholder="João" required maxlength="100">
          </div>
          <div>
            <label>Idade da criança *</label>
            <input type="number" name="child_age" placeholder="3" min="0" max="12" required>
          </div>
          <div>
            <label>Data *</label>
            <input type="date" name="date" required>
          </div>
          <div>
            <label>Hora *</label>
            <select name="time_slot" required>
              <option value="">Seleciona</option>
              <option>08:00 - 10:00</option>
              <option>10:00 - 12:00</option>
              <option>14:00 - 16:00</option>
              <option>16:00 - 18:00</option>
              <option>18:00 - 20:00</option>
              <option>20:00 - 22:00</option>
            </select>
          </div>
          <div>
            <label>Número de horas *</label>
            <input type="number" name="hours" placeholder="2" min="1" max="12" required>
          </div>
          <div class="form-full">
            <label>Notas adicionais</label>
            <textarea name="notes" placeholder="Alergias, necessidades especiais..."></textarea>
          </div>
        </div>
        <button class="btn btn-primary" type="submit" style="width:100%;margin-top:.5rem">
          📅 Enviar Pedido de Agendamento
        </button>
      </form>
    </div>
  </div>
</section>

<!-- AVALIAÇÕES -->
<div class="wave-divider">
  <svg viewBox="0 0 1440 60" xmlns="http://www.w3.org/2000/svg">
    <path d="M0,30 C360,0 1080,60 1440,30 L1440,60 L0,60 Z" fill="var(--cream)"/>
  </svg>
</div>

<section id="avaliacoes" style="background:var(--cream);padding:5rem 2rem">
  <div style="max-width:1000px;margin:0 auto">
    <div class="section-header">
      <div class="section-label">Depoimentos</div>
      <h2 class="section-title">O que dizem as famílias</h2>
    </div>
    <div class="reviews-grid">{reviews_html}</div>
    <div style="background:var(--white);border-radius:var(--radius);padding:2rem;
                box-shadow:0 4px 24px rgba(244,114,182,.08)">
      <h3 style="margin-bottom:1.2rem;font-family:'Playfair Display',serif">
        Deixa a tua avaliação 💛
      </h3>
      {review_msg_html}
      <form method="POST" action="/review">
        <div class="form-grid">
          <div>
            <label>O teu nome / família *</label>
            <input type="text" name="family" placeholder="Família Silva" required maxlength="80">
          </div>
          <div>
            <label>Nota *</label>
            <select name="stars" required>
              <option value="">Seleciona</option>
              <option value="5">★★★★★ Excelente</option>
              <option value="4">★★★★☆ Muito bom</option>
              <option value="3">★★★☆☆ Bom</option>
              <option value="2">★★☆☆☆ Regular</option>
              <option value="1">★☆☆☆☆ Mau</option>
            </select>
          </div>
          <div class="form-full">
            <label>Comentário *</label>
            <textarea name="comment" placeholder="Conta a tua experiência..." required maxlength="500"></textarea>
          </div>
        </div>
        <button class="btn btn-primary" type="submit">Enviar Avaliação</button>
      </form>
    </div>
  </div>
</section>

<!-- CHATBOT -->
<div class="wave-divider">
  <svg viewBox="0 0 1440 60" xmlns="http://www.w3.org/2000/svg">
    <path d="M0,30 C360,60 1080,0 1440,30 L1440,60 L0,60 Z" fill="#ffffff"/>
  </svg>
</div>

<section id="chatbot" style="background:var(--white);padding:5rem 2rem">
  <div style="max-width:600px;margin:0 auto">
    <div class="section-header">
      <div class="section-label">Assistente Virtual</div>
      <h2 class="section-title">Tens dúvidas? Pergunta-me! 🤖</h2>
      <p class="section-sub">A minha assistente virtual responde em segundos. Se não conseguir ajudar, chama um funcionário.</p>
    </div>
    <div class="chat-widget">
      <div class="chat-header">
        <div class="chat-header-avatar">🤖</div>
        <div>
          <h3>Assistente da Henriqueta</h3>
          <p>Online agora · Responde em segundos</p>
        </div>
      </div>
      <div class="chat-messages" id="chat-messages">
        <div class="chat-msg bot">
          Olá! 👋 Sou a assistente virtual da Henriqueta. Como posso ajudar-te hoje?
        </div>
      </div>
      <div class="chat-input-row">
        <input id="chat-input" type="text" placeholder="Escreve a tua pergunta...">
        <button id="chat-send">Enviar</button>
      </div>
    </div>
  </div>
</section>

""", session)


def page_login(error=""):
    msg = f'<div class="msg msg-err">{error}</div>' if error else ""
    google_btn = ""
    if GOOGLE_CLIENT_ID:
        google_btn = f"""
        <a href="/auth/google" class="btn btn-google" style="width:100%;justify-content:center">
          <svg width="18" height="18" viewBox="0 0 24 24">
            <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
            <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
            <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/>
            <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
          </svg>
          Entrar com Google
        </a>
        <div class="divider">ou</div>"""

    return f"""<!DOCTYPE html>
<html lang="pt-PT">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Entrar – {BABYSITTER_NAME}</title>
  <style>{STYLE}</style>
</head>
<body>
<div class="login-page">
  <div class="login-card">
    <div style="font-size:3rem;margin-bottom:1rem">🍼</div>
    <h1>{BABYSITTER_NAME.split()[0]}</h1>
    <p>Entra para gerir os teus agendamentos</p>
    {msg}
    {google_btn}
    <form method="POST" action="/login">
      <input type="email" name="email" placeholder="O teu email" required style="margin-bottom:.8rem">
      <input type="password" name="password" placeholder="Senha" required style="margin-bottom:1rem">
      <button class="btn btn-primary" type="submit" style="width:100%;justify-content:center">
        Entrar
      </button>
    </form>
    <p style="margin-top:1.5rem;font-size:.82rem;color:var(--gray)">
      <a href="/" style="color:var(--pink)">← Voltar ao site</a>
    </p>
  </div>
</div>
<script>{SCRIPT}</script>
</body>
</html>"""


def page_admin(section="dashboard", msg="", msg_type="ok"):
    with get_db() as db:
        bookings    = [dict(r) for r in db.execute("SELECT * FROM bookings ORDER BY id DESC").fetchall()]
        reviews     = [dict(r) for r in db.execute("SELECT * FROM reviews ORDER BY approved ASC, id DESC").fetchall()]
        escalations = [dict(r) for r in db.execute("SELECT * FROM chat_escalations ORDER BY resolved ASC, id DESC").fetchall()]
        staff_list  = [dict(r) for r in db.execute("SELECT * FROM staff ORDER BY id").fetchall()]
        users       = [dict(r) for r in db.execute("SELECT * FROM users ORDER BY id DESC").fetchall()]

    pending_bookings  = sum(1 for b in bookings if b["status"] == "pending")
    pending_reviews   = sum(1 for r in reviews if not r["approved"])
    open_escalations  = sum(1 for e in escalations if not e["resolved"])

    msg_html = f'<div class="msg msg-{msg_type}" style="margin-bottom:1.5rem">{msg}</div>' if msg else ""

    # ── Dashboard ──
    if section == "dashboard":
        content = f"""
        {msg_html}
        <div class="stats-grid">
          <div class="stat-card">
            <div class="stat-value">{len(bookings)}</div>
            <div class="stat-label">Total Agendamentos</div>
          </div>
          <div class="stat-card" style="border-color:#f59e0b">
            <div class="stat-value" style="color:#b45309">{pending_bookings}</div>
            <div class="stat-label">Agendamentos Pendentes</div>
          </div>
          <div class="stat-card" style="border-color:#10b981">
            <div class="stat-value" style="color:#065f46">{len(users)}</div>
            <div class="stat-label">Clientes Registados</div>
          </div>
          <div class="stat-card" style="border-color:#ef4444">
            <div class="stat-value" style="color:#991b1b">{open_escalations}</div>
            <div class="stat-label">Pedidos de Ajuda</div>
          </div>
        </div>
        <h2 style="font-family:'Playfair Display',serif;margin-bottom:1rem">Últimos Agendamentos</h2>
        <table class="admin-table">
          <thead><tr>
            <th>#</th><th>Nome</th><th>Criança</th><th>Idade</th><th>Data</th><th>Hora</th><th>Estado</th><th>Ações</th>
          </tr></thead>
          <tbody>
            {"".join(f'''<tr>
              <td>{b["id"]}</td>
              <td><strong>{b["parent_name"]}</strong><br>
                  <small style="color:var(--gray)">{b["address"][:30]}</small></td>
              <td>{b["child_name"]}</td>
              <td>{b["child_age"]} anos</td>
              <td>{b["date"]}</td>
              <td>{b["time_slot"]}</td>
              <td><span class="status-badge status-{b["status"]}">{b["status"].title()}</span></td>
              <td style="display:flex;gap:.4rem;flex-wrap:wrap">
                <form method="POST" action="{ADMIN_SECRET_PATH}/booking-action" style="display:inline">
                  <input type="hidden" name="id" value="{b["id"]}">
                  <input type="hidden" name="action" value="accept">
                  <button class="btn btn-sm btn-success">✓</button>
                </form>
                <form method="POST" action="{ADMIN_SECRET_PATH}/booking-action" style="display:inline">
                  <input type="hidden" name="id" value="{b["id"]}">
                  <input type="hidden" name="action" value="reject">
                  <button class="btn btn-sm btn-danger">✗</button>
                </form>
              </td>
            </tr>''' for b in bookings[:10]) or '<tr><td colspan="8" style="color:var(--gray)">Sem agendamentos.</td></tr>'}
          </tbody>
        </table>"""

    # ── Clientes ──
    elif section == "clients":
        content = f"""
        {msg_html}
        <h2 style="font-family:'Playfair Display',serif;margin-bottom:1rem">Clientes</h2>
        <table class="admin-table">
          <thead><tr><th>#</th><th>Nome</th><th>Email</th><th>Registado</th></tr></thead>
          <tbody>
            {"".join(f'''<tr>
              <td>{u["id"]}</td>
              <td><img src="{u.get("photo") or ""}" style="width:28px;height:28px;border-radius:50%;object-fit:cover;margin-right:.5rem;vertical-align:middle" onerror="this.style.display=\'none\'">
                  {u["name"] or "—"}</td>
              <td>{u["email"]}</td>
              <td>{u["created_at"][:10]}</td>
            </tr>''' for u in users) or '<tr><td colspan="4" style="color:var(--gray)">Sem clientes.</td></tr>'}
          </tbody>
        </table>"""

    # ── Avaliações ──
    elif section == "reviews":
        content = f"""
        {msg_html}
        <h2 style="font-family:'Playfair Display',serif;margin-bottom:1rem">Avaliações</h2>
        <table class="admin-table">
          <thead><tr><th>#</th><th>Família</th><th>Nota</th><th>Comentário</th><th>Estado</th><th>Ações</th></tr></thead>
          <tbody>
            {"".join(f'''<tr style="{'background:#fffbeb' if not r["approved"] else ''}">
              <td>{r["id"]}</td>
              <td>{r["family"]}</td>
              <td style="color:#f59e0b">{stars_html(r["stars"])}</td>
              <td>{r["comment"][:60]}{"…" if len(r["comment"])>60 else ""}</td>
              <td>{"✅ Aprovado" if r["approved"] else "⏳ Pendente"}</td>
              <td style="display:flex;gap:.4rem">
                {f\'<form method="POST" action="{ADMIN_SECRET_PATH}/review-action" style="display:inline"><input type="hidden" name="id" value="{r["id"]}"><input type="hidden" name="action" value="approve"><button class="btn btn-sm btn-success">Aprovar</button></form>\' if not r["approved"] else ""}
                <form method="POST" action="{ADMIN_SECRET_PATH}/review-action" style="display:inline">
                  <input type="hidden" name="id" value="{r["id"]}">
                  <input type="hidden" name="action" value="delete">
                  <button class="btn btn-sm btn-danger" onclick="return confirm(\'Apagar?\')">Apagar</button>
                </form>
              </td>
            </tr>''' for r in reviews) or '<tr><td colspan="6" style="color:var(--gray)">Sem avaliações.</td></tr>'}
          </tbody>
        </table>"""

    # ── Ajuda / Escalações ──
    elif section == "help":
        content = f"""
        {msg_html}
        <h2 style="font-family:'Playfair Display',serif;margin-bottom:1rem">Pedidos de Ajuda do Chatbot</h2>
        <table class="admin-table">
          <thead><tr><th>#</th><th>Utilizador</th><th>Pergunta</th><th>Atribuído a</th><th>Estado</th><th>Ações</th></tr></thead>
          <tbody>
            {"".join(f'''<tr>
              <td>{e["id"]}</td>
              <td>{e.get("user_name") or "Anónimo"}<br>
                  <small style="color:var(--gray)">{e.get("user_email") or ""}</small></td>
              <td>{e["question"][:80]}</td>
              <td>
                <form method="POST" action="{ADMIN_SECRET_PATH}/assign-escalation" style="display:inline-flex;gap:.4rem">
                  <input type="hidden" name="id" value="{e["id"]}">
                  <select name="staff_id" style="padding:.3rem;font-size:.8rem">
                    <option value="">— Ninguém —</option>
                    {"".join(f\'<option value="{s["id"]}" {"selected" if e.get("assigned_to")==s["id"] else ""}>{s["name"]}</option>\' for s in staff_list)}
                  </select>
                  <button class="btn btn-sm btn-outline" type="submit">OK</button>
                </form>
              </td>
              <td><span class="status-badge {"status-resolved" if e["resolved"] else "status-pending"}">{"Resolvido" if e["resolved"] else "Aberto"}</span></td>
              <td>
                <form method="POST" action="{ADMIN_SECRET_PATH}/resolve-escalation" style="display:inline">
                  <input type="hidden" name="id" value="{e["id"]}">
                  <button class="btn btn-sm btn-success">✓ Resolver</button>
                </form>
              </td>
            </tr>''' for e in escalations) or '<tr><td colspan="6" style="color:var(--gray)">Sem pedidos de ajuda.</td></tr>'}
          </tbody>
        </table>"""

    # ── Funcionários ──
    elif section == "staff":
        content = f"""
        {msg_html}
        <h2 style="font-family:'Playfair Display',serif;margin-bottom:1rem">Funcionários</h2>
        <div style="background:var(--white);border-radius:var(--radius);padding:1.5rem;
                    margin-bottom:1.5rem;box-shadow:0 2px 12px rgba(0,0,0,.05)">
          <h3 style="margin-bottom:1rem;font-size:1rem">Adicionar Funcionário</h3>
          <form method="POST" action="{ADMIN_SECRET_PATH}/add-staff" style="display:flex;gap:1rem;flex-wrap:wrap">
            <input type="text" name="name" placeholder="Nome" required style="flex:1;min-width:150px">
            <input type="email" name="email" placeholder="Email" style="flex:1;min-width:150px">
            <button class="btn btn-primary btn-sm" type="submit">Adicionar</button>
          </form>
        </div>
        <table class="admin-table">
          <thead><tr><th>#</th><th>Nome</th><th>Email</th><th>Disponível</th><th>Ações</th></tr></thead>
          <tbody>
            {"".join(f'''<tr>
              <td>{s["id"]}</td>
              <td>{s["name"]}</td>
              <td>{s.get("email") or "—"}</td>
              <td>{"✅" if s["available"] else "❌"}</td>
              <td>
                <form method="POST" action="{ADMIN_SECRET_PATH}/delete-staff" style="display:inline">
                  <input type="hidden" name="id" value="{s["id"]}">
                  <button class="btn btn-sm btn-danger" onclick="return confirm(\'Remover?\')">Remover</button>
                </form>
              </td>
            </tr>''' for s in staff_list) or '<tr><td colspan="5" style="color:var(--gray)">Sem funcionários.</td></tr>'}
          </tbody>
        </table>"""

    else:
        content = "<p>Secção não encontrada.</p>"

    nav_items = [
        ("dashboard", "📊", "Dashboard"),
        ("clients",   "👥", "Clientes"),
        ("reviews",   "⭐", "Avaliações"),
        ("help",      "🆘", "Pedidos de Ajuda"),
        ("staff",     "👤", "Funcionários"),
    ]
    sidebar_nav = "".join(
        f'<a href="{ADMIN_SECRET_PATH}?s={s}" class="{"active" if section==s else ""}">'
        f'{icon} {label}'
        f'{"<span style=\'background:var(--pink);color:#fff;border-radius:99px;padding:.1rem .5rem;font-size:.72rem;margin-left:auto\'>" + str(pending_reviews if s=="reviews" else open_escalations if s=="help" else pending_bookings if s=="dashboard" else "") + "</span>" if (s=="reviews" and pending_reviews) or (s=="help" and open_escalations) or (s=="dashboard" and pending_bookings) else ""}'
        f'</a>'
        for s, icon, label in nav_items
    )

    return f"""<!DOCTYPE html>
<html lang="pt-PT">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Admin – {BABYSITTER_NAME}</title>
  <style>{STYLE}</style>
</head>
<body>
<div class="admin-layout">
  <aside class="admin-sidebar">
    <div class="admin-logo">🍼 Admin</div>
    <nav class="admin-nav">
      {sidebar_nav}
      <a href="/" style="margin-top:auto">🌐 Ver site</a>
      <a href="{ADMIN_SECRET_PATH}/logout">🚪 Sair</a>
    </nav>
  </aside>
  <main class="admin-main">
    <div class="admin-header">
      <h1>{"Dashboard" if section=="dashboard" else "Clientes" if section=="clients" else "Avaliações" if section=="reviews" else "Pedidos de Ajuda" if section=="help" else "Funcionários"}</h1>
    </div>
    {content}
  </main>
</div>
<script>{SCRIPT}</script>
</body>
</html>"""


# ══════════════════════════════════════════════════════════════
#  HANDLER HTTP
# ══════════════════════════════════════════════════════════════

class Handler(http.server.BaseHTTPRequestHandler):

    def client_ip(self):
        forwarded = self.headers.get("X-Forwarded-For", "")
        return forwarded.split(",")[0].strip() if forwarded else self.client_address[0]

    def send_html(self, html, status=200, headers=None):
        body = html.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def redirect(self, loc, headers=None):
        self.send_response(302)
        self.send_header("Location", loc)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()

    def is_admin_session(self):
        s = get_session(self)
        return s and s.get("is_admin")

    # ── GET ──────────────────────────────────────────────────
    def do_GET(self):
        ip = self.client_ip()
        if is_rate_limited(ip):
            self.send_html("<h2 style='text-align:center;margin-top:4rem'>429 – Demasiados pedidos. Aguarda um momento.</h2>", 429)
            return

        parsed = urlparse(self.path)
        path   = parsed.path
        params = parse_qs(parsed.query)
        session = get_session(self)

        # Site público
        if path == "/":
            self.send_html(page_home(session))

        elif path == "/login":
            if session:
                return self.redirect("/")
            self.send_html(page_login())

        elif path == "/logout":
            destroy_session(self)
            self.redirect("/", headers={"Set-Cookie": "sid=; Max-Age=0; Path=/; HttpOnly"})

        # Google OAuth
        elif path == "/auth/google":
            if not GOOGLE_CLIENT_ID:
                return self.redirect("/login")
            self.redirect(google_auth_url())

        elif path == "/auth/google/callback":
            code = params.get("code", [""])[0]
            if not code:
                return self.redirect("/login")
            tokens  = google_exchange_code(code)
            guser   = google_get_user(tokens.get("access_token", ""))
            if not guser.get("email"):
                return self.send_html(page_login("Erro ao autenticar com Google."))
            user_id = get_or_create_user(guser)
            token   = create_session(user_id=user_id)
            self.redirect("/", headers={
                "Set-Cookie": f"sid={token}; Max-Age=28800; Path=/; HttpOnly"
            })

        # Admin (URL secreta)
        elif path == ADMIN_SECRET_PATH:
            if not self.is_admin_session():
                return self.send_html(self._admin_login_page())
            section = params.get("s", ["dashboard"])[0]
            self.send_html(page_admin(section))

        elif path == f"{ADMIN_SECRET_PATH}/logout":
            destroy_session(self)
            self.redirect("/", headers={"Set-Cookie": "sid=; Max-Age=0; Path=/; HttpOnly"})

        else:
            self.send_html("<h2 style='text-align:center;margin-top:4rem;font-family:serif'>404 – Página não encontrada</h2>", 404)

    # ── POST ─────────────────────────────────────────────────
    def do_POST(self):
        ip = self.client_ip()
        if is_rate_limited(ip):
            self.send_html("<h2 style='text-align:center;margin-top:4rem'>429 – Demasiados pedidos.</h2>", 429)
            return

        path    = urlparse(self.path).path
        session = get_session(self)

        # ── API Chatbot ──
        if path == "/api/chat":
            body = read_json(self)
            question = body.get("message", "")[:500]
            history  = body.get("history", [])
            if not question:
                return self.send_json({"reply": "Não percebi. Podes repetir?"})
            reply = ask_chatbot(question, history)
            # Se o chatbot escalou
            if reply.startswith("ESCALAR:"):
                reason = reply[8:].strip()
                with get_db() as db:
                    db.execute(
                        "INSERT INTO chat_escalations (user_name, user_email, question) VALUES (?,?,?)",
                        (session.get("name") if session else None,
                         session.get("email") if session else None,
                         f"{question} | Motivo: {reason}")
                    )
                    db.commit()
                reply = ("Vou passar o teu pedido a um funcionário que te ajudará em breve! "
                         "Podes também contactar diretamente pelo WhatsApp. 💬")
            return self.send_json({"reply": reply})

        # ── Agendamento ──
        elif path == "/agendar":
            data        = read_body(self)
            parent_name = field(data, "parent_name")
            address     = field(data, "address")
            child_name  = field(data, "child_name")
            child_age   = field(data, "child_age")
            date        = field(data, "date")
            time_slot   = field(data, "time_slot")
            hours       = field(data, "hours")
            notes       = field(data, "notes")
            user_id     = field(data, "user_id") or None

            if not all([parent_name, address, child_name, child_age, date, time_slot, hours]):
                return self.send_html(page_home(session, booking_msg="Preenche todos os campos obrigatórios.", booking_type="err"))
            try:
                child_age = int(child_age); assert 0 <= child_age <= 12
                hours_int = int(hours);     assert 1 <= hours_int <= 12
            except Exception:
                return self.send_html(page_home(session, booking_msg="Valores inválidos.", booking_type="err"))

            with get_db() as db:
                db.execute(
                    "INSERT INTO bookings (user_id,parent_name,address,child_name,child_age,date,time_slot,hours,notes) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (user_id, parent_name, address, child_name, child_age, date, time_slot, hours_int, notes)
                )
                db.commit()
            self.send_html(page_home(session,
                booking_msg="✅ Pedido enviado! Entrarei em contacto para confirmar.", booking_type="ok"))

        # ── Avaliação ──
        elif path == "/review":
            data    = read_body(self)
            family  = field(data, "family")
            comment = field(data, "comment")
            stars   = field(data, "stars")
            if not all([family, comment, stars]):
                return self.send_html(page_home(session, review_msg="Preenche todos os campos.", review_type="err"))
            try:
                stars_int = int(stars); assert 1 <= stars_int <= 5
            except Exception:
                return self.send_html(page_home(session, review_msg="Nota inválida.", review_type="err"))
            with get_db() as db:
                db.execute("INSERT INTO reviews (family,stars,comment) VALUES (?,?,?)",
                           (family, stars_int, comment))
                db.commit()
            self.send_html(page_home(session,
                review_msg="💛 Obrigada! A tua avaliação ficará visível após aprovação.", review_type="ok"))

        # ── Login Admin ──
        elif path == f"{ADMIN_SECRET_PATH}/login":
            data = read_body(self)
            pwd  = field(data, "password")
            if (hashlib.sha256(pwd.encode()).hexdigest() ==
                    hashlib.sha256(ADMIN_PASSWORD.encode()).hexdigest()):
                token = create_session(is_admin=True)
                self.redirect(ADMIN_SECRET_PATH, headers={
                    "Set-Cookie": f"sid={token}; Max-Age=14400; Path=/; HttpOnly"
                })
            else:
                self.send_html(self._admin_login_page("Senha incorreta."))

        # ── Admin: Booking action ──
        elif path == f"{ADMIN_SECRET_PATH}/booking-action":
            if not self.is_admin_session():
                return self.redirect(ADMIN_SECRET_PATH)
            data   = read_body(self)
            bid    = field(data, "id")
            action = field(data, "action")
            status = "accepted" if action == "accept" else "rejected"
            with get_db() as db:
                db.execute("UPDATE bookings SET status=? WHERE id=?", (status, bid))
                db.commit()
            self.redirect(f"{ADMIN_SECRET_PATH}?s=dashboard")

        # ── Admin: Review action ──
        elif path == f"{ADMIN_SECRET_PATH}/review-action":
            if not self.is_admin_session():
                return self.redirect(ADMIN_SECRET_PATH)
            data   = read_body(self)
            rid    = field(data, "id")
            action = field(data, "action")
            with get_db() as db:
                if action == "approve":
                    db.execute("UPDATE reviews SET approved=1 WHERE id=?", (rid,))
                else:
                    db.execute("DELETE FROM reviews WHERE id=?", (rid,))
                db.commit()
            self.redirect(f"{ADMIN_SECRET_PATH}?s=reviews")

        # ── Admin: Assign escalation ──
        elif path == f"{ADMIN_SECRET_PATH}/assign-escalation":
            if not self.is_admin_session():
                return self.redirect(ADMIN_SECRET_PATH)
            data     = read_body(self)
            eid      = field(data, "id")
            staff_id = field(data, "staff_id") or None
            with get_db() as db:
                db.execute("UPDATE chat_escalations SET assigned_to=? WHERE id=?", (staff_id, eid))
                db.commit()
            self.redirect(f"{ADMIN_SECRET_PATH}?s=help")

        # ── Admin: Resolve escalation ──
        elif path == f"{ADMIN_SECRET_PATH}/resolve-escalation":
            if not self.is_admin_session():
                return self.redirect(ADMIN_SECRET_PATH)
            data = read_body(self)
            eid  = field(data, "id")
            with get_db() as db:
                db.execute("UPDATE chat_escalations SET resolved=1 WHERE id=?", (eid,))
                db.commit()
            self.redirect(f"{ADMIN_SECRET_PATH}?s=help")

        # ── Admin: Add staff ──
        elif path == f"{ADMIN_SECRET_PATH}/add-staff":
            if not self.is_admin_session():
                return self.redirect(ADMIN_SECRET_PATH)
            data  = read_body(self)
            name  = field(data, "name")
            email = field(data, "email")
            if name:
                with get_db() as db:
                    db.execute("INSERT INTO staff (name, email) VALUES (?,?)", (name, email))
                    db.commit()
            self.redirect(f"{ADMIN_SECRET_PATH}?s=staff")

        # ── Admin: Delete staff ──
        elif path == f"{ADMIN_SECRET_PATH}/delete-staff":
            if not self.is_admin_session():
                return self.redirect(ADMIN_SECRET_PATH)
            data = read_body(self)
            sid  = field(data, "id")
            with get_db() as db:
                db.execute("DELETE FROM staff WHERE id=?", (sid,))
                db.commit()
            self.redirect(f"{ADMIN_SECRET_PATH}?s=staff")

        else:
            self.send_html("<h2>404</h2>", 404)

    def _admin_login_page(self, error=""):
        msg = f'<div class="msg msg-err">{error}</div>' if error else ""
        return f"""<!DOCTYPE html>
<html lang="pt-PT">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Admin</title>
  <style>{STYLE}</style>
</head>
<body>
<div class="login-page">
  <div class="login-card">
    <div style="font-size:2.5rem;margin-bottom:1rem">🔐</div>
    <h1>Acesso Restrito</h1>
    <p>Área de gestão privada</p>
    {msg}
    <form method="POST" action="{ADMIN_SECRET_PATH}/login">
      <input type="password" name="password" placeholder="Senha" required style="margin-bottom:1rem">
      <button class="btn btn-primary" type="submit" style="width:100%;justify-content:center">Entrar</button>
    </form>
  </div>
</div>
</body>
</html>"""

    def log_message(self, fmt, *args):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {fmt % args}")


# ══════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    init_db()
    print(f"✅  Base de dados: {DB_PATH}")
    print(f"🚀  Site em       http://localhost:{PORT}")
    print(f"🔐  Admin em      http://localhost:{PORT}{ADMIN_SECRET_PATH}")
    print(f"    (URL secreta — não partilhes!)")
    if not ANTHROPIC_API_KEY:
        print("⚠️   ANTHROPIC_API_KEY não definida — chatbot em modo limitado")
    if not GOOGLE_CLIENT_ID:
        print("⚠️   GOOGLE_CLIENT_ID não definida — login Google desativado")
    print("    Ctrl+C para parar\n")
    with http.server.HTTPServer((HOST, PORT), Handler) as httpd:
        httpd.serve_forever()
