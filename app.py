import os, re, json, time
from datetime import datetime, timedelta
import psycopg2
import redis
from werkzeug.security import generate_password_hash, check_password_hash
from markupsafe import escape
from urllib.parse import quote
from flask import Flask, request, redirect, session, jsonify

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me-secret")

DB_CFG = dict(
    host=os.environ.get("DB_HOST", "pe-db"),
    dbname=os.environ.get("DB_NAME", "parking"),
    user=os.environ.get("DB_USER", "parking"),
    password=os.environ.get("DB_PASSWORD", "parking123"),
)
cache = redis.Redis(host=os.environ.get("REDIS_HOST", "pe-cache"), port=6379, decode_responses=True,
                    socket_connect_timeout=2, socket_timeout=2)
DURATIONS = {30: "30 minutes", 60: "1 hour", 120: "2 hours", 240: "4 hours", 480: "8 hours"}

def get_db():
    return psycopg2.connect(**DB_CFG)

def cache_get(key):
    try:
        return cache.get(key)
    except Exception:
        return None

def cache_set(key, val, ttl):
    try:
        cache.setex(key, ttl, val)
    except Exception:
        pass

def init_db():
    for attempt in range(40):
        try:
            con = get_db()
            cur = con.cursor()
            cur.execute("CREATE TABLE IF NOT EXISTS users (id SERIAL PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, role TEXT DEFAULT 'user')")
            cur.execute("CREATE TABLE IF NOT EXISTS slots (id SERIAL PRIMARY KEY, zone TEXT, number INT, stype TEXT, rate INT)")
            cur.execute("CREATE TABLE IF NOT EXISTS bookings (id SERIAL PRIMARY KEY, user_id INT, slot_id INT, vehicle TEXT, start_time TIMESTAMP, end_time TIMESTAMP, amount NUMERIC, status TEXT DEFAULT 'Active')")
            cur.execute("SELECT COUNT(*) FROM slots")
            if cur.fetchone()[0] == 0:
                for zone, stype, rate in (("A", "Car", 40), ("B", "Bike", 15), ("C", "EV", 60)):
                    for n in range(1, 9):
                        cur.execute("INSERT INTO slots (zone, number, stype, rate) VALUES (%s, %s, %s, %s)", (zone, n, stype, rate))
            cur.execute("INSERT INTO users (username, password_hash, role) VALUES (%s, %s, 'admin') ON CONFLICT DO NOTHING",
                        ("admin", generate_password_hash(os.environ.get("ADMIN_PASSWORD", "admin123"))))
            con.commit()
            con.close()
            print("Database ready", flush=True)
            return
        except Exception as e:
            print("Waiting for database...", e, flush=True)
            time.sleep(3)
    raise SystemExit("Database not reachable")

def slot_rows(n):
    con = get_db()
    cur = con.cursor()
    cur.execute("""SELECT s.id, s.zone, s.number, s.stype, s.rate, b.user_id, b.vehicle, b.end_time
        FROM slots s LEFT JOIN bookings b ON b.slot_id = s.id AND b.status = 'Active' AND b.start_time <= %s AND b.end_time > %s
        ORDER BY s.zone, s.number""", (n, n))
    rows = cur.fetchall()
    con.close()
    return rows

def get_stats():
    cached = cache_get("stats")
    if cached:
        return json.loads(cached), True
    rows = slot_rows(datetime.now())
    total = len(rows)
    busy = sum(1 for r in rows if r[7] is not None)
    by_zone = {}
    for r in rows:
        z = by_zone.setdefault(r[1], {"total": 0, "busy": 0})
        z["total"] += 1
        if r[7] is not None:
            z["busy"] += 1
    data = dict(total=total, occupied=busy, free=total - busy, occupancy=int(busy * 100 / total) if total else 0, by_zone=by_zone)
    cache_set("stats", json.dumps(data), 5)
    return data, False

def status_of(st, end, n):
    if st == "Cancelled":
        return "Cancelled"
    if end <= n:
        return "Completed"
    return "Active"

