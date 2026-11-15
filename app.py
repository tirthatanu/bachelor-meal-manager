import os
import json
import sqlite3
import socket
from datetime import datetime, date, time, timedelta
from zoneinfo import ZoneInfo
from flask import Flask, render_template_string, request, jsonify, session

# ==============================================================================
# BACHELOR FLAT MEAL MANAGEMENT SYSTEM - VS CODE WINDOWS READY (SINGLE FILE)
# ==============================================================================

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dhaka-bachelor-flat-secret-2026")
DB_NAME = "bachelor_flat_meals.db"
DHAKA_TZ = ZoneInfo("Asia/Dhaka")


def get_local_ip():
    """Automatically discover the computer's local Wi-Fi IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.executescript("""
    CREATE TABLE IF NOT EXISTS system_settings (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        cutoff_time TEXT NOT NULL DEFAULT '17:00',
        cutoff_target TEXT NOT NULL DEFAULT 'NEXT_DAY' CHECK (cutoff_target IN ('NEXT_DAY', 'SAME_DAY')),
        timezone TEXT NOT NULL DEFAULT 'Asia/Dhaka',
        meal_rate REAL NOT NULL DEFAULT 65.0
    );

    INSERT OR IGNORE INTO system_settings (id, cutoff_time, cutoff_target, timezone, meal_rate)
    VALUES (1, '17:00', 'NEXT_DAY', 'Asia/Dhaka', 65.0);

    CREATE TABLE IF NOT EXISTS members (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        full_name TEXT NOT NULL,
        phone_number TEXT NULL,
        role TEXT NOT NULL DEFAULT 'MEMBER' CHECK (role IN ('ADMIN', 'MEMBER')),
        account_status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (account_status IN ('ACTIVE', 'PENDING_JOIN'))
    );

    CREATE TABLE IF NOT EXISTS daily_meals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
        meal_date TEXT NOT NULL,
        lunch_status INTEGER NOT NULL DEFAULT 0,
        dinner_status INTEGER NOT NULL DEFAULT 0,
        confirmed_at TEXT NOT NULL,
        updated_by_admin INTEGER NOT NULL DEFAULT 0,
        UNIQUE(member_id, meal_date)
    );

    CREATE TABLE IF NOT EXISTS guest_meals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
        meal_date TEXT NOT NULL,
        guest_lunch_qty INTEGER NOT NULL DEFAULT 0 CHECK (guest_lunch_qty >= 0),
        guest_dinner_qty INTEGER NOT NULL DEFAULT 0 CHECK (guest_dinner_qty >= 0),
        guest_name TEXT NULL,
        description TEXT NULL,
        idempotency_key TEXT UNIQUE NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        CHECK (guest_lunch_qty > 0 OR guest_dinner_qty > 0)
    );
    """)

    count = cur.execute("SELECT COUNT(*) FROM members").fetchone()[0]
    if count == 0:
        cur.executemany(
            """
            INSERT INTO members (username, password, full_name, phone_number, role, account_status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                ("tanu", "admin123", "Tanu", "01816125232", "ADMIN", "ACTIVE"),
                ("shovan", "meal123", "Shovan", "01786336429", "MEMBER", "ACTIVE"),
                ("jubayer", "meal123", "Jubayer", "01836267797", "MEMBER", "ACTIVE"),
                ("rahat", "meal123", "Rahat", "01777470408", "MEMBER", "ACTIVE"),
                ("member5", "meal123", "Member 5", None, "MEMBER", "PENDING_JOIN"),
            ],
        )

    conn.commit()
    conn.close()


def get_dhaka_now() -> datetime:
    return datetime.now(DHAKA_TZ)


