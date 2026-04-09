#!/usr/bin/env python3
"""
Site de Babysitter – Servidor Python puro
- Página pública com perfil, serviços, avaliações
- Famílias deixam avaliações (nome + estrelas + comentário)
- Botão WhatsApp para contato
- Painel admin para aprovar/apagar avaliações
"""

import http.server
import sqlite3
import hashlib
import secrets
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlparse
from datetime import datetime, timedelta
import os

# ── Configuração ──────────────────────────────────────────────
BABYSITTER_NAME  = "Henriqueta Machava"
BABYSITTER_BIO   = ("Olá! Sou a Henriqueta, tenho 14 anos e adoro cuidar de crianças. "
                    "Tenho experiência com bebês e crianças até 10 anos, curso de "
                    "culinária e muita paciência e carinho. 💛")
BABYSITTER_PHOTO = "C:\Users\Glei\Downloads\SOSBabySitter-1775747078618\SOSBabySitter-horizontal.png "               # URL de uma foto (deixe vazio para usar emoji)
WHATSAPP_NUMBER  = "351965813670"   # DDI+número, só dígitos
HOURLY_RATE      = "10€/hora"
LOCATION         = "Lisboa, Portugal"
ADMIN_PASSWORD   = "Henriqueta2011"

DB_PATH = os.environ.get("DB_PATH", "babysitter.db")
HOST    = "0.0.0.0"
PORT    = int(os.environ.get("PORT", 8080))
# ─────────────────────────────────────────────────────────────


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS reviews (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                family     TEXT    NOT NULL,
                stars      INTEGER NOT NULL CHECK(stars BETWEEN 1 AND 5),
                comment    TEXT    NOT NULL,
                approved   INTEGER DEFAULT 0,
                created_at TEXT    DEFAULT CURRENT_TIMESTAMP
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                token      TEXT PRIMARY KEY,
                expires_at TEXT NOT NULL
            )
        """)
        db.commit()


# ── Sessão admin ──────────────────────────────────────────────

def create_session() -> str:
    token = secrets.token_hex(32)
    expires = (datetime.utcnow() + timedelta(hours=4)).isoformat()
    with get_db() as db:
        db.execute("INSERT INTO sessions VALUES (?, ?)", (token, expires))
        db.commit()
    return token


def is_admin(handler) -> bool:
    cookies = SimpleCookie(handler.headers.get("Cookie", ""))
    if "admin_session" not in cookies:
        return False
    token = cookies["admin_session"].value
    with get_db() as db:
        row = db.execute(
            "SELECT 1 FROM sessions WHERE token=? AND expires_at>?",
            (token, datetime.utcnow().isoformat())
        ).fetchone()
    return row is not None


def delete_session(handler):
    cookies = SimpleCookie(handler.headers.get("Cookie", ""))
    if "admin_session" in cookies:
        with get_db() as db:
            db.execute("DELETE FROM sessions WHERE token=?",
                       (cookies["admin_session"].value,))
            db.commit()


# ── Helpers ───────────────────────────────────────────────────

def read_body(handler) -> dict:
    length = int(handler.headers.get("Content-Length", 0))
    return parse_qs(handler.rfile.read(length).decode())


def field(data, name):
    return data.get(name, [""])[0].strip()


def stars_html(n):
    return "★" * int(n) + "☆" * (5 - int(n))


def avg_stars(reviews):
    if not reviews:
        return 0
    return sum(r["stars"] for r in reviews) / len(reviews)


# ── CSS ───────────────────────────────────────────────────────

CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: system-ui, sans-serif; background: #fff8f0; color: #333; }

nav { background: #ff7eb3; padding: 1rem 2rem;
      display: flex; justify-content: space-between; align-items: center; }
nav .brand { color: #fff; font-size: 1.2rem; font-weight: 700; text-decoration: none; }
nav a { color: #fff; text-decoration: none; font-size: .9rem;
        margin-left: 1rem; opacity: .85; }
nav a:hover { opacity: 1; text-decoration: underline; }

.hero { text-align: center; padding: 3rem 1rem 2rem; }
.avatar { width: 130px; height: 130px; border-radius: 50%;
          object-fit: cover; border: 4px solid #ff7eb3; }
.avatar-emoji { font-size: 6rem; line-height: 130px; }
.hero h1 { font-size: 2rem; margin: .75rem 0 .4rem; }
.hero .subtitle { color: #777; margin-bottom: 1.2rem; }
.badge { display: inline-block; background: #fff3cd; color: #856404;
         border-radius: 99px; padding: .3rem .9rem;
         font-size: .85rem; margin: .2rem; }
.btn-whatsapp { display: inline-block; margin-top: 1.5rem;
                background: #25d366; color: #fff; padding: .85rem 2rem;
                border-radius: 99px; font-size: 1rem; font-weight: 600;
                text-decoration: none; }
.btn-whatsapp:hover { background: #1ebe5d; }

section { max-width: 760px; margin: 0 auto 2.5rem; padding: 0 1.5rem; }
section h2 { font-size: 1.3rem; color: #ff7eb3; margin-bottom: 1rem;
             border-bottom: 2px solid #ffe0ef; padding-bottom: .4rem; }

.services { display: grid;
            grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
            gap: 1rem; }
.service-card { background: #fff; border-radius: 10px; padding: 1.2rem;
                text-align: center; box-shadow: 0 2px 8px rgba(0,0,0,.07); }
.service-card .icon { font-size: 2rem; margin-bottom: .5rem; }
.service-card h3 { font-size: .95rem; margin-bottom: .3rem; }
.service-card p { font-size: .82rem; color: #777; }

.review-card { background: #fff; border-radius: 10px; padding: 1.2rem;
               margin-bottom: 1rem; box-shadow: 0 2px 8px rgba(0,0,0,.07); }
.review-card .stars { color: #f59e0b; font-size: 1.1rem; }
.review-card .family { font-weight: 600; margin-bottom: .3rem; }
.review-card .comment { font-size: .92rem; color: #555; }
.review-card .date { font-size: .78rem; color: #aaa; margin-top: .4rem; }

.review-form { background: #fff; border-radius: 10px; padding: 1.5rem;
               box-shadow: 0 2px 8px rgba(0,0,0,.07); }
.review-form h3 { margin-bottom: 1rem; }

input[type=text], textarea, select, input[type=password] {
  width: 100%; padding: .65rem; margin-bottom: .9rem;
  border: 1px solid #ddd; border-radius: 6px;
  font-size: .95rem; font-family: inherit; }
textarea { resize: vertical; min-height: 80px; }

.btn { padding: .7rem 1.6rem; border: none; border-radius: 6px;
       font-size: .95rem; cursor: pointer; font-weight: 600; }
.btn-pink { background: #ff7eb3; color: #fff; }
.btn-pink:hover { background: #ff5c9e; }

.msg { padding: .75rem 1rem; border-radius: 6px;
       margin-bottom: 1rem; font-size: .9rem; }
.msg-ok  { background: #d1fae5; color: #065f46; }
.msg-err { background: #fee2e2; color: #991b1b; }

.admin-table { width: 100%; border-collapse: collapse; font-size: .9rem; }
.admin-table th, .admin-table td {
  padding: .6rem .8rem; border: 1px solid #eee; text-align: left; }
.admin-table th { background: #fce7f3; }
.btn-sm { padding: .35rem .8rem; font-size: .82rem; border-radius: 4px;
          border: none; cursor: pointer; font-weight: 600; margin-right: .3rem; }
.btn-approve { background: #bbf7d0; color: #065f46; }
.btn-delete  { background: #fecaca; color: #991b1b; }
"""


