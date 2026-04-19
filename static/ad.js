/**
 * USHSS Backend Server
 * Stack: Node.js + Express + better-sqlite3 + bcryptjs + jsonwebtoken
 * Run:   node server.js
 * Port:  3001 (proxied from frontend /api/*)
 */

const express    = require("express");
const Database   = require("better-sqlite3");
const bcrypt     = require("bcryptjs");
const jwt        = require("jsonwebtoken");
const cors       = require("cors");
const path       = require("path");

const app  = express();
const PORT = process.env.PORT || 3001;
const JWT_SECRET = process.env.JWT_SECRET || "ushss_super_secret_2025_change_me";

/* ── MIDDLEWARE ── */
app.use(cors({ origin: "*", credentials: true }));
app.use(express.json());
app.use(express.static(path.join(__dirname, "public")));

/* ── DATABASE SETUP ── */
const db = new Database(path.join(__dirname, "ushss.db"));
db.pragma("journal_mode = WAL");
db.pragma("foreign_keys = ON");

db.exec(`
  CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    role          TEXT    NOT NULL CHECK(role IN ('student','faculty','cr','admin')),
    username      TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,
    full_name     TEXT    NOT NULL,
    email         TEXT,
    phone         TEXT,
    department    TEXT,
    programme     TEXT,
    batch         TEXT,
    designation   TEXT,
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    last_login    TEXT
  );

  CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER REFERENCES users(id),
    action     TEXT    NOT NULL,
    detail     TEXT,
    ip         TEXT,
    ts         TEXT    NOT NULL DEFAULT (datetime('now'))
  );
`);

/* ── SEED DEFAULT ADMIN (only if table is empty) ── */
const userCount = db.prepare("SELECT COUNT(*) AS n FROM users").get().n;
if (userCount === 0) {
  const adminHash = bcrypt.hashSync("Admin@1234", 10);
  db.prepare(`
    INSERT INTO users (role, username, password_hash, full_name, email, department)
    VALUES ('admin','admin001', ?, 'USHSS Administrator', 'admin.ushss@ipu.ac.in', 'Administration')
  `).run(adminHash);

  /* Sample students */
  const students = [
    ["2301001","John Sharma","john.sharma@ipu.ac.in","M.A. English","2023"],
    ["2301002","Priya Verma","priya.verma@ipu.ac.in","M.A. Psychology","2023"],
    ["2401001","Rahul Gupta","rahul.gupta@ipu.ac.in","M.A. Economics","2024"],
  ];
  const stmtS = db.prepare(`
    INSERT INTO users (role, username, password_hash, full_name, email, department, programme, batch)
    VALUES ('student',?,?,?,?,?,?,?)
  `);
  for (const [uname, name, email, prog, batch] of students) {
    stmtS.run(uname, bcrypt.hashSync("Student@123", 10), name, email, "Humanities", prog, batch);
  }

  /* Sample faculty */
  const faculty = [
    ["fac001","Dr. Anup Singh Beniwal","anupbeniwal@ipu.ac.in","Professor"],
    ["fac002","Dr. Shuchi Sharma","shuchi.sharma@ipu.ac.in","Dean / Professor"],
    ["fac003","Dr. Chetna Tiwari","chetna.ushss@ipu.ac.in","Associate Professor"],
  ];
  const stmtF = db.prepare(`
    INSERT INTO users (role, username, password_hash, full_name, email, department, designation)
    VALUES ('faculty',?,?,?,?,?,?)
  `);
  for (const [uname, name, email, desig] of faculty) {
    stmtF.run(uname, bcrypt.hashSync("Faculty@123", 10), name, email, "USHSS", desig);
  }

  /* Sample CR */
  db.prepare(`
    INSERT INTO users (role, username, password_hash, full_name, email, department, programme, batch, designation)
    VALUES ('cr','cr2301001',?,?,?,?,?,?,?)
  `).run(bcrypt.hashSync("Cr@12345", 10), "Amit CR Leader", "cr.english@ipu.ac.in",
         "Humanities", "M.A. English", "2023", "Class Representative");

  console.log("✅ Database seeded with default credentials.");
}

/* ── AUTH MIDDLEWARE ── */
function requireAuth(roles = []) {
  return (req, res, next) => {
    const token = req.headers.authorization?.split(" ")[1];
    if (!token) return res.status(401).json({ error: "No token provided" });
    try {
      const payload = jwt.verify(token, JWT_SECRET);
      if (roles.length && !roles.includes(payload.role))
        return res.status(403).json({ error: "Insufficient permissions" });
      req.user = payload;
      next();
    } catch {
      return res.status(401).json({ error: "Invalid or expired token" });
    }
  };
}

/* ── ROUTES ── */

/* POST /api/login */
app.post("/api/login", (req, res) => {
  const { username, password, role } = req.body;
  if (!username || !password || !role)
    return res.status(400).json({ success: false, error: "Missing fields" });

  const user = db.prepare("SELECT * FROM users WHERE username=? AND role=? AND is_active=1")
                  .get(username.trim(), role.trim());

  if (!user || !bcrypt.compareSync(password, user.password_hash))
    return res.status(401).json({ success: false, error: "Invalid credentials" });

  db.prepare("UPDATE users SET last_login=datetime('now') WHERE id=?").run(user.id);
  db.prepare("INSERT INTO audit_log(user_id,action,ip) VALUES(?,?,?)")
    .run(user.id, "LOGIN", req.ip);

  const token = jwt.sign(
    { id: user.id, role: user.role, name: user.full_name, username: user.username },
    JWT_SECRET,
    { expiresIn: "8h" }
  );

  const redirectMap = { admin: "/admin", faculty: "/faculty", cr: "/cr", student: "/student" };
  res.json({ success: true, token, redirect_url: redirectMap[role], user: {
    id: user.id, name: user.full_name, role: user.role, email: user.email
  }});
});