def get_cutoff_context(conn):
    settings = conn.execute("SELECT * FROM system_settings WHERE id = 1").fetchone()
    now_dhaka = get_dhaka_now()
    hh, mm = map(int, settings["cutoff_time"].split(":"))
    today_cutoff = datetime.combine(now_dhaka.date(), time(hh, mm), tzinfo=DHAKA_TZ)

    is_past_cutoff = now_dhaka >= today_cutoff
    remaining_seconds = max(0, int((today_cutoff - now_dhaka).total_seconds()))

    if settings["cutoff_target"] == "NEXT_DAY":
        applicable_date = now_dhaka.date() + timedelta(days=1)
        next_open_date = now_dhaka.date() + timedelta(days=2) if is_past_cutoff else applicable_date
    else:
        applicable_date = now_dhaka.date()
        next_open_date = now_dhaka.date() + timedelta(days=1) if is_past_cutoff else applicable_date

    cutoff_display = today_cutoff.strftime("%I:%M %p").lstrip("0")
    local_ip = get_local_ip()

    return {
        "now_formatted": now_dhaka.strftime("%d %b %Y, %I:%M:%S %p"),
        "today_str": now_dhaka.date().isoformat(),
        "cutoff_time": settings["cutoff_time"],
        "cutoff_display": cutoff_display,
        "cutoff_target": settings["cutoff_target"],
        "meal_rate": settings["meal_rate"],
        "is_past_cutoff": is_past_cutoff,
        "remaining_seconds": remaining_seconds,
        "applicable_date": applicable_date.isoformat(),
        "active_edit_date": next_open_date.isoformat(),
        "local_ip": local_ip,
        "wifi_link": f"http://{local_ip}:5000"
    }


def can_modify_date(conn, target_date_str: str, is_admin: bool) -> tuple[bool, str]:
    if is_admin:
        return True, "Admin override permitted."
    ctx = get_cutoff_context(conn)
    if target_date_str < ctx["applicable_date"]:
        return False, "Cannot modify past or same-day meals after pre-day deadline."
    if target_date_str == ctx["applicable_date"] and ctx["is_past_cutoff"]:
        return False, f"Meal selection closed at {ctx['cutoff_display']} (Asia/Dhaka)."
    return True, "Allowed"


HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Bachelor Flat Meal Management System</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <style>
    body {
      font-family: 'Plus Jakarta Sans', sans-serif;
      background-color: #FFF1E6;
      background-image: linear-gradient(to bottom right, rgba(255, 241, 230, 0.95), rgba(240, 239, 235, 0.95)),
                        url('https://images.unsplash.com/photo-1543353071-10c8ba85a904?auto=format&fit=crop&w=1600&q=80');
      background-size: cover;
      background-position: center;
      background-attachment: fixed;
    }
  </style>