def layout(title, body, admin=False):
    admin_link = ('<a href="/admin/logout">Sair</a>' if admin
                  else '<a href="/admin">Admin</a>')
    return f"""<!DOCTYPE html>
<html lang="pt-PT">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{title}</title>
  <style>{CSS}</style>
</head>
<body>
  <nav>
    <a class="brand" href="/">🍼 {BABYSITTER_NAME}</a>
    <div>{admin_link}</div>
  </nav>
  {body}
</body>
</html>"""


# ── Páginas ───────────────────────────────────────────────────

def page_home(msg="", msg_type="ok"):
    with get_db() as db:
        reviews = [dict(r) for r in db.execute(
            "SELECT * FROM reviews WHERE approved=1 ORDER BY id DESC"
        ).fetchall()]

    avg = avg_stars(reviews)
    avg_display = (f"{avg:.1f} {stars_html(round(avg))}"
                   if reviews else "Sem avaliações ainda")

    reviews_html = "".join(f"""
    <div class="review-card">
      <div class="family">{r['family']}</div>
      <div class="stars">{stars_html(r['stars'])}</div>
      <div class="comment">{r['comment']}</div>
      <div class="date">{r['created_at'][:10]}</div>
    </div>""" for r in reviews) or \
        "<p style='color:#aaa'>Seja a primeira família a avaliar! 😊</p>"

    msg_html   = f'<div class="msg msg-{msg_type}">{msg}</div>' if msg else ""
    photo_html = (f'<img class="avatar" src="{BABYSITTER_PHOTO}" alt="Foto">'
                  if BABYSITTER_PHOTO else '<div class="avatar-emoji">👩</div>')
    wa_text = (f"Olá {BABYSITTER_NAME.split()[0]}! "
               "Vi o teu site e gostaria de saber mais sobre os teus serviços.")
    wa_url  = f"https://wa.me/{WHATSAPP_NUMBER}?text={wa_text.replace(' ', '%20')}"

    body = f"""
    <div class="hero">
      {photo_html}
      <h1>{BABYSITTER_NAME}</h1>
      <p class="subtitle">📍 {LOCATION} &nbsp;|&nbsp; 💰 {HOURLY_RATE}</p>
      <span class="badge">🍳 Curso de Culinária</span>
      <span class="badge">🍼 Bebés e crianças</span>
      <span class="badge">⭐ {avg_display}</span>
      <br>
      <a class="btn-whatsapp" href="{wa_url}" target="_blank">
        💬 Chamar no WhatsApp
      </a>
    </div>

    <section>
      <h2>Sobre mim</h2>
      <p style="line-height:1.7">{BABYSITTER_BIO}</p>
    </section>

    <section>
      <h2>O que ofereço</h2>
      <div class="services">
        <div class="service-card">
          <div class="icon">🍼</div>
          <h3>Bebés</h3>
          <p>Cuidados especiais para bebés de 0–2 anos</p>
        </div>
        <div class="service-card">
          <div class="icon">🎨</div>
          <h3>Recreação</h3>
          <p>Brincadeiras e atividades educativas</p>
        </div>
        <div class="service-card">
          <div class="icon">🍳</div>
          <h3>Refeições</h3>
          <p>Preparação de refeições saudáveis para as crianças</p>
        </div>
        <div class="service-card">
          <div class="icon">💛</div>
          <h3>Carinho</h3>
          <p>Muita paciência e dedicação a cada criança</p>
        </div>
      </div>
    </section>

    <section>
      <h2>O que as famílias dizem</h2>
      {reviews_html}
    </section>

    <section>
      <div class="review-form">
        <h3>Deixe a sua avaliação 💛</h3>
        {msg_html}
        <form method="POST" action="/review">
          <input type="text" name="family"
                 placeholder="O seu nome / família" required maxlength="80">
          <select name="stars" required>
            <option value="">⭐ A sua nota</option>
            <option value="5">★★★★★ Excelente</option>
            <option value="4">★★★★☆ Muito bom</option>
            <option value="3">★★★☆☆ Bom</option>
            <option value="2">★★☆☆☆ Regular</option>
            <option value="1">★☆☆☆☆ Mau</option>
          </select>
          <textarea name="comment"
                    placeholder="Conte a sua experiência..."
                    required maxlength="500"></textarea>
          <button class="btn btn-pink" type="submit">Enviar avaliação</button>
        </form>
      </div>
    </section>
    """
    return layout(f"{BABYSITTER_NAME} – Babysitter em Lisboa", body)


