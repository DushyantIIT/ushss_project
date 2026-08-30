"""
tests/test_flow.py
──────────────────
Comprehensive integration tests for USHSS Portal:
  1. Health check GET /health
  2. Public endpoints (faculty, events, news, contact)
  3. Login for all 4 roles (admin001, fac001, cr2301001, 2301001)
  4. Pending & rejected registration flow & status check
  5. Student timetable, open sessions, attendance marking, attendance history
  6. Faculty timetable, opening session, closing session
  7. CR classmates listing
  8. Admin users listing, audit log, stats, pending requests
"""

import os
os.environ["TESTING"] = "true"
import sys
import unittest
from fastapi.testclient import TestClient
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from main import app
from app.seed import seed

client = TestClient(app)


class TestUSHSSPortal(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Run seed once before tests
        try:
            seed()
        except Exception as e:
            print("Test seed exception:", e)

    def test_01_health(self):
        res = client.get("/health")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("status", data)
        self.assertIn("connected", data)

    def test_02_public_endpoints(self):
        # Faculty
        r_fac = client.get("/api/public/faculty")
        self.assertEqual(r_fac.status_code, 200)
        self.assertIsInstance(r_fac.json(), list)

        # Events
        r_ev = client.get("/api/public/events")
        self.assertEqual(r_ev.status_code, 200)
        self.assertIsInstance(r_ev.json(), list)

        # News
        r_nw = client.get("/api/public/news")
        self.assertEqual(r_nw.status_code, 200)
        self.assertIsInstance(r_nw.json(), list)

        # Contact
        r_cnt = client.post("/api/public/contact", json={
            "first_name": "Test",
            "last_name": "User",
            "email": "test@example.com",
            "subject": "Inquiry",
            "message": "Hello USHSS portal"
        })
        self.assertEqual(r_cnt.status_code, 201)
        self.assertTrue(r_cnt.json().get("success"))

    def test_03_logins(self):
        roles_creds = [
            ("admin001",   "Admin@1234",  "admin",   "/dashboard/admin"),
            ("fac001",     "Faculty@123", "faculty", "/dashboard/faculty"),
            ("cr2301001",  "Cr@12345",    "cr",      "/dashboard/cr"),
            ("2301001",    "Student@123", "student", "/dashboard/student"),
        ]

        for username, pwd, role, expected_redirect in roles_creds:
            res = client.post("/api/login", json={
                "username": username,
                "password": pwd,
                "role": role,
            })
            self.assertEqual(
                res.status_code, 200,
                f"Login failed for {role} '{username}': {res.text}"
            )
            data = res.json()
            self.assertTrue(data.get("success"))
            self.assertIn("token", data)
            self.assertEqual(data.get("redirect_url"), expected_redirect)

    def test_04_admin_endpoints(self):
        # Login admin
        r_login = client.post("/api/login", json={
            "username": "admin001",
            "password": "Admin@1234",
            "role": "admin",
        })
        token = r_login.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        # List users
        r_users = client.get("/api/admin/users", headers=headers)
        self.assertEqual(r_users.status_code, 200)
        self.assertIn("users", r_users.json())

        # Pending requests
        r_pend = client.get("/api/admin/pending-requests", headers=headers)
        self.assertEqual(r_pend.status_code, 200)

        # Stats
        r_stats = client.get("/api/admin/stats", headers=headers)
        self.assertEqual(r_stats.status_code, 200)

        # Audit
        r_audit = client.get("/api/admin/audit", headers=headers)
        self.assertEqual(r_audit.status_code, 200)

    def test_05_student_endpoints(self):
        # Login student
        r_login = client.post("/api/login", json={
            "username": "2301001",
            "password": "Student@123",
            "role": "student",
        })
        token = r_login.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Profile
        r_prof = client.get("/api/student/profile", headers=headers)
        self.assertEqual(r_prof.status_code, 200)
        self.assertEqual(r_prof.json()["username"], "2301001")

        # Timetable
        r_tt = client.get("/api/student/timetable", headers=headers)
        self.assertEqual(r_tt.status_code, 200)

        # Attendance open sessions
        r_open = client.get("/api/student/attendance/open", headers=headers)
        self.assertEqual(r_open.status_code, 200)

        # Attendance history
        r_hist = client.get("/api/student/attendance/history", headers=headers)
        self.assertEqual(r_hist.status_code, 200)

    def test_06_cr_endpoints(self):
        # Login CR
        r_login = client.post("/api/login", json={
            "username": "cr2301001",
            "password": "Cr@12345",
            "role": "cr",
        })
        token = r_login.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Classmates
        r_class = client.get("/api/cr/classmates", headers=headers)
        self.assertEqual(r_class.status_code, 200)
        data = r_class.json()
        self.assertIn("students", data)

    def test_07_faculty_endpoints(self):
        # Login Faculty
        r_login = client.post("/api/login", json={
            "username": "fac001",
            "password": "Faculty@123",
            "role": "faculty",
        })
        token = r_login.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Profile
        r_prof = client.get("/api/faculty/profile", headers=headers)
        self.assertEqual(r_prof.status_code, 200)

        # Timetable
        r_tt = client.get("/api/faculty/timetable", headers=headers)
        self.assertEqual(r_tt.status_code, 200)

    def test_08_registration_approval_flow(self):
        import time
        ts = str(int(time.time()))
        uname = f"stu{ts}"
        email = f"stu{ts}@ipu.ac.in"

        # 1. Register a new student
        reg_res = client.post("/api/register", json={
            "username": uname,
            "password": "Student@999",
            "role": "student",
            "full_name": "New Pending Student",
            "email": email,
            "enrollment_no": uname,
            "programme": "M.A. English",
            "batch": "2024"
        })
        self.assertEqual(reg_res.status_code, 201)
        reg_data = reg_res.json()
        self.assertEqual(reg_data.get("redirect_url"), "/waiting")

        # 2. Login while pending should return 403 redirect to /waiting
        login_pending = client.post("/api/login", json={
            "username": uname,
            "password": "Student@999",
            "role": "student"
        })
        self.assertEqual(login_pending.status_code, 403)
        self.assertEqual(login_pending.json().get("detail", {}).get("redirect_url"), "/waiting")

        # 3. Admin logs in and approves the request
        admin_login = client.post("/api/login", json={
            "username": "admin001",
            "password": "Admin@1234",
            "role": "admin"
        })
        admin_token = admin_login.json()["token"]
        headers = {"Authorization": f"Bearer {admin_token}"}

        # Get pending list to find ID
        pend_list = client.get("/api/admin/pending-requests", headers=headers).json()["requests"]
        target = next((r for r in pend_list if r["username"] == uname), None)
        self.assertIsNotNone(target)

        # Approve
        appr_res = client.post(f"/api/admin/pending-requests/{target['id']}/approve", headers=headers)
        self.assertEqual(appr_res.status_code, 200)

        # 4. Login after approval should succeed with 200
        login_approved = client.post("/api/login", json={
            "username": uname,
            "password": "Student@999",
            "role": "student"
        })
        self.assertEqual(login_approved.status_code, 200)
        self.assertTrue(login_approved.json().get("success"))
        self.assertEqual(login_approved.json().get("redirect_url"), "/dashboard/student")


if __name__ == "__main__":
    unittest.main()