</head>
<body class="text-[#2B2623] antialiased min-h-screen flex flex-col">

  <!-- Top Header -->
  <header class="bg-[#2B2623] text-[#FFF1E6] shadow-md sticky top-0 z-30 border-b border-[#CB997E]/40">
    <div class="max-w-7xl mx-auto px-4 py-3 flex flex-wrap items-center justify-between gap-3">
      <div class="flex items-center gap-3">
        <div class="h-10 w-10 rounded-xl bg-[#CB997E] flex items-center justify-center font-extrabold text-[#2B2623] text-base">
          BM
        </div>
        <div>
          <h1 class="font-bold text-base sm:text-lg leading-tight">Bachelor Flat Meal Manager</h1>
          <p class="text-xs text-[#DDBEA9]">Asia/Dhaka (UTC+6): {{ ctx.now_formatted }}</p>
        </div>
      </div>

      <div class="flex flex-wrap items-center gap-2 text-xs">
        <button onclick="openShareModal()"
          class="px-3 py-1.5 rounded-xl font-semibold bg-[#CB997E] text-[#2B2623] hover:bg-[#b88468] transition flex items-center gap-1.5">
          Share App & 24/7 Cloud Guide
        </button>
        {% if current_user %}
        <div class="flex items-center gap-2 bg-[#3D3530] px-3 py-1.5 rounded-xl border border-[#DDBEA9]/40">
          <span class="text-[#DDBEA9]">Signed in:</span>
          <strong>{{ current_user.full_name }}</strong>
          <button onclick="logoutUser()" class="ml-2 px-2 py-0.5 rounded bg-rose-700 hover:bg-rose-600 text-white font-semibold">
            Logout
          </button>
        </div>
        {% endif %}
      </div>
    </div>
  </header>

  <!-- Share Modal with Cloud Hosting Guide -->
  <div id="share-modal" class="fixed inset-0 z-50 hidden items-center justify-center bg-[#2B2623]/60 backdrop-blur-xs p-4">
    <div class="bg-[#FFF1E6] rounded-3xl max-w-xl w-full p-6 sm:p-7 shadow-2xl border border-[#DDBEA9] space-y-4 max-h-[90vh] overflow-y-auto">
      <div class="flex items-center justify-between">
        <h3 class="text-xl font-bold text-[#2B2623]">24/7 Cloud Access (Mobile Data & Offline Ready)</h3>
        <button onclick="closeShareModal()" class="text-[#3D3530] hover:text-[#2B2623] text-2xl font-bold">&times;</button>
      </div>

      <div class="space-y-3 text-xs sm:text-sm text-[#3D3530]">
        <div class="bg-[#F0EFEB] p-3.5 rounded-2xl border border-[#DDBEA9] space-y-1.5">
          <div class="font-bold text-[#2B2623]">1. Same Wi-Fi Local Link (When your PC is on):</div>
          <p>Flatmates on your same Wi-Fi can open:</p>
          <div class="font-mono font-bold text-xs bg-[#EDDCD2] p-2 rounded-lg text-indigo-900 select-all">
            {{ ctx.wifi_link }}
          </div>
        </div>

        <div class="bg-[#F0EFEB] p-3.5 rounded-2xl border border-[#DDBEA9] space-y-2">
          <div class="font-bold text-[#2B2623]">2. Permanent 24/7 Cloud Hosting (Works on Mobile Data / PC Off):</div>
          <p>To access the site even when your computer is shut down or your flatmates are using mobile data from outside, deploy this single <code class="bg-[#EDDCD2] px-1 rounded font-mono">app.py</code> file for free on <strong>Render.com</strong>:</p>
          <ol class="list-decimal list-inside space-y-1.5 text-xs pl-1">
            <li>Create a free account on <a href="https://render.com" target="_blank" class="text-blue-700 underline font-semibold">Render.com</a> and upload your <code class="bg-[#EDDCD2] px-1 rounded font-mono">app.py</code> to a GitHub repository.</li>
            <li>Click <strong>New +</strong> -> <strong>Web Service</strong> and link your GitHub repository.</li>
            <li>Set <strong>Build Command</strong> to: <code class="bg-[#EDDCD2] px-1 rounded font-mono">pip install flask tzdata</code></li>
            <li>Set <strong>Start Command</strong> to: <code class="bg-[#EDDCD2] px-1 rounded font-mono">python app.py</code></li>
            <li>Click <strong>Create Web Service</strong>. Render will provide a permanent link (e.g. <code class="bg-[#EDDCD2] px-1 rounded font-mono">https://bachelor-meal-manager.onrender.com</code>) that works 24/7 on mobile data from any phone!</li>
          </ol>
        </div>
      </div>

      <div class="pt-2 flex justify-end">
        <button onclick="closeShareModal()" class="px-5 py-2.5 rounded-xl text-sm font-semibold bg-[#2B2623] text-[#FFF1E6] hover:bg-[#3D3530]">
          Got It
        </button>
      </div>
    </div>
  </div>

  {% if not current_user %}
  <!-- Login Screen -->
  <main class="flex-1 flex items-center justify-center p-4">
    <div class="bg-[#FFF1E6] rounded-3xl max-w-md w-full p-8 shadow-2xl border border-[#CB997E]/60 space-y-5">
      <div class="text-center">
        <div class="h-14 w-14 rounded-2xl bg-[#CB997E] mx-auto flex items-center justify-center font-extrabold text-[#2B2623] text-xl">
          BM
        </div>
        <h2 class="text-2xl font-extrabold text-[#2B2623] mt-3">Member Profile Login</h2>
        <p class="text-xs text-[#3D3530] mt-1">Sign in with your private username and password.</p>
      </div>

      <form onsubmit="loginUser(event)" class="space-y-4">
        <div>
          <label class="block text-xs font-bold text-[#3D3530] mb-1">Username</label>
          <input type="text" id="username" required placeholder="tanu, shovan, jubayer, rahat..."
            class="w-full rounded-xl border border-[#DDBEA9] bg-white px-4 py-2.5 text-sm font-semibold focus:outline-none focus:ring-2 focus:ring-[#CB997E]" />
        </div>
        <div>
          <label class="block text-xs font-bold text-[#3D3530] mb-1">Password</label>
          <input type="password" id="password" required placeholder="••••••••"
            class="w-full rounded-xl border border-[#DDBEA9] bg-white px-4 py-2.5 text-sm font-semibold focus:outline-none focus:ring-2 focus:ring-[#CB997E]" />
        </div>
        <div id="login-error" class="hidden text-xs font-semibold text-rose-700 bg-rose-100 p-2.5 rounded-xl"></div>
        
        <button type="submit" class="w-full py-3 bg-[#CB997E] hover:bg-[#b88468] text-[#2B2623] font-extrabold text-sm rounded-xl shadow-md transition">
          Sign In
        </button>
      </form>
    </div>
  </main>
  {% else %}

  <!-- Dashboard -->
  <main class="max-w-7xl mx-auto px-4 py-6 space-y-7 flex-1 w-full">

    <section class="rounded-2xl p-5 border shadow-xs bg-[#F0EFEB] border-[#DDBEA9]">
      <div class="flex items-center justify-between">
        <div>
          <span class="px-2.5 py-0.5 rounded-full text-xs font-bold bg-[#A5A58D]/30 text-[#2B2623]">
            {{ 'CLOSED FOR ' + ctx.applicable_date if ctx.is_past_cutoff else 'OPEN FOR ' + ctx.applicable_date }}
          </span>
          <h2 class="text-xl font-extrabold text-[#2B2623] mt-1">Meal Selection Closes at {{ ctx.cutoff_display }}</h2>
        </div>
        <div class="bg-[#2B2623] text-[#FFF1E6] p-3 rounded-2xl text-right">
          <div class="text-[10px] text-[#DDBEA9]">Remaining</div>
          <div id="countdown-timer" class="text-xl font-mono font-bold text-[#B7B7A4]">--:--:--</div>
        </div>
      </div>
    </section>

    <!-- Profiles -->
    <section>
      <h2 class="text-lg font-bold text-[#2B2623] mb-3">
        {{ 'All Flat Member Profiles (Admin Master View)' if current_user.role == 'ADMIN' else 'My Private Profile & Status' }}
      </h2>
      <div class="grid grid-cols-1 {{ 'sm:grid-cols-2 lg:grid-cols-5' if current_user.role == 'ADMIN' else 'max-w-sm' }} gap-4">
        {% for m in visible_member_cards %}
        <div class="bg-[#F0EFEB] rounded-2xl border border-[#DDBEA9] p-4 shadow-xs flex flex-col justify-between">
          <div>
            <div class="flex items-center gap-3">
              <div class="h-11 w-11 rounded-full bg-[#2B2623] text-[#FFF1E6] flex items-center justify-center font-bold text-sm">
                {{ m.full_name[0] }}
              </div>
              <div>
                <h3 class="font-bold text-sm">{{ m.full_name }} {% if m.role == 'ADMIN' %}<span class="text-[10px] px-1 py-0.5 rounded bg-[#CB997E] text-[#2B2623]">ADMIN</span>{% endif %}</h3>
                <p class="text-xs text-[#CB997E]">{{ m.phone_number or 'No phone' }}</p>
              </div>
            </div>
          </div>
          <div class="mt-3 pt-3 border-t border-[#DDBEA9] text-xs space-y-1">
            <div class="flex justify-between"><span>Monthly Meals:</span><strong>{{ m.total_meals }}</strong></div>
            <div class="flex justify-between"><span>Payable:</span><strong>৳{{ m.payable }} BDT</strong></div>
          </div>
        </div>
        {% endfor %}
      </div>
    </section>

    <!-- Meal Selection -->
    <section class="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <div class="bg-[#F0EFEB] rounded-2xl border border-[#DDBEA9] p-6 space-y-4">
        <div class="flex items-center justify-between">
          <h3 class="text-lg font-bold">Daily Meal Selection</h3>
          <input type="date" id="meal_date" value="{{ ctx.active_edit_date }}" class="rounded-xl border border-[#CB997E] px-2.5 py-1 text-xs font-bold bg-[#EDDCD2]" />
        </div>
        <div class="space-y-3">
          <label class="flex items-center justify-between p-4 rounded-xl border border-[#DDBEA9] bg-[#FFF1E6] cursor-pointer">
            <span class="font-bold text-sm">Lunch</span>
            <input type="checkbox" id="lunch_status" class="w-5 h-5 accent-[#CB997E]" {{ 'checked' if my_card.target_lunch else '' }}>
          </label>
          <label class="flex items-center justify-between p-4 rounded-xl border border-[#DDBEA9] bg-[#FFF1E6] cursor-pointer">
            <span class="font-bold text-sm">Dinner</span>
            <input type="checkbox" id="dinner_status" class="w-5 h-5 accent-[#CB997E]" {{ 'checked' if my_card.target_dinner else '' }}>
          </label>
        </div>
        <button onclick="saveDailyMeal()" class="w-full py-2.5 bg-[#CB997E] text-[#2B2623] font-bold text-sm rounded-xl">
          Confirm Selection
        </button>
      </div>

      <!-- Guest Meals -->
      <div class="bg-[#F0EFEB] rounded-2xl border border-[#DDBEA9] p-6 space-y-4">
        <h3 class="text-lg font-bold">Add Guest Meals</h3>
        <form onsubmit="submitGuestMeal(event)" class="space-y-3">
          <div class="grid grid-cols-3 gap-2">
            <input type="date" id="guest_date" value="{{ ctx.active_edit_date }}" required class="rounded-xl border border-[#DDBEA9] bg-[#FFF1E6] p-2 text-xs" />
            <input type="number" id="guest_lunch_qty" min="0" value="0" required class="rounded-xl border border-[#DDBEA9] bg-[#FFF1E6] p-2 text-xs" placeholder="Lunch Qty" />
            <input type="number" id="guest_dinner_qty" min="0" value="0" required class="rounded-xl border border-[#DDBEA9] bg-[#FFF1E6] p-2 text-xs" placeholder="Dinner Qty" />
          </div>
          <input type="text" id="guest_name" placeholder="Guest Name (Optional)" class="w-full rounded-xl border border-[#DDBEA9] bg-[#FFF1E6] p-2 text-xs" />
          <button type="submit" class="w-full py-2.5 bg-[#2B2623] text-[#FFF1E6] text-sm font-semibold rounded-xl">
            Add Guest Meals
          </button>
        </form>
      </div>
    </section>

  </main>
  {% endif %}

  <script>
    function openShareModal() { document.getElementById("share-modal").classList.replace("hidden", "flex"); }
    function closeShareModal() { document.getElementById("share-modal").classList.replace("flex", "hidden"); }

    async function loginUser(e) {
      e.preventDefault();
      const res = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: document.getElementById("username").value,
          password: document.getElementById("password").value
        })
      });
      if (!res.ok) {
        const data = await res.json();
        const err = document.getElementById("login-error");
        err.textContent = data.error;
        err.classList.remove("hidden");
        return;
      }
      location.reload();
    }

    async function logoutUser() {
      await fetch("/api/logout", { method: "POST" });
      location.reload();
    }

    let remaining = {{ ctx.remaining_seconds }};
    const timerEl = document.getElementById("countdown-timer");
    if (timerEl) {
      setInterval(() => {
        if (remaining <= 0) { timerEl.textContent = "CLOSED"; return; }
        const h = String(Math.floor(remaining / 3600)).padStart(2, "0");
        const m = String(Math.floor((remaining % 3600) / 60)).padStart(2, "0");
        const s = String(remaining % 60).padStart(2, "0");
        timerEl.textContent = `${h}h ${m}m ${s}s`;
        remaining--;
      }, 1000);
    }

    async function saveDailyMeal() {
      const res = await fetch("/api/meals/select", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          meal_date: document.getElementById("meal_date").value,
          lunch_status: document.getElementById("lunch_status").checked,
          dinner_status: document.getElementById("dinner_status").checked
        })
      });
      const data = await res.json();
      if (!res.ok) return alert(data.error);
      location.reload();
    }

    async function submitGuestMeal(e) {
      e.preventDefault();
      const res = await fetch("/api/guests", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          meal_date: document.getElementById("guest_date").value,
          guest_lunch_qty: document.getElementById("guest_lunch_qty").value,
          guest_dinner_qty: document.getElementById("guest_dinner_qty").value,
          guest_name: document.getElementById("guest_name").value,
          idempotency_key: String(Date.now())
        })
      });
      const data = await res.json();
      if (!res.ok) return alert(data.error);
      location.reload();
    }
  </script>