def page_admin_login(error=""):
    msg = f'<div class="msg msg-err">{error}</div>' if error else ""
    body = f"""
    <div style="max-width:380px;margin:4rem auto;background:#fff;padding:2rem;
                border-radius:10px;box-shadow:0 2px 12px rgba(0,0,0,.1)">
      <h2 style="margin-bottom:1.2rem;color:#ff7eb3">🔐 Painel Admin</h2>
      {msg}
      <form method="POST" action="/admin/login">
        <input type="password" name="password" placeholder="Senha admin" required>
        <button class="btn btn-pink" type="submit" style="width:100%">Entrar</button>
      </form>
    </div>"""
    return layout("Admin – Login", body)


def page_admin_dashboard():
    with get_db() as db:
        reviews = [dict(r) for r in db.execute(
            "SELECT * FROM reviews ORDER BY approved ASC, id DESC"
        ).fetchall()]

    rows = "".join(f"""
    <tr style="{'background:#fffbeb' if not r['approved'] else ''}">
      <td>{r['id']}</td>
      <td>{r['family']}</td>
      <td style="color:#f59e0b">{stars_html(r['stars'])}</td>
      <td>{r['comment'][:80]}{'…' if len(r['comment']) > 80 else ''}</td>
      <td>{'✅ Aprovado' if r['approved'] else '⏳ Pendente'}</td>
      <td>
        {'<form method="POST" action="/admin/approve" style="display:inline">'
         f'<input type="hidden" name="id" value="{r["id"]}">'
         '<button class="btn-sm btn-approve">Aprovar</button>'
         '</form>' if not r['approved'] else ''}
        <form method="POST" action="/admin/delete" style="display:inline">
          <input type="hidden" name="id" value="{r['id']}">
          <button class="btn-sm btn-delete"
                  onclick="return confirm('Apagar esta avaliação?')">
            Apagar
          </button>
        </form>
      </td>
    </tr>""" for r in reviews) or \
        "<tr><td colspan='6' style='color:#aaa'>Nenhuma avaliação ainda.</td></tr>"

    body = f"""
    <section style="max-width:900px">
      <h2 style="margin:2rem 0 1rem">Gerir Avaliações</h2>
      <table class="admin-table">
        <thead>
          <tr>
            <th>#</th><th>Família</th><th>Nota</th>
            <th>Comentário</th><th>Estado</th><th>Ações</th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
      <p style="margin-top:1rem;font-size:.85rem;color:#aaa">
        ⏳ Avaliações pendentes ficam a amarelo e só aparecem no site após aprovação.
      </p>
    </section>"""
    return layout("Admin – Avaliações", body, admin=True)


