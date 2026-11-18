import os, csv, io, sqlite3, uuid
from datetime import datetime, date
from functools import wraps
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, session, flash, abort, send_from_directory, make_response
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")
DB_PATH = Path(os.environ.get("DATABASE", BASE / "instance" / "recircle.db"))
UPLOAD_DIR = Path(os.environ.get("UPLOAD_FOLDER", BASE / "uploads"))
ALLOWED = {"png", "jpg", "jpeg", "webp"}
CATEGORIES = ["Paper", "Plastic", "Glass", "Metal", "E-waste", "Organic Waste"]
STATUSES = ["Pending", "Assigned", "Accepted", "In Progress", "Completed", "Cancelled"]
SLOTS = ["8 AM – 11 AM", "11 AM – 2 PM", "2 PM – 5 PM"]

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-me")
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(
          id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
          phone TEXT, password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('household','collector','admin')),
          active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS household_profiles(
          user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE, address TEXT
        );
        CREATE TABLE IF NOT EXISTS collector_profiles(
          user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
          availability TEXT NOT NULL DEFAULT 'Available', notes TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS pickup_requests(
          id INTEGER PRIMARY KEY AUTOINCREMENT, request_code TEXT UNIQUE NOT NULL,
          household_id INTEGER NOT NULL REFERENCES users(id), category TEXT NOT NULL,
          estimated_kg REAL NOT NULL CHECK(estimated_kg > 0), address TEXT NOT NULL,
          preferred_date TEXT NOT NULL, time_slot TEXT NOT NULL, instructions TEXT DEFAULT '',
          status TEXT NOT NULL DEFAULT 'Pending', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS collector_assignments(
          id INTEGER PRIMARY KEY AUTOINCREMENT, request_id INTEGER NOT NULL REFERENCES pickup_requests(id),
          collector_id INTEGER NOT NULL REFERENCES users(id), assigned_by INTEGER NOT NULL REFERENCES users(id),
          assigned_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS collection_records(
          id INTEGER PRIMARY KEY AUTOINCREMENT, request_id INTEGER UNIQUE NOT NULL REFERENCES pickup_requests(id),
          collector_id INTEGER NOT NULL REFERENCES users(id), actual_kg REAL NOT NULL CHECK(actual_kg > 0),
          actual_category TEXT NOT NULL, collected_at TEXT NOT NULL, remarks TEXT DEFAULT '',
          proof_filename TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS status_history(
          id INTEGER PRIMARY KEY AUTOINCREMENT, request_id INTEGER NOT NULL REFERENCES pickup_requests(id),
          old_status TEXT, new_status TEXT NOT NULL, changed_by INTEGER NOT NULL REFERENCES users(id),
          changed_at TEXT NOT NULL, note TEXT DEFAULT ''
        );
        """)
        # First admin is configurable through environment variables.
        email = os.environ.get("ADMIN_EMAIL", "admin@recircle.local").lower()
        if not c.execute("SELECT id FROM users WHERE role='admin' LIMIT 1").fetchone():
            c.execute("INSERT OR IGNORE INTO users(name,email,password_hash,role,created_at) VALUES(?,?,?,?,?)",
                      (os.environ.get("ADMIN_NAME","ReCircle Admin"), email,
                       generate_password_hash(os.environ.get("ADMIN_PASSWORD","Admin@123")), "admin", now()))
        c.commit()

def seed_demo():
    with db() as c:
        if c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"] > 1:
            return
        demo = [
          ("Ananya Rao","ananya@example.com","9876543210","household","House@123"),
          ("Rahul Shetty","rahul@example.com","9876543211","household","House@123"),
          ("Kiran Kumar","kiran@example.com","9876543212","collector","Collect@123"),
          ("Meera Das","meera@example.com","9876543213","collector","Collect@123")
        ]
        ids = {}
        for name,email,phone,role,pw in demo:
            c.execute("INSERT INTO users(name,email,phone,password_hash,role,created_at) VALUES(?,?,?,?,?,?)",
                      (name,email,phone,generate_password_hash(pw),role,now()))
            ids[email] = c.execute("SELECT id FROM users WHERE email=?",(email,)).fetchone()["id"]
            if role=="household":
                c.execute("INSERT INTO household_profiles(user_id,address) VALUES(?,?)",(ids[email], "12 Green Park, Mysuru"))
            else:
                c.execute("INSERT INTO collector_profiles(user_id,availability) VALUES(?,?)",(ids[email],"Available"))
        households=[ids["ananya@example.com"],ids["rahul@example.com"]]
        for i,(hid,cat,kg,st,collector) in enumerate([
          (households[0],"Plastic",4.5,"Pending",None),
          (households[1],"Paper",8.0,"Assigned",ids["kiran@example.com"]),
          (households[0],"E-waste",2.0,"In Progress",ids["meera@example.com"]),
          (households[1],"Glass",3.2,"Completed",ids["kiran@example.com"])
        ]):
            code=f"RC-{datetime.now().strftime('%y%m')}-{1001+i}"
            c.execute("""INSERT INTO pickup_requests(request_code,household_id,category,estimated_kg,address,preferred_date,time_slot,instructions,status,created_at,updated_at)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?)""",(code,hid,cat,kg,"12 Green Park, Mysuru","2026-11-20",SLOTS[i%3],"Please call on arrival",st,now(),now()))
            rid=c.execute("SELECT last_insert_rowid()").fetchone()[0]
            if collector:
                c.execute("INSERT INTO collector_assignments(request_id,collector_id,assigned_by,assigned_at,active) VALUES(?,?,?,?,1)",
                          (rid,collector,1,now()))
            c.execute("INSERT INTO status_history(request_id,old_status,new_status,changed_by,changed_at,note) VALUES(?,?,?,?,?,?)",
                      (rid,None,"Pending",hid,now(),"Demo request created"))
            if st!="Pending":
                c.execute("INSERT INTO status_history(request_id,old_status,new_status,changed_by,changed_at,note) VALUES(?,?,?,?,?,?)",
                          (rid,"Pending",st,1,now(),"Demo status"))
        c.commit()

def current_user():
    if not session.get("uid"): return None
    with db() as c:
        return c.execute("SELECT * FROM users WHERE id=? AND active=1",(session["uid"],)).fetchone()

def login_required(fn):
    @wraps(fn)
    def wrapper(*a,**kw):
        if not current_user():
            flash("Please log in to continue.","warning"); return redirect(url_for("login"))
        return fn(*a,**kw)
    return wrapper

def roles(*allowed):
    def deco(fn):
        @wraps(fn)
        @login_required
        def wrapper(*a,**kw):
            u=current_user()
            if u["role"] not in allowed: abort(403)
            return fn(*a,**kw)
        return wrapper
    return deco

def set_status(c, rid, new, actor, note=""):
    row=c.execute("SELECT status FROM pickup_requests WHERE id=?",(rid,)).fetchone()
    if not row: abort(404)
    old=row["status"]
    if old==new: return
    c.execute("UPDATE pickup_requests SET status=?,updated_at=? WHERE id=?",(new,now(),rid))
    c.execute("INSERT INTO status_history(request_id,old_status,new_status,changed_by,changed_at,note) VALUES(?,?,?,?,?,?)",
              (rid,old,new,actor,now(),note))

def get_request(c, rid):
    return c.execute("""SELECT p.*, u.name household_name,u.phone household_phone,
        (SELECT u2.name FROM collector_assignments a JOIN users u2 ON u2.id=a.collector_id
         WHERE a.request_id=p.id AND a.active=1 ORDER BY a.id DESC LIMIT 1) collector_name,
        (SELECT u2.id FROM collector_assignments a JOIN users u2 ON u2.id=a.collector_id
         WHERE a.request_id=p.id AND a.active=1 ORDER BY a.id DESC LIMIT 1) collector_id
        FROM pickup_requests p JOIN users u ON u.id=p.household_id WHERE p.id=?""",(rid,)).fetchone()

@app.context_processor
def inject():
    return {"me":current_user(),"categories":CATEGORIES,"statuses":STATUSES,"today":date.today().isoformat()}

@app.route("/")
def home():
    with db() as c:
        stats=c.execute("SELECT COUNT(*) requests, COALESCE(SUM(actual_kg),0) kg FROM collection_records").fetchone()
    return render_template("home.html", impact=stats)

@app.route("/register", methods=["GET","POST"])
def register():
    if request.method=="POST":
        name=request.form.get("name","").strip(); email=request.form.get("email","").strip().lower()
        phone=request.form.get("phone","").strip(); password=request.form.get("password","")
        address=request.form.get("address","").strip()
        if not name or not email or len(password)<8 or not address:
            flash("Complete all required fields. Password must be at least 8 characters.","danger")
        else:
            try:
                with db() as c:
                    c.execute("INSERT INTO users(name,email,phone,password_hash,role,created_at) VALUES(?,?,?,?,?,?)",
                              (name,email,phone,generate_password_hash(password),"household",now()))
                    uid=c.execute("SELECT last_insert_rowid()").fetchone()[0]
                    c.execute("INSERT INTO household_profiles(user_id,address) VALUES(?,?)",(uid,address)); c.commit()
                session["uid"]=uid; flash("Your household account is ready!","success"); return redirect(url_for("household"))
            except sqlite3.IntegrityError: flash("An account with that email already exists.","danger")
    return render_template("auth.html", mode="register")

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method=="POST":
        email=request.form.get("email","").strip().lower(); pw=request.form.get("password","")
        with db() as c: u=c.execute("SELECT * FROM users WHERE email=?",(email,)).fetchone()
        if u and u["active"] and check_password_hash(u["password_hash"],pw):
            session.clear(); session["uid"]=u["id"]
            flash("Welcome back!","success")
            return redirect(url_for({"household":"household","collector":"collector","admin":"admin"}[u["role"]]))
        flash("Invalid email or password, or account is inactive.","danger")
    return render_template("auth.html", mode="login")

@app.route("/logout")
def logout():
    session.clear(); flash("You have been logged out.","success"); return redirect(url_for("home"))

@app.route("/household")
@roles("household")
def household():
    u=current_user()
    with db() as c:
        rows=c.execute("SELECT * FROM pickup_requests WHERE household_id=? ORDER BY created_at DESC",(u["id"],)).fetchall()
        counts=c.execute("""SELECT COUNT(*) total, SUM(status IN ('Pending','Assigned','Accepted','In Progress')) pending,
            SUM(status='Completed') completed FROM pickup_requests WHERE household_id=?""",(u["id"],)).fetchone()
        kg=c.execute("SELECT COALESCE(SUM(cr.actual_kg),0) kg FROM collection_records cr JOIN pickup_requests p ON p.id=cr.request_id WHERE p.household_id=?",(u["id"],)).fetchone()["kg"]
    return render_template("household.html",rows=rows,counts=counts,kg=kg)

@app.route("/request/new", methods=["GET","POST"])
@roles("household")
def new_request():
    if request.method=="POST":
        cat=request.form.get("category"); qty=request.form.get("quantity",""); address=request.form.get("address","").strip()
        dt=request.form.get("date",""); slot=request.form.get("slot"); instructions=request.form.get("instructions","").strip()
        try: kg=float(qty)
        except: kg=0
        if cat not in CATEGORIES or kg<=0 or kg>10000 or not address or not dt or dt<date.today().isoformat() or slot not in SLOTS:
            flash("Check category, quantity, address, future pickup date and time slot.","danger")
        else:
            with db() as c:
                code=f"RC-{datetime.now().strftime('%y%m')}-{uuid.uuid4().hex[:6].upper()}"
                c.execute("""INSERT INTO pickup_requests(request_code,household_id,category,estimated_kg,address,preferred_date,time_slot,instructions,status,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,'Pending',?,?)""",(code,current_user()["id"],cat,kg,address,dt,slot,instructions,now(),now()))
                rid=c.execute("SELECT last_insert_rowid()").fetchone()[0]
                c.execute("INSERT INTO status_history(request_id,old_status,new_status,changed_by,changed_at,note) VALUES(?,NULL,'Pending',?,?,?)",
                          (rid,current_user()["id"],now(),"Household submitted request")); c.commit()
            flash(f"Pickup request {code} created.","success"); return redirect(url_for("household"))
    with db() as c:
        prof=c.execute("SELECT address FROM household_profiles WHERE user_id=?",(current_user()["id"],)).fetchone()
    return render_template("request_form.html",address=prof["address"] if prof else "")

@app.route("/request/<int:rid>")
@login_required
def request_detail(rid):
    u=current_user()
    with db() as c:
        r=get_request(c,rid)
        if not r: abort(404)
        if u["role"]=="household" and r["household_id"]!=u["id"]: abort(403)
        if u["role"]=="collector" and r["collector_id"]!=u["id"]: abort(403)
        record=c.execute("SELECT cr.*,u.name collector_name FROM collection_records cr JOIN users u ON u.id=cr.collector_id WHERE cr.request_id=?",(rid,)).fetchone()
        history=c.execute("SELECT h.*,u.name actor_name FROM status_history h JOIN users u ON u.id=h.changed_by WHERE request_id=? ORDER BY h.id",(rid,)).fetchall()
        collectors=c.execute("SELECT u.id,u.name,cp.availability,COUNT(a.id) workload FROM users u JOIN collector_profiles cp ON cp.user_id=u.id LEFT JOIN collector_assignments a ON a.collector_id=u.id AND a.active=1 LEFT JOIN pickup_requests p ON p.id=a.request_id AND p.status NOT IN ('Completed','Cancelled') WHERE u.role='collector' AND u.active=1 GROUP BY u.id ORDER BY u.name").fetchall() if u["role"]=="admin" else []
    return render_template("detail.html",r=r,record=record,history=history,collectors=collectors)

@app.route("/request/<int:rid>/cancel",methods=["POST"])
@roles("household")
def cancel_request(rid):
    with db() as c:
        r=c.execute("SELECT * FROM pickup_requests WHERE id=? AND household_id=?",(rid,current_user()["id"])).fetchone()
        if not r: abort(404)
        if r["status"]!="Pending": flash("Only pending, unassigned requests can be cancelled.","danger")
        else: set_status(c,rid,"Cancelled",current_user()["id"],"Cancelled by household"); flash("Request cancelled.","success")
        c.commit()
    return redirect(url_for("request_detail",rid=rid))

@app.route("/collector")
@roles("collector")
def collector():
    u=current_user()
    with db() as c:
        rows=c.execute("""SELECT p.*,u.name household_name FROM pickup_requests p JOIN users u ON u.id=p.household_id
          JOIN collector_assignments a ON a.request_id=p.id WHERE a.collector_id=? AND a.active=1 ORDER BY p.preferred_date""",(u["id"],)).fetchall()
        counts=c.execute("""SELECT COUNT(*) assigned,SUM(status IN ('Assigned','Accepted','In Progress')) pending,SUM(status='Completed') completed FROM pickup_requests p JOIN collector_assignments a ON a.request_id=p.id WHERE a.collector_id=? AND a.active=1""",(u["id"],)).fetchone()
        kg=c.execute("SELECT COALESCE(SUM(cr.actual_kg),0) kg FROM collection_records cr WHERE cr.collector_id=?",(u["id"],)).fetchone()["kg"]
        profile=c.execute("SELECT availability FROM collector_profiles WHERE user_id=?",(u["id"],)).fetchone()
    return render_template("collector.html",rows=rows,counts=counts,kg=kg,availability=profile["availability"] if profile else "Available")

@app.route("/collector/availability",methods=["POST"])
@roles("collector")
def availability():
    value=request.form.get("availability")
    if value not in ["Available","Unavailable"]: abort(400)
    with db() as c: c.execute("UPDATE collector_profiles SET availability=? WHERE user_id=?",(value,current_user()["id"])); c.commit()
    flash("Availability updated.","success"); return redirect(url_for("collector"))

@app.route("/request/<int:rid>/action",methods=["POST"])
@roles("collector")
def collector_action(rid):
    action=request.form.get("action")
    transitions={"accept":("Assigned","Accepted"),"start":("Accepted","In Progress")}
    if action not in transitions: abort(400)
    old,new=transitions[action]
    with db() as c:
        r=get_request(c,rid)
        if not r or r["collector_id"]!=current_user()["id"]: abort(403)
        if r["status"]!=old: flash("This request has already changed or cannot take that action.","danger")
        else: set_status(c,rid,new,current_user()["id"],f"Collector {action}ed request"); flash(f"Request marked {new.lower()}.","success")
        c.commit()
    return redirect(url_for("request_detail",rid=rid))

@app.route("/request/<int:rid>/complete",methods=["POST"])
@roles("collector")
def complete_request(rid):
    r_id=rid; f=request.files.get("proof"); qty=request.form.get("actual_kg","")
    try: kg=float(qty)
    except: kg=0
    cat=request.form.get("actual_category"); collected=request.form.get("collected_at") or now()
    remarks=request.form.get("remarks","").strip()
    if kg<=0 or kg>10000 or cat not in CATEGORIES or not request.form.get("confirm") or not f or not f.filename:
        flash("Enter a valid quantity and category, attach proof, and confirm the collection.","danger")
        return redirect(url_for("request_detail",rid=rid))
    ext=f.filename.rsplit(".",1)[-1].lower() if "." in f.filename else ""
    if ext not in ALLOWED:
        flash("Proof photo must be PNG, JPG, JPEG or WEBP.","danger"); return redirect(url_for("request_detail",rid=rid))
    # Check the file signature as well as the extension; never trust the browser MIME type.
    head = f.stream.read(16)
    f.stream.seek(0)
    valid = (ext in ("jpg", "jpeg") and head.startswith(b"\xff\xd8\xff")) or (ext == "png" and head.startswith(b"\x89PNG\r\n\x1a\n")) or (ext == "webp" and head.startswith(b"RIFF") and head[8:12] == b"WEBP")
    if not valid:
        flash("The uploaded file does not appear to be a valid supported image.","danger"); return redirect(url_for("request_detail",rid=rid))
    with db() as c:
        r=get_request(c,rid)
        if not r or r["collector_id"]!=current_user()["id"]: abort(403)
        if r["status"]!="In Progress" or c.execute("SELECT id FROM collection_records WHERE request_id=?",(rid,)).fetchone():
            flash("This pickup is not ready for completion.","danger"); return redirect(url_for("request_detail",rid=rid))
        filename=f"{uuid.uuid4().hex}.{ext}"; f.save(UPLOAD_DIR/filename)
        c.execute("""INSERT INTO collection_records(request_id,collector_id,actual_kg,actual_category,collected_at,remarks,proof_filename,created_at)
                     VALUES(?,?,?,?,?,?,?,?)""",(rid,current_user()["id"],kg,cat,collected,remarks,filename,now()))
        set_status(c,rid,"Completed",current_user()["id"],"Collection completed with proof photo")
        c.commit()
    flash("Pickup completed and proof saved.","success"); return redirect(url_for("request_detail",rid=rid))

@app.route("/admin")
@roles("admin")
def admin():
    with db() as c:
        stats=c.execute("""SELECT COUNT(*) total,SUM(status='Pending') pending,SUM(status='Assigned') assigned,
          SUM(status='In Progress') progress,SUM(status='Completed') completed FROM pickup_requests""").fetchone()
        kg=c.execute("SELECT COALESCE(SUM(actual_kg),0) kg FROM collection_records").fetchone()["kg"]
        rows=c.execute("""SELECT p.*,u.name household_name,
          (SELECT u2.name FROM collector_assignments a JOIN users u2 ON u2.id=a.collector_id WHERE a.request_id=p.id AND a.active=1 ORDER BY a.id DESC LIMIT 1) collector_name
          FROM pickup_requests p JOIN users u ON u.id=p.household_id ORDER BY p.created_at DESC""").fetchall()
        cats=c.execute("SELECT actual_category category,SUM(actual_kg) kg FROM collection_records GROUP BY actual_category").fetchall()
        months=c.execute("SELECT substr(collected_at,1,7) month,SUM(actual_kg) kg FROM collection_records GROUP BY month ORDER BY month DESC LIMIT 6").fetchall()
        collectors=c.execute("""SELECT u.id,u.name,u.email,u.phone,u.active,cp.availability,
          SUM(CASE WHEN p.status NOT IN ('Completed','Cancelled') THEN 1 ELSE 0 END) workload,
          SUM(CASE WHEN p.status='Completed' THEN 1 ELSE 0 END) completed
          FROM users u JOIN collector_profiles cp ON cp.user_id=u.id LEFT JOIN collector_assignments a ON a.collector_id=u.id AND a.active=1
          LEFT JOIN pickup_requests p ON p.id=a.request_id WHERE u.role='collector' GROUP BY u.id ORDER BY u.name""").fetchall()
    return render_template("admin.html",stats=stats,kg=kg,rows=rows,cats=cats,months=list(reversed(months)),collectors=collectors)

@app.route("/admin/assign/<int:rid>",methods=["POST"])
@roles("admin")
def assign(rid):
    cid=request.form.get("collector_id")
    with db() as c:
        r=c.execute("SELECT * FROM pickup_requests WHERE id=?",(rid,)).fetchone()
        collector=c.execute("SELECT * FROM users WHERE id=? AND role='collector' AND active=1",(cid,)).fetchone()
        if not r or not collector: abort(400)
        if r["status"] in ["Completed","Cancelled"]: flash("Completed or cancelled requests cannot be assigned.","danger")
        else:
            c.execute("UPDATE collector_assignments SET active=0 WHERE request_id=? AND active=1",(rid,))
            c.execute("INSERT INTO collector_assignments(request_id,collector_id,assigned_by,assigned_at,active) VALUES(?,?,?,?,1)",(rid,cid,current_user()["id"],now()))
            set_status(c,rid,"Assigned",current_user()["id"],"Assigned/reassigned by NGO admin")
            c.commit(); flash("Collector assigned successfully.","success")
    return redirect(url_for("request_detail",rid=rid))

@app.route("/admin/collectors",methods=["POST"])
@roles("admin")
def add_collector():
    name=request.form.get("name","").strip(); email=request.form.get("email","").strip().lower()
    phone=request.form.get("phone","").strip(); pw=request.form.get("password","")
    if not name or not email or len(pw)<8:
        flash("Name, email and password (minimum 8 characters) are required.","danger")
    else:
        try:
            with db() as c:
                c.execute("INSERT INTO users(name,email,phone,password_hash,role,created_at) VALUES(?,?,?,?,?,?)",
                          (name,email,phone,generate_password_hash(pw),"collector",now()))
                uid=c.execute("SELECT last_insert_rowid()").fetchone()[0]
                c.execute("INSERT INTO collector_profiles(user_id) VALUES(?)",(uid,)); c.commit()
            flash("Collector account created.","success")
        except sqlite3.IntegrityError: flash("Email already exists.","danger")
    return redirect(url_for("admin"))

@app.route("/admin/collector/<int:uid>/toggle",methods=["POST"])
@roles("admin")
def toggle_collector(uid):
    with db() as c:
        u=c.execute("SELECT * FROM users WHERE id=? AND role='collector'",(uid,)).fetchone()
        if not u: abort(404)
        c.execute("UPDATE users SET active=? WHERE id=?", (0 if u["active"] else 1,uid)); c.commit()
    flash("Collector account status updated.","success"); return redirect(url_for("admin"))

@app.route("/admin/export.csv")
@roles("admin")
def export_csv():
    with db() as c:
        rows=c.execute("""SELECT p.request_code,u.name household,p.category,p.estimated_kg,p.address,p.preferred_date,p.time_slot,p.status,
          cr.actual_kg,cr.actual_category,cr.collected_at,cr.remarks
          FROM pickup_requests p JOIN users u ON u.id=p.household_id LEFT JOIN collection_records cr ON cr.request_id=p.id ORDER BY p.created_at DESC""").fetchall()
    out=io.StringIO(); w=csv.writer(out); w.writerow(rows[0].keys() if rows else ["No records"])
    for r in rows: w.writerow(list(r))
    resp=make_response(out.getvalue()); resp.headers["Content-Disposition"]="attachment; filename=recircle-requests.csv"; resp.headers["Content-Type"]="text/csv"
    return resp

@app.route("/uploads/<filename>")
@login_required
def uploaded_file(filename):
    with db() as c:
        rec=c.execute("""SELECT cr.request_id,p.household_id,cr.collector_id FROM collection_records cr JOIN pickup_requests p ON p.id=cr.request_id WHERE cr.proof_filename=?""",(filename,)).fetchone()
    u=current_user()
    if not rec or (u["role"]=="household" and rec["household_id"]!=u["id"]) or (u["role"]=="collector" and rec["collector_id"]!=u["id"]) or u["role"] not in ("admin","household","collector"): abort(403)
    return send_from_directory(UPLOAD_DIR,filename)

@app.errorhandler(403)
def forbidden(e): return render_template("error.html",code=403,message="You don't have permission to view this page."),403
@app.errorhandler(404)
def missing(e): return render_template("error.html",code=404,message="The page or record could not be found."),404
@app.errorhandler(413)
def too_large(e): return "File too large. Maximum upload size is 5 MB.",413

with app.app_context():
    init_db()
    if os.environ.get("SEED_DEMO","true").lower()=="true": seed_demo()

if __name__=="__main__":
    app.run(debug=True)