</body>
</html>
"""


@app.route("/")
def index():
    conn = get_db()
    ctx = get_cutoff_context(conn)
    user_id = session.get("user_id")
    current_user = conn.execute("SELECT * FROM members WHERE id = ?", (user_id,)).fetchone() if user_id else None

    if not current_user:
        conn.close()
        return render_template_string(HTML_TEMPLATE, current_user=None, ctx=ctx)

    today_str = ctx["today_str"]
    month_prefix = today_str[:7]
    members_rows = conn.execute("SELECT * FROM members ORDER BY id ASC").fetchall()

    all_member_cards = []
    for m in members_rows:
        mid = m["id"]
        today_meal = conn.execute("SELECT * FROM daily_meals WHERE member_id = ? AND meal_date = ?", (mid, today_str)).fetchone()
        target_meal = conn.execute("SELECT * FROM daily_meals WHERE member_id = ? AND meal_date = ?", (mid, ctx["active_edit_date"])).fetchone()

        reg_stats = conn.execute(
            "SELECT COALESCE(SUM(lunch_status), 0) AS rl, COALESCE(SUM(dinner_status), 0) AS rd FROM daily_meals WHERE member_id = ? AND meal_date LIKE ?",
            (mid, f"{month_prefix}%"),
        ).fetchone()

        guest_stats = conn.execute(
            "SELECT COALESCE(SUM(guest_lunch_qty), 0) AS gl, COALESCE(SUM(guest_dinner_qty), 0) AS gd FROM guest_meals WHERE member_id = ? AND meal_date LIKE ?",
            (mid, f"{month_prefix}%"),
        ).fetchone()

        rl, rd = int(reg_stats["rl"]), int(reg_stats["rd"])
        gl, gd = int(guest_stats["gl"]), int(guest_stats["gd"])
        total_meals = rl + rd + gl + gd

        all_member_cards.append({
            "id": mid,
            "full_name": m["full_name"],
            "phone_number": m["phone_number"],
            "role": m["role"],
            "account_status": m["account_status"],
            "target_lunch": bool(target_meal["lunch_status"]) if target_meal else False,
            "target_dinner": bool(target_meal["dinner_status"]) if target_meal else False,
            "total_meals": total_meals,
            "payable": round(total_meals * ctx["meal_rate"], 2),
        })

    visible_member_cards = all_member_cards if current_user["role"] == "ADMIN" else [c for c in all_member_cards if c["id"] == current_user["id"]]
    my_card = next(c for c in all_member_cards if c["id"] == current_user["id"])
    conn.close()

    return render_template_string(
        HTML_TEMPLATE,
        current_user=current_user,
        ctx=ctx,
        visible_member_cards=visible_member_cards,
        my_card=my_card,
    )


@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json()
    uname = (data.get("username") or "").strip().lower()
    pwd = (data.get("password") or "").strip()
    conn = get_db()
    user = conn.execute("SELECT * FROM members WHERE username = ? AND password = ?", (uname, pwd)).fetchone()
    conn.close()
    if not user:
        return jsonify({"error": "Invalid username or password."}), 401
    session["user_id"] = user["id"]
    return jsonify({"ok": True})


@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.pop("user_id", None)
    return jsonify({"ok": True})


@app.route("/api/meals/select", methods=["POST"])
def api_select_meal():
    data = request.get_json()
    conn = get_db()
    user = conn.execute("SELECT * FROM members WHERE id = ?", (session.get("user_id"),)).fetchone()
    if not user:
        conn.close()
        return jsonify({"error": "Not authenticated."}), 401

    meal_date = data["meal_date"]
    allowed, msg = can_modify_date(conn, meal_date, user["role"] == "ADMIN")
    if not allowed:
        conn.close()
        return jsonify({"error": msg}), 403

    lunch = 1 if data.get("lunch_status") else 0
    dinner = 1 if data.get("dinner_status") else 0
    now_str = get_dhaka_now().strftime("%Y-%m-%d %I:%M:%S %p")

    existing = conn.execute("SELECT * FROM daily_meals WHERE member_id = ? AND meal_date = ?", (user["id"], meal_date)).fetchone()
    if existing:
        conn.execute("UPDATE daily_meals SET lunch_status = ?, dinner_status = ?, confirmed_at = ? WHERE id = ?", (lunch, dinner, now_str, existing["id"]))
    else:
        conn.execute("INSERT INTO daily_meals (member_id, meal_date, lunch_status, dinner_status, confirmed_at) VALUES (?, ?, ?, ?, ?)", (user["id"], meal_date, lunch, dinner, now_str))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/guests", methods=["POST"])
def api_add_guest():
    data = request.get_json()
    conn = get_db()
    user = conn.execute("SELECT * FROM members WHERE id = ?", (session.get("user_id"),)).fetchone()
    if not user:
        conn.close()
        return jsonify({"error": "Not authenticated."}), 401

    meal_date = data["meal_date"]
    gl = int(data.get("guest_lunch_qty", 0))
    gd = int(data.get("guest_dinner_qty", 0))
    if gl == 0 and gd == 0:
        conn.close()
        return jsonify({"error": "Enter at least 1 guest meal."}), 400

    now_str = get_dhaka_now().strftime("%Y-%m-%d %I:%M:%S %p")
    conn.execute(
        "INSERT INTO guest_meals (member_id, meal_date, guest_lunch_qty, guest_dinner_qty, guest_name, idempotency_key, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (user["id"], meal_date, gl, gd, data.get("guest_name"), data.get("idempotency_key"), now_str, now_str)
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


init_db()

if __name__ == "__main__":
    print(f"\n[*] Bachelor Flat Meal Manager running on Local Wi-Fi: http://{get_local_ip()}:5000\n")
    app.run(debug=True, host="0.0.0.0", port=5000)