/* GET /api/me */
app.get("/api/me", requireAuth(), (req, res) => {
  const user = db.prepare("SELECT id,role,username,full_name,email,department,designation,programme,batch,last_login FROM users WHERE id=?").get(req.user.id);
  res.json(user);
});

/* ── ADMIN ROUTES ── */

/* GET /api/admin/users */
app.get("/api/admin/users", requireAuth(["admin"]), (req, res) => {
  const { role, search, page = 1, limit = 20 } = req.query;
  let where = "WHERE 1=1";
  const params = [];
  if (role) { where += " AND role=?"; params.push(role); }
  if (search) { where += " AND (full_name LIKE ? OR username LIKE ? OR email LIKE ?)"; params.push(`%${search}%`,`%${search}%`,`%${search}%`); }
  const offset = (page - 1) * limit;
  const users = db.prepare(`SELECT id,role,username,full_name,email,phone,department,programme,batch,designation,is_active,created_at,last_login FROM users ${where} ORDER BY created_at DESC LIMIT ? OFFSET ?`).all(...params, limit, offset);
  const total = db.prepare(`SELECT COUNT(*) AS n FROM users ${where}`).get(...params).n;
  res.json({ users, total, page: +page, pages: Math.ceil(total / limit) });
});

/* POST /api/admin/users — create user */
app.post("/api/admin/users", requireAuth(["admin"]), (req, res) => {
  const { role, username, password, full_name, email, phone, department, programme, batch, designation } = req.body;
  if (!role || !username || !password || !full_name)
    return res.status(400).json({ error: "role, username, password, full_name required" });
  const hash = bcrypt.hashSync(password, 10);
  try {
    const r = db.prepare(`INSERT INTO users (role,username,password_hash,full_name,email,phone,department,programme,batch,designation) VALUES (?,?,?,?,?,?,?,?,?,?)`).run(role, username, hash, full_name, email, phone, department, programme, batch, designation);
    db.prepare("INSERT INTO audit_log(user_id,action,detail) VALUES(?,?,?)").run(req.user.id, "CREATE_USER", `Created ${role} ${username}`);
    res.status(201).json({ id: r.lastInsertRowid, message: "User created" });
  } catch (e) {
    res.status(409).json({ error: "Username already exists" });
  }
});

/* PUT /api/admin/users/:id — update user */
app.put("/api/admin/users/:id", requireAuth(["admin"]), (req, res) => {
  const { full_name, email, phone, department, programme, batch, designation, is_active, password } = req.body;
  const user = db.prepare("SELECT * FROM users WHERE id=?").get(req.params.id);
  if (!user) return res.status(404).json({ error: "User not found" });
  let hash = user.password_hash;
  if (password) hash = bcrypt.hashSync(password, 10);
  db.prepare(`UPDATE users SET full_name=?,email=?,phone=?,department=?,programme=?,batch=?,designation=?,is_active=?,password_hash=? WHERE id=?`)
    .run(full_name ?? user.full_name, email ?? user.email, phone ?? user.phone, department ?? user.department, programme ?? user.programme, batch ?? user.batch, designation ?? user.designation, is_active ?? user.is_active, hash, req.params.id);
  db.prepare("INSERT INTO audit_log(user_id,action,detail) VALUES(?,?,?)").run(req.user.id, "UPDATE_USER", `Updated user id=${req.params.id}`);
  res.json({ message: "Updated" });
});

/* DELETE /api/admin/users/:id */
app.delete("/api/admin/users/:id", requireAuth(["admin"]), (req, res) => {
  if (req.params.id == req.user.id)
    return res.status(400).json({ error: "Cannot delete yourself" });
  const info = db.prepare("DELETE FROM users WHERE id=?").run(req.params.id);
  if (!info.changes) return res.status(404).json({ error: "Not found" });
  db.prepare("INSERT INTO audit_log(user_id,action,detail) VALUES(?,?,?)").run(req.user.id, "DELETE_USER", `Deleted user id=${req.params.id}`);
  res.json({ message: "Deleted" });
});

/* GET /api/admin/stats */
app.get("/api/admin/stats", requireAuth(["admin"]), (req, res) => {
  const counts = db.prepare("SELECT role, COUNT(*) AS n FROM users GROUP BY role").all();
  const active = db.prepare("SELECT COUNT(*) AS n FROM users WHERE is_active=1").get().n;
  const recentLogins = db.prepare("SELECT id,full_name,role,last_login FROM users WHERE last_login IS NOT NULL ORDER BY last_login DESC LIMIT 10").all();
  const log = db.prepare("SELECT l.*,u.full_name FROM audit_log l LEFT JOIN users u ON u.id=l.user_id ORDER BY l.ts DESC LIMIT 20").all();
  res.json({ counts, active, recentLogins, log });
});

/* GET /api/admin/audit */
app.get("/api/admin/audit", requireAuth(["admin"]), (req, res) => {
  const rows = db.prepare("SELECT l.*,u.full_name,u.role FROM audit_log l LEFT JOIN users u ON u.id=l.user_id ORDER BY l.ts DESC LIMIT 100").all();
  res.json(rows);
});

/* ── START ── */
app.listen(PORT, () => {
  console.log(`\n🏛️  USHSS Backend running on http://localhost:${PORT}`);
  console.log(`\n📋 Default Credentials:`);
  console.log(`   Admin    → username: admin001   password: Admin@1234`);
  console.log(`   Student  → username: 2301001    password: Student@123`);
  console.log(`   Faculty  → username: fac001     password: Faculty@123`);
  console.log(`   CR       → username: cr2301001  password: Cr@12345\n`);
});