# ── Handler HTTP ──────────────────────────────────────────────

class Handler(http.server.BaseHTTPRequestHandler):

    def send_html(self, html, status=200, headers=None):
        body = html.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def redirect(self, loc, headers=None):
        self.send_response(302)
        self.send_header("Location", loc)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            self.send_html(page_home())
        elif path == "/admin":
            if is_admin(self):
                self.send_html(page_admin_dashboard())
            else:
                self.redirect("/admin/login")
        elif path == "/admin/login":
            self.send_html(page_admin_login())
        elif path == "/admin/logout":
            delete_session(self)
            self.redirect("/", headers={
                "Set-Cookie": "admin_session=; Max-Age=0; Path=/; HttpOnly"
            })
        else:
            self.send_html(
                "<h2 style='text-align:center;margin-top:4rem'>404 – Página não encontrada</h2>",
                404
            )

    def do_POST(self):
        path = urlparse(self.path).path
        data = read_body(self)

        if path == "/review":
            family  = field(data, "family")
            comment = field(data, "comment")
            stars   = field(data, "stars")
            if not all([family, comment, stars]):
                return self.send_html(page_home("Preencha todos os campos.", "err"))
            try:
                stars_int = int(stars)
                assert 1 <= stars_int <= 5
            except Exception:
                return self.send_html(page_home("Nota inválida.", "err"))
            with get_db() as db:
                db.execute(
                    "INSERT INTO reviews (family, stars, comment) VALUES (?,?,?)",
                    (family, stars_int, comment)
                )
                db.commit()
            self.send_html(page_home(
                "Obrigado pela avaliação! Ficará visível após aprovação. 💛", "ok"
            ))

        elif path == "/admin/login":
            pwd = field(data, "password")
            if (hashlib.sha256(pwd.encode()).hexdigest() ==
                    hashlib.sha256(ADMIN_PASSWORD.encode()).hexdigest()):
                token = create_session()
                self.redirect("/admin", headers={
                    "Set-Cookie": (f"admin_session={token}; "
                                   "Max-Age=14400; Path=/; HttpOnly")
                })
            else:
                self.send_html(page_admin_login("Senha incorreta."))

        elif path == "/admin/approve":
            if not is_admin(self):
                return self.redirect("/admin/login")
            with get_db() as db:
                db.execute("UPDATE reviews SET approved=1 WHERE id=?",
                           (field(data, "id"),))
                db.commit()
            self.redirect("/admin")

        elif path == "/admin/delete":
            if not is_admin(self):
                return self.redirect("/admin/login")
            with get_db() as db:
                db.execute("DELETE FROM reviews WHERE id=?",
                           (field(data, "id"),))
                db.commit()
            self.redirect("/admin")

        else:
            self.send_html("<h2>404</h2>", 404)

    def log_message(self, fmt, *args):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {fmt % args}")


# ── Main ──────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    print(f"✅  Base de dados iniciada: {DB_PATH}")
    print(f"🚀  Site em   http://localhost:{PORT}")
    print(f"🔐  Admin em  http://localhost:{PORT}/admin")
    print("    Ctrl+C para parar\n")
    with http.server.HTTPServer((HOST, PORT), Handler) as httpd:
        httpd.serve_forever()