def booking_row(b, n, show_user=False):
    bid, zone, num, veh, start, end, amount, st = b[:8]
    status = status_of(st, end, n)
    cls = {"Active": "ok", "Completed": "mut", "Cancelled": "bad"}[status]
    who = f"<td>{escape(b[8])}</td>" if show_user else ""
    act = ""
    if status == "Active":
        act = f"<form method='post' action='/cancel/{bid}' onsubmit=\"return confirm('Cancel this booking?')\"><button class='rm'>{'Release' if show_user else 'Cancel'}</button></form>"
    return (f"<tr>{who}<td><b>{zone}{num}</b></td><td>{escape(veh)}</td><td>{start:%d %b %H:%M}</td><td>{end:%d %b %H:%M}</td>"
            f"<td>&#8377;{float(amount):.0f}</td><td><span class='pill {cls}'>{status}</span></td><td>{act}</td></tr>")

AUTH_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>ParkEasy - Sign in</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',system-ui,-apple-system,Roboto,Arial,sans-serif;min-height:100vh;display:flex;align-items:center;justify-content:center;
background:radial-gradient(1200px 600px at 10% 10%,#064e3b 0%,transparent 60%),radial-gradient(900px 500px at 90% 90%,#0e7490 0%,transparent 60%),#0b1020;color:#e5e7eb;padding:20px}
.card{width:100%;max-width:420px;background:rgba(255,255,255,.06);backdrop-filter:blur(14px);border:1px solid rgba(255,255,255,.12);border-radius:20px;padding:34px;box-shadow:0 20px 60px rgba(0,0,0,.45);animation:rise .6s ease}
@keyframes rise{from{opacity:0;transform:translateY(20px)}to{opacity:1;transform:none}}
.logo{display:flex;align-items:center;gap:10px;font-size:22px;font-weight:700;margin-bottom:6px}
.logo span{width:38px;height:38px;border-radius:11px;background:linear-gradient(135deg,#10b981,#06b6d4);display:grid;place-items:center;font-size:20px;font-weight:800}
.sub{color:#9ca3af;font-size:14px;margin-bottom:22px}
.tabs{display:flex;background:rgba(255,255,255,.06);border-radius:12px;padding:4px;margin-bottom:20px}
.tabs button{flex:1;padding:10px;border:0;background:transparent;color:#9ca3af;font-weight:600;border-radius:9px;cursor:pointer;transition:.25s;font-size:14px}
.tabs button.active{background:linear-gradient(135deg,#10b981,#06b6d4);color:#fff;box-shadow:0 6px 18px rgba(16,185,129,.35)}
label{display:block;font-size:12px;color:#9ca3af;margin:12px 0 6px;letter-spacing:.4px;text-transform:uppercase}
.field{position:relative}
input{width:100%;padding:13px 14px;border-radius:11px;border:1px solid rgba(255,255,255,.14);background:rgba(0,0,0,.25);color:#fff;font-size:15px;outline:none;transition:.2s}
input:focus{border-color:#10b981;box-shadow:0 0 0 3px rgba(16,185,129,.25)}
.eye{position:absolute;right:10px;top:50%;transform:translateY(-50%);background:none;border:0;color:#9ca3af;cursor:pointer;font-size:13px}
.btn{width:100%;margin-top:20px;padding:13px;border:0;border-radius:11px;font-size:15px;font-weight:700;color:#fff;cursor:pointer;background:linear-gradient(135deg,#10b981,#06b6d4);transition:.25s;box-shadow:0 10px 24px rgba(16,185,129,.35)}
.btn:hover{transform:translateY(-2px);box-shadow:0 14px 30px rgba(6,182,212,.35)}
.alert{padding:11px 13px;border-radius:10px;font-size:14px;margin-bottom:14px}
.ok{background:rgba(16,185,129,.15);border:1px solid rgba(16,185,129,.4);color:#6ee7b7}
.bad{background:rgba(239,68,68,.15);border:1px solid rgba(239,68,68,.4);color:#fca5a5}
form{display:none}form.show{display:block;animation:rise .35s ease}
.foot{text-align:center;color:#6b7280;font-size:12px;margin-top:22px}
</style></head><body>
<div class="card">
<div class="logo"><span>P</span>ParkEasy</div>
<p class="sub">Find. Book. Park. Smart parking made simple.</p>
__ALERT__
<div class="tabs"><button id="tl" class="active" onclick="tab('login')">Login</button><button id="ts" onclick="tab('signup')">Sign up</button></div>
<form id="login" class="show" method="post" action="/login">
<label>Username</label><input name="username" required autocomplete="username">
<label>Password</label><div class="field"><input name="password" type="password" required autocomplete="current-password"><button type="button" class="eye" onclick="eye(this)">Show</button></div>
<button class="btn">Login</button>
</form>
<form id="signup" method="post" action="/signup">
<label>Choose a username</label><input name="username" required minlength="3" autocomplete="username" placeholder="at least 3 characters">
<label>Choose a password</label><div class="field"><input name="password" type="password" required minlength="4" autocomplete="new-password" placeholder="at least 4 characters"><button type="button" class="eye" onclick="eye(this)">Show</button></div>
<button class="btn">Create account</button>
</form>
<p class="foot">Powered by Podman &middot; Red Hat UBI &middot; PostgreSQL &middot; Redis</p>
</div>
<script>
function tab(t){var l=document.getElementById('login'),s=document.getElementById('signup');
l.className=t==='login'?'show':'';s.className=t==='signup'?'show':'';
document.getElementById('tl').className=t==='login'?'active':'';document.getElementById('ts').className=t==='signup'?'active':'';}
function eye(b){var i=b.previousElementSibling;i.type=i.type==='password'?'text':'password';b.textContent=i.type==='password'?'Show':'Hide';}
</script></body></html>"""

DASH_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>ParkEasy</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#0b1020;--card:#121a33;--line:rgba(255,255,255,.08);--mut:#9ca3af;--grad:linear-gradient(135deg,#10b981,#06b6d4)}
body{font-family:'Segoe UI',system-ui,-apple-system,Roboto,Arial,sans-serif;background:radial-gradient(1000px 500px at 85% -10%,#064e3b 0%,transparent 60%),var(--bg);color:#e5e7eb;min-height:100vh}
.nav{position:sticky;top:0;z-index:5;display:flex;align-items:center;justify-content:space-between;padding:14px 28px;background:rgba(11,16,32,.75);backdrop-filter:blur(12px);border-bottom:1px solid var(--line)}
.brand{display:flex;align-items:center;gap:10px;font-weight:700;font-size:19px}
.brand i{width:34px;height:34px;border-radius:10px;background:var(--grad);display:grid;place-items:center;font-style:normal;font-weight:800}
.user{display:flex;align-items:center;gap:12px;font-size:14px;color:var(--mut)}
.av{width:36px;height:36px;border-radius:50%;background:var(--grad);display:grid;place-items:center;color:#fff;font-weight:700}
.role{font-size:11px;padding:3px 9px;border-radius:99px;background:rgba(16,185,129,.18);color:#6ee7b7;text-transform:uppercase;letter-spacing:.5px}
.out{color:#e5e7eb;text-decoration:none;padding:8px 14px;border:1px solid var(--line);border-radius:10px;transition:.2s;font-size:13px}
.out:hover{background:rgba(255,255,255,.08)}
.wrap{max-width:1100px;margin:0 auto;padding:28px 20px 60px}
h1{font-size:26px;margin-bottom:4px}.hint{color:var(--mut);font-size:14px;margin-bottom:22px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px;margin-bottom:26px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:18px;transition:.25s;animation:rise .5s ease both}
.stat:hover{transform:translateY(-3px);border-color:rgba(16,185,129,.5)}
.stat small{color:var(--mut);font-size:12px;text-transform:uppercase;letter-spacing:.5px}
.stat b{display:block;font-size:28px;margin-top:6px}
.meter{height:8px;background:rgba(255,255,255,.08);border-radius:99px;margin-top:10px;overflow:hidden}
.meter div{height:100%;background:var(--grad);border-radius:99px;transition:width .8s ease}
.zrow{display:flex;justify-content:space-between;font-size:12px;color:var(--mut);margin-top:8px}
.legend{display:flex;gap:18px;font-size:13px;color:var(--mut);margin:6px 0 14px;flex-wrap:wrap}
.legend span:before{content:'';display:inline-block;width:11px;height:11px;border-radius:4px;margin-right:6px;vertical-align:-1px}
.lf:before{background:#10b981}.lb:before{background:#ef4444}.lm:before{background:#818cf8}
.zone{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:18px;margin-bottom:16px;animation:rise .5s ease both}
.zone h3{margin-bottom:12px;font-size:16px}.zone h3 small{color:var(--mut);font-weight:400;margin-left:8px}
.slots{display:grid;grid-template-columns:repeat(auto-fill,minmax(74px,1fr));gap:10px}
.slot{height:64px;border-radius:12px;display:grid;place-items:center;font-weight:700;border:1px solid;transition:.2s;user-select:none}
.slot.free{background:rgba(16,185,129,.12);border-color:rgba(16,185,129,.5);color:#6ee7b7;cursor:pointer}
.slot.free:hover{transform:translateY(-4px) scale(1.05);background:rgba(16,185,129,.28);box-shadow:0 10px 22px rgba(16,185,129,.25)}
.slot.busy{background:rgba(239,68,68,.12);border-color:rgba(239,68,68,.45);color:#fca5a5;cursor:not-allowed}
.slot.mine{background:rgba(99,102,241,.25);border-color:#818cf8;color:#c7d2fe;box-shadow:0 0 0 3px rgba(99,102,241,.2)}
h2{font-size:18px;margin:26px 0 12px}
.tbl{width:100%;border-collapse:collapse;background:var(--card);border-radius:14px;overflow:hidden;font-size:14px}
.tbl th,.tbl td{padding:11px 12px;text-align:left;border-bottom:1px solid var(--line)}
.tbl th{color:var(--mut);font-size:12px;text-transform:uppercase;letter-spacing:.5px}
.tbl tr:hover td{background:rgba(255,255,255,.03)}
.pill{padding:3px 10px;border-radius:99px;font-size:12px;font-weight:600}
.pill.ok{background:rgba(16,185,129,.18);color:#6ee7b7}.pill.mut{background:rgba(148,163,184,.18);color:#cbd5e1}.pill.bad{background:rgba(239,68,68,.18);color:#fca5a5}
.rm{background:rgba(239,68,68,.14);color:#fca5a5;border:0;padding:6px 12px;border-radius:8px;cursor:pointer;font-weight:600;transition:.2s}.rm:hover{background:rgba(239,68,68,.3)}
.empty{color:var(--mut);padding:20px;text-align:center}
.modal{position:fixed;inset:0;background:rgba(0,0,0,.6);backdrop-filter:blur(4px);display:none;align-items:center;justify-content:center;z-index:30;padding:16px}
.modal.open{display:flex}
.mbox{background:#121a33;border:1px solid var(--line);border-radius:18px;padding:26px;width:100%;max-width:400px;animation:rise .3s ease}
.mbox h3{margin-bottom:4px}.mbox p{color:var(--mut);font-size:13px}
.mbox label{display:block;font-size:12px;color:var(--mut);margin:14px 0 6px;text-transform:uppercase;letter-spacing:.4px}
.mbox input,.mbox select{width:100%;padding:12px;border-radius:10px;border:1px solid var(--line);background:rgba(0,0,0,.3);color:#fff;font-size:15px;outline:none}
.mbox input:focus,.mbox select:focus{border-color:#10b981;box-shadow:0 0 0 3px rgba(16,185,129,.25)}
.est{margin-top:14px;font-size:15px;color:#e5e7eb}
.row{display:flex;gap:10px;margin-top:18px}
.btn,.ghost{flex:1;padding:12px;border-radius:10px;border:0;font-weight:700;cursor:pointer;font-size:14px;transition:.2s}
.btn{background:var(--grad);color:#fff}.btn:hover{filter:brightness(1.1)}
.ghost{background:rgba(255,255,255,.08);color:#e5e7eb}
.toast{position:fixed;right:22px;bottom:22px;padding:13px 18px;border-radius:12px;font-size:14px;transform:translateY(90px);opacity:0;transition:.4s;z-index:40;max-width:340px}
.toast.show{transform:none;opacity:1}
.toast.okk{background:#064e3b;border:1px solid #10b981;color:#a7f3d0}.toast.bad2{background:#7f1d1d;border:1px solid #ef4444;color:#fecaca}
@keyframes rise{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:none}}
</style></head><body>
<div class="nav"><div class="brand"><i>P</i>ParkEasy</div>
<div class="user"><span class="role">__ROLE__</span><span>__USER__</span><div class="av">__INITIAL__</div><a class="out" href="/logout">Logout</a></div></div>
<div class="wrap">
<h1>Hello, __USER__</h1><p class="hint">Pick a free slot to book it. The map updates live every few seconds.</p>
<div class="stats">
<div class="stat"><small>Total slots</small><b>__TOTAL__</b></div>
<div class="stat"><small>Free now</small><b id="kFree">__FREE__</b></div>
<div class="stat"><small>Occupied</small><b id="kBusy">__BUSY__</b></div>
<div class="stat"><small>Occupancy</small><b id="kOcc">__OCC__%</b><div class="meter"><div id="kBar" style="width:__OCC__%"></div></div></div>
</div>
<div class="legend"><span class="lf">Free</span><span class="lb">Occupied</span><span class="lm">Your booking</span></div>
<div id="slotsWrap">__SLOTS__</div>
__ADMIN__
<h2>My bookings</h2>
<table class="tbl"><tr><th>Slot</th><th>Vehicle</th><th>From</th><th>Until</th><th>Amount</th><th>Status</th><th></th></tr>__MYROWS__</table>
</div>
<div class="modal" id="modal"><div class="mbox"><h3 id="mt">Book slot</h3><p id="ms"></p>
<form method="post" action="/book"><input type="hidden" name="slot_id" id="sid">
<label>Vehicle number</label><input name="vehicle" id="veh" placeholder="e.g. TN 07 AB 1234" required minlength="4">
<label>Duration</label><select name="minutes" id="mins">__DUR__</select>
<p class="est">Estimated price: <b id="est"></b></p>
<div class="row"><button type="button" class="ghost" onclick="closeBook()">Cancel</button><button class="btn">Confirm booking</button></div></form></div></div>
<div class="toast" id="toast" data-msg="__MSG__" data-err="__ERR__"></div>
<script>
var rate=0;
function setTxt(id,v){var e=document.getElementById(id);if(e)e.textContent=v}
function upd(){document.getElementById('est').textContent='\u20b9'+Math.round(rate*parseInt(document.getElementById('mins').value)/60)}
function openBook(el){rate=parseInt(el.dataset.rate);document.getElementById('sid').value=el.dataset.id;
document.getElementById('mt').textContent='Book slot '+el.dataset.label;
document.getElementById('ms').textContent=el.dataset.type+' slot - \u20b9'+rate+' per hour';
upd();document.getElementById('modal').className='modal open';document.getElementById('veh').focus()}
function closeBook(){document.getElementById('modal').className='modal'}
document.getElementById('mins').onchange=upd;
document.getElementById('slotsWrap').addEventListener('click',function(e){var el=e.target.closest('.slot');if(el&&el.classList.contains('free'))openBook(el)});
document.getElementById('modal').addEventListener('click',function(e){if(e.target===this)closeBook()});
function show(t,bad){var el=document.getElementById('toast');el.textContent=t;el.className='toast '+(bad?'bad2':'okk')+' show';setTimeout(function(){el.className='toast'},4500)}
var tt=document.getElementById('toast');if(tt.dataset.err)show(tt.dataset.err,true);else if(tt.dataset.msg)show(tt.dataset.msg,false);
function poll(){fetch('/api/slots').then(function(r){return r.json()}).then(function(d){
d.slots.forEach(function(s){var el=document.getElementById('slot-'+s.id);if(el){el.className='slot '+s.state;el.title=s.title}});
var st=d.stats;setTxt('kFree',st.free);setTxt('kBusy',st.occupied);setTxt('kOcc',st.occupancy+'%');document.getElementById('kBar').style.width=st.occupancy+'%';
}).catch(function(){})}
setInterval(poll,5000);
</script></body></html>"""

@app.route("/")
def home():
    msg = request.args.get("msg", "")
    err = request.args.get("err", "")
    uid = session.get("uid")
    if not uid:
        alert = ""
        if msg:
            alert = "<div class='alert ok'>" + str(escape(msg)) + "</div>"
        if err:
            alert = "<div class='alert bad'>" + str(escape(err)) + "</div>"
        return AUTH_HTML.replace("__ALERT__", alert)
    role = session.get("role", "user")
    n = datetime.now()
    rows = slot_rows(n)
    stats, _ = get_stats()
    zones = {}
    for sid, zone, num, stype, rate, buid, veh, end in rows:
        zones.setdefault(zone, {"type": stype, "rate": rate, "slots": []})["slots"].append((sid, num, buid, end))
    slots_html = ""
    for zone, z in zones.items():
        items = ""
        for sid, num, buid, end in z["slots"]:
            if end is None:
                state, title = "free", "Free - click to book"
            elif buid == uid:
                state, title = "mine", f"Your booking until {end:%H:%M}"
            else:
                state, title = "busy", f"Occupied until {end:%H:%M}"
            items += (f"<div class='slot {state}' id='slot-{sid}' data-id='{sid}' data-label='{zone}{num}' "
                      f"data-rate='{z['rate']}' data-type='{z['type']}' title='{title}'>{zone}{num}</div>")
        slots_html += (f"<div class='zone'><h3>Zone {zone}<small>{z['type']} &middot; &#8377;{z['rate']}/hr</small></h3>"
                       f"<div class='slots'>{items}</div></div>")
    con = get_db()
    cur = con.cursor()
    cur.execute("""SELECT b.id, s.zone, s.number, b.vehicle, b.start_time, b.end_time, b.amount, b.status
        FROM bookings b JOIN slots s ON s.id = b.slot_id WHERE b.user_id = %s ORDER BY b.id DESC LIMIT 10""", (uid,))
    mine = cur.fetchall()
    admin_html = ""
    if role == "admin":
        cur.execute("""SELECT b.id, s.zone, s.number, b.vehicle, b.start_time, b.end_time, b.amount, b.status, u.username
            FROM bookings b JOIN slots s ON s.id = b.slot_id JOIN users u ON u.id = b.user_id ORDER BY b.id DESC LIMIT 15""")
        allb = cur.fetchall()
        cur.execute("SELECT COALESCE(SUM(amount), 0), COUNT(*) FROM bookings WHERE status <> 'Cancelled'")
        revenue, count = cur.fetchone()
        arows = "".join(booking_row(b, n, True) for b in allb) or "<tr><td colspan='8' class='empty'>No bookings yet</td></tr>"
        zbars = "".join(
            f"<div class='zrow'><span>Zone {z}</span><span>{d['busy']}/{d['total']}</span></div>"
            f"<div class='meter'><div style='width:{int(d['busy'] * 100 / d['total'])}%'></div></div>"
            for z, d in stats["by_zone"].items())
        admin_html = (f"<h2>Admin overview</h2><div class='stats'>"
                      f"<div class='stat'><small>Total revenue</small><b>&#8377;{float(revenue):.0f}</b></div>"
                      f"<div class='stat'><small>Total bookings</small><b>{count}</b></div>"
                      f"<div class='stat'><small>Zone occupancy</small>{zbars}</div></div>"
                      f"<h2>All bookings</h2><table class='tbl'><tr><th>User</th><th>Slot</th><th>Vehicle</th><th>From</th><th>Until</th><th>Amount</th><th>Status</th><th></th></tr>{arows}</table>")
    con.close()
    myrows = "".join(booking_row(b, n) for b in mine) or "<tr><td colspan='7' class='empty'>No bookings yet. Click a green slot to book.</td></tr>"
    username = session.get("username", "")
    dur = "".join(f"<option value='{k}'{' selected' if k == 60 else ''}>{v}</option>" for k, v in DURATIONS.items())
    return (DASH_HTML.replace("__ROLE__", role)
            .replace("__USER__", str(escape(username)))
            .replace("__INITIAL__", str(escape(username[:1].upper())))
            .replace("__TOTAL__", str(stats["total"]))
            .replace("__FREE__", str(stats["free"]))
            .replace("__BUSY__", str(stats["occupied"]))
            .replace("__OCC__", str(stats["occupancy"]))
            .replace("__DUR__", dur)
            .replace("__MSG__", str(escape(msg)))
            .replace("__ERR__", str(escape(err)))
            .replace("__ADMIN__", admin_html)
            .replace("__MYROWS__", myrows)
            .replace("__SLOTS__", slots_html))

@app.route("/signup", methods=["POST"])
def signup():
    u = request.form["username"].strip()
    p = request.form["password"]
    if len(u) < 3 or len(p) < 4:
        return redirect("/?err=" + quote("Username min 3 and password min 4 characters"))
    con = get_db()
    cur = con.cursor()
    cur.execute("INSERT INTO users (username, password_hash) VALUES (%s, %s) ON CONFLICT DO NOTHING RETURNING id", (u, generate_password_hash(p)))
    row = cur.fetchone()
    con.commit()
    con.close()
    if not row:
        return redirect("/?err=" + quote("Username already taken"))
    return redirect("/?msg=" + quote("Account created. Please login."))

@app.route("/login", methods=["POST"])
def login():
    u = request.form["username"].strip()
    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT id, password_hash, role FROM users WHERE username = %s", (u,))
    row = cur.fetchone()
    con.close()
    if not row or not check_password_hash(row[1], request.form["password"]):
        return redirect("/?err=" + quote("Wrong username or password"))
    session["uid"] = row[0]
    session["username"] = u
    session["role"] = row[2] or "user"
    return redirect("/")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

@app.route("/book", methods=["POST"])
def book():
    uid = session.get("uid")
    if not uid:
        return redirect("/")
    try:
        sid = int(request.form["slot_id"])
        minutes = int(request.form["minutes"])
    except (KeyError, ValueError):
        return redirect("/?err=" + quote("Invalid booking request"))
    vehicle = re.sub(r"[^A-Za-z0-9 -]", "", request.form.get("vehicle", "")).upper().strip()
    if len(vehicle) < 4 or minutes not in DURATIONS:
        return redirect("/?err=" + quote("Enter a valid vehicle number and duration"))
    lock_key = f"lock:slot:{sid}"
    try:
        got = cache.set(lock_key, str(uid), nx=True, ex=10)
    except Exception:
        got = True
    if not got:
        return redirect("/?err=" + quote("Someone else is booking this slot right now. Please try again."))
    result = ("err", "Booking failed")
    con = get_db()
    try:
        cur = con.cursor()
        cur.execute("SELECT rate, zone, number FROM slots WHERE id = %s", (sid,))
        s = cur.fetchone()
        if not s:
            result = ("err", "Slot not found")
        else:
            start = datetime.now()
            end = start + timedelta(minutes=minutes)
            cur.execute("SELECT 1 FROM bookings WHERE slot_id = %s AND status = 'Active' AND start_time < %s AND end_time > %s LIMIT 1", (sid, end, start))
            if cur.fetchone():
                result = ("err", "That slot is already booked for this time")
            else:
                amount = round(s[0] * minutes / 60, 2)
                cur.execute("INSERT INTO bookings (user_id, slot_id, vehicle, start_time, end_time, amount) VALUES (%s, %s, %s, %s, %s, %s)",
                            (uid, sid, vehicle, start, end, amount))
                con.commit()
                result = ("msg", f"Booked slot {s[1]}{s[2]} for {DURATIONS[minutes]} - total Rs {amount:.0f}")
    finally:
        con.close()
        try:
            cache.delete(lock_key, "stats")
        except Exception:
            pass
    return redirect(f"/?{result[0]}=" + quote(result[1]))

@app.route("/cancel/<int:bid>", methods=["POST"])
def cancel(bid):
    uid = session.get("uid")
    if not uid:
        return redirect("/")
    is_admin = session.get("role") == "admin"
    con = get_db()
    cur = con.cursor()
    cur.execute("UPDATE bookings SET status = 'Cancelled' WHERE id = %s AND status = 'Active' AND (user_id = %s OR %s) RETURNING id", (bid, uid, is_admin))
    row = cur.fetchone()
    con.commit()
    con.close()
    try:
        cache.delete("stats")
    except Exception:
        pass
    if not row:
        return redirect("/?err=" + quote("Could not cancel that booking"))
    return redirect("/?msg=" + quote("Booking cancelled and slot released"))

@app.route("/api/slots")
def api_slots():
    uid = session.get("uid")
    if not uid:
        return jsonify(error="login required"), 401
    rows = slot_rows(datetime.now())
    out = []
    for sid, zone, num, stype, rate, buid, veh, end in rows:
        if end is None:
            out.append(dict(id=sid, state="free", title="Free - click to book"))
        elif buid == uid:
            out.append(dict(id=sid, state="mine", title=f"Your booking until {end:%H:%M}"))
        else:
            out.append(dict(id=sid, state="busy", title=f"Occupied until {end:%H:%M}"))
    stats, hit = get_stats()
    resp = jsonify(slots=out, stats=stats)
    resp.headers["X-Cache"] = "HIT" if hit else "MISS"
    return resp

@app.route("/api/stats")
def api_stats():
    stats, hit = get_stats()
    resp = jsonify(stats)
    resp.headers["X-Cache"] = "HIT" if hit else "MISS"
    return resp

@app.route("/health")
def health():
    status = {}
    try:
        con = get_db()
        con.cursor().execute("SELECT 1")
        con.close()
        status["database"] = "up"
    except Exception:
        status["database"] = "DOWN"
    try:
        cache.ping()
        status["redis"] = "up"
    except Exception:
        status["redis"] = "DOWN"
    code = 200 if all(v == "up" for v in status.values()) else 500
    return jsonify(status), code

init_db()
app.run(host="0.0.0.0", port=5000)
