"""Synthetic databases and evaluation cases for SQLiteX.

Each dataset includes deterministic data and questions whose correct answer is
computed with SQL at evaluation time, not hardcoded.
"""

from __future__ import annotations

import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _iso(day: date) -> str:
    return day.isoformat()


# ---------------------------------------------------------------------------
# HR
# ---------------------------------------------------------------------------

HR_SCHEMA = """
CREATE TABLE departments (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    cost_center TEXT NOT NULL,
    budget REAL NOT NULL
);
CREATE TABLE employees (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    department_id INTEGER NOT NULL REFERENCES departments(id),
    role TEXT NOT NULL,
    salary REAL NOT NULL,
    hired_at TEXT NOT NULL,
    manager_id INTEGER REFERENCES employees(id),
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE projects (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    department_id INTEGER NOT NULL REFERENCES departments(id),
    start_date TEXT NOT NULL,
    end_date TEXT,
    budget REAL NOT NULL,
    status TEXT NOT NULL
);
CREATE TABLE assignments (
    id INTEGER PRIMARY KEY,
    employee_id INTEGER NOT NULL REFERENCES employees(id),
    project_id INTEGER NOT NULL REFERENCES projects(id),
    hours REAL NOT NULL,
    worked_on TEXT NOT NULL
);
CREATE TABLE payroll (
    id INTEGER PRIMARY KEY,
    employee_id INTEGER NOT NULL REFERENCES employees(id),
    period TEXT NOT NULL,
    gross REAL NOT NULL,
    tax REAL NOT NULL,
    net REAL NOT NULL
);
CREATE TABLE absences (
    id INTEGER PRIMARY KEY,
    employee_id INTEGER NOT NULL REFERENCES employees(id),
    kind TEXT NOT NULL,
    days INTEGER NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL
);
"""


def build_hr(path: Path) -> None:
    rng = random.Random(11)
    conn = _connect(path)
    try:
        conn.executescript(HR_SCHEMA)
        departments = [
            ("Management", "CC-100", 900000),
            ("Technology", "CC-200", 1800000),
            ("Sales", "CC-300", 1200000),
            ("People", "CC-400", 600000),
            ("Finance", "CC-500", 750000),
        ]
        conn.executemany(
            "INSERT INTO departments (name, cost_center, budget) VALUES (?, ?, ?)",
            departments,
        )

        employees: list[tuple] = []

        def add(name, dept, role, salary, manager, active=1, hired="2021-01-15"):
            employees.append(
                (
                    len(employees) + 1,
                    name,
                    f"employee{len(employees) + 1}@example.com",
                    dept,
                    role,
                    salary,
                    hired,
                    manager,
                    active,
                )
            )

        add("Elena Ruiz", 1, "General Manager", 85000, None, hired="2015-01-12")
        add("Marcos Gil", 2, "CTO", 82000, 1, hired="2016-03-01")
        add("Sara Peña", 2, "Data Lead", 78000, 2, hired="2018-05-20")
        add("Iker Soler", 2, "Backend Engineer", 74000, 2, hired="2019-09-10")
        add("Nora Campos", 2, "Frontend Engineer", 70000, 2, hired="2020-02-17")
        add("Luis Ferrer", 2, "QA Engineer", 66000, 2, hired="2021-06-07")
        add("Marta Vidal", 3, "Sales Director", 55000, 1, hired="2017-11-02")
        add("Pablo Romero", 3, "Account Manager", 45000, 7, hired="2020-01-13")
        add("Ana Torres", 3, "Account Manager", 42000, 7, hired="2020-08-24")
        add("Javier León", 3, "Sales Development", 40000, 7, hired="2021-04-05")
        add("María Sanz", 3, "Sales Development", 38000, 7, hired="2022-01-10")
        add("Andrés Moya", 3, "Sales Development", 36000, 7, hired="2022-09-19")
        add("Laura Prieto", 3, "Sales Development", 34000, 7, hired="2023-02-27")
        add("Clara Nieto", 4, "People Director", 60000, 1, hired="2018-03-12")
        add("Paula Iglesias", 4, "Talent Partner", 44000, 14, hired="2020-07-01")
        add("Sergio Blanco", 4, "People Ops", 41000, 14, hired="2021-10-18")
        add("Nuria Ortiz", 4, "People Ops", 39000, 14, hired="2022-05-09")
        add("Diego Castro", 5, "CFO", 70000, 1, hired="2017-02-20")
        add("Carmen Luna", 5, "Controller", 50000, 18, hired="2019-06-03")
        add("Raúl Duarte", 5, "Accountant", 47000, 18, hired="2020-11-16")
        add("Alba Serrano", 5, "Accountant", 45000, 18, hired="2021-12-01")
        add("Iván Rey", 5, "Treasury", 43000, 18, hired="2023-04-11")
        add("Tomás Arroyo", 3, "Sales (former)", 150000, 7, active=0, hired="2016-06-06")
        add("Hugo Navas", 5, "External consultant", 200000, 18, active=0, hired="2015-09-30")
        add("Lucía Ferrer", 1, "Executive Assistant", 30000, 1, hired="2022-03-14")

        conn.executemany(
            "INSERT INTO employees (id, name, email, department_id, role, salary, hired_at, "
            "manager_id, active) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            employees,
        )

        projects = [
            (1, "ERP Migration", 2, "2026-01-15", "2026-07-31", 250000, "completed"),
            (2, "Customer Portal", 2, "2026-02-01", "2026-12-15", 180000, "in progress"),
            (3, "Q4 Campaign", 3, "2026-09-01", "2026-12-20", 90000, "in progress"),
            (4, "Onboarding 2.0", 4, "2026-03-10", "2026-08-30", 40000, "completed"),
            (5, "Fiscal Close 2026", 5, "2026-10-01", "2027-01-31", 60000, "in progress"),
            (6, "Data Lake", 2, "2025-11-01", "2026-09-30", 220000, "completed"),
            (7, "Mobile App", 2, "2026-04-01", "2027-02-28", 300000, "in progress"),
            (8, "Internal Audit", 5, "2026-06-01", "2026-08-15", 35000, "completed"),
        ]
        conn.executemany(
            "INSERT INTO projects (id, name, department_id, start_date, end_date, budget, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            projects,
        )

        assignments = []
        assignment_id = 1
        for project_id in range(1, 9):
            for _ in range(rng.randint(10, 16)):
                employee_id = rng.randint(1, 22)
                hours = rng.choice([2, 4, 6, 8, 10, 12, 16, 20, 24, 32, 40])
                month = rng.randint(1, 10)
                assignments.append(
                    (
                        assignment_id,
                        employee_id,
                        project_id,
                        hours,
                        f"2026-{month:02d}-{rng.randint(1, 28):02d}",
                    )
                )
                assignment_id += 1
        conn.executemany(
            "INSERT INTO assignments (id, employee_id, project_id, hours, worked_on) VALUES (?, ?, ?, ?, ?)",
            assignments,
        )

        payroll = []
        payroll_id = 1
        for employee in employees:
            if not employee[-1]:
                continue
            base = employee[5] / 12
            for period in ("2026-09", "2026-10"):
                gross = round(base * rng.uniform(0.98, 1.02), 2)
                tax = round(gross * 0.15, 2)
                payroll.append((payroll_id, employee[0], period, gross, tax, round(gross - tax, 2)))
                payroll_id += 1
        conn.executemany(
            "INSERT INTO payroll (id, employee_id, period, gross, tax, net) VALUES (?, ?, ?, ?, ?, ?)",
            payroll,
        )

        absences = []
        for index in range(1, 21):
            start = date(2026, 1, 1) + timedelta(days=rng.randint(0, 300))
            days = rng.randint(1, 10)
            absences.append(
                (
                    index,
                    rng.randint(1, 22),
                    rng.choice(["vacation", "sick leave", "personal leave"]),
                    days,
                    _iso(start),
                    _iso(start + timedelta(days=days - 1)),
                )
            )
        conn.executemany(
            "INSERT INTO absences (id, employee_id, kind, days, start_date, end_date) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            absences,
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# IoT
# ---------------------------------------------------------------------------

IOT_SCHEMA = """
CREATE TABLE sites (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    city TEXT NOT NULL,
    country TEXT NOT NULL,
    kind TEXT NOT NULL
);
CREATE TABLE devices (
    id INTEGER PRIMARY KEY,
    site_id INTEGER NOT NULL REFERENCES sites(id),
    code TEXT NOT NULL UNIQUE,
    model TEXT NOT NULL,
    status TEXT NOT NULL,
    installed_at TEXT NOT NULL
);
CREATE TABLE readings (
    id INTEGER PRIMARY KEY,
    device_id INTEGER NOT NULL REFERENCES devices(id),
    taken_at TEXT NOT NULL,
    temperature REAL NOT NULL,
    humidity REAL NOT NULL,
    battery REAL NOT NULL
);
CREATE TABLE alerts (
    id INTEGER PRIMARY KEY,
    device_id INTEGER NOT NULL REFERENCES devices(id),
    kind TEXT NOT NULL,
    severity TEXT NOT NULL,
    created_at TEXT NOT NULL,
    resolved INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE maintenance (
    id INTEGER PRIMARY KEY,
    device_id INTEGER NOT NULL REFERENCES devices(id),
    kind TEXT NOT NULL,
    scheduled_for TEXT NOT NULL,
    completed_at TEXT,
    cost REAL
);
CREATE INDEX idx_readings_device ON readings(device_id, taken_at);
"""


def build_iot(path: Path) -> None:
    rng = random.Random(23)
    conn = _connect(path)
    try:
        conn.executescript(IOT_SCHEMA)
        sites = [
            ("North Plant", "Madrid", "Spain", "factory"),
            ("South Plant", "Seville", "Spain", "factory"),
            ("Central Warehouse", "Valencia", "Spain", "warehouse"),
            ("Barcelona Office", "Barcelona", "Spain", "office"),
            ("Data Center", "Bilbao", "Spain", "data center"),
        ]
        conn.executemany("INSERT INTO sites (name, city, country, kind) VALUES (?, ?, ?, ?)", sites)

        devices = []
        for index in range(1, 13):
            if index <= 5:
                site_id = 1
            elif index <= 7:
                site_id = 2
            elif index <= 9:
                site_id = 3
            elif index <= 11:
                site_id = 4
            else:
                site_id = 5
            devices.append(
                (
                    index,
                    site_id,
                    f"DEV-{index:03d}",
                    rng.choice(["Sensor-A", "Sensor-B", "Sensor-Pro"]),
                    rng.choices(["active", "maintenance", "inactive"], [0.8, 0.15, 0.05])[0],
                    _iso(date(2024, 1, 1) + timedelta(days=rng.randint(0, 500))),
                )
            )
        conn.executemany(
            "INSERT INTO devices (id, site_id, code, model, status, installed_at) VALUES (?, ?, ?, ?, ?, ?)",
            devices,
        )

        readings = []
        reading_id = 1
        start = date(2026, 10, 1)
        for device in devices:
            base = 33.0 if device[2] == "DEV-003" else rng.uniform(17.5, 27.0)
            for _ in range(500):
                moment = start + timedelta(
                    days=rng.randint(0, 30), hours=rng.randint(0, 23), minutes=rng.randint(0, 59)
                )
                readings.append(
                    (
                        reading_id,
                        device[0],
                        f"{moment.isoformat()}T{rng.randint(0, 23):02d}:{rng.randint(0, 59):02d}:00",
                        round(base + rng.uniform(-1.0, 1.0), 2),
                        round(rng.uniform(25, 75), 2),
                        round(rng.uniform(5, 100), 2),
                    )
                )
                reading_id += 1
        conn.executemany(
            "INSERT INTO readings (id, device_id, taken_at, temperature, humidity, battery) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            readings,
        )

        alerts = []
        for index in range(1, 41):
            if index <= 8:
                severity, resolved = "critical", 0 if index <= 5 else 1
            else:
                severity = rng.choice(["high", "medium", "low"])
                resolved = int(rng.random() < 0.6)
            alerts.append(
                (
                    index,
                    rng.randint(1, 12),
                    rng.choice(["high temperature", "low battery", "connection lost", "sensor failure"]),
                    severity,
                    f"2026-{rng.randint(1, 10):02d}-{rng.randint(1, 28):02d}T"
                    f"{rng.randint(0, 23):02d}:{rng.randint(0, 59):02d}:00",
                    resolved,
                )
            )
        conn.executemany(
            "INSERT INTO alerts (id, device_id, kind, severity, created_at, resolved) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            alerts,
        )

        maintenance = []
        for index in range(1, 21):
            scheduled = date(2026, 1, 1) + timedelta(days=rng.randint(0, 500))
            completed = _iso(scheduled + timedelta(days=rng.randint(-3, 3))) if index <= 12 else None
            maintenance.append(
                (
                    index,
                    rng.randint(1, 12),
                    rng.choice(["preventive", "corrective", "calibration"]),
                    _iso(scheduled),
                    completed,
                    round(rng.uniform(150, 2000), 2) if completed else None,
                )
            )
        conn.executemany(
            "INSERT INTO maintenance (id, device_id, kind, scheduled_for, completed_at, cost) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            maintenance,
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Education
# ---------------------------------------------------------------------------

EDU_SCHEMA = """
CREATE TABLE teachers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    specialty TEXT NOT NULL
);
CREATE TABLE courses (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    teacher_id INTEGER NOT NULL REFERENCES teachers(id),
    level TEXT NOT NULL,
    hours INTEGER NOT NULL,
    price REAL NOT NULL
);
CREATE TABLE students (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    country TEXT NOT NULL,
    enrolled_at TEXT NOT NULL
);
CREATE TABLE enrollments (
    id INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES students(id),
    course_id INTEGER NOT NULL REFERENCES courses(id),
    enrolled_at TEXT NOT NULL,
    status TEXT NOT NULL,
    UNIQUE (student_id, course_id)
);
CREATE TABLE exams (
    id INTEGER PRIMARY KEY,
    enrollment_id INTEGER NOT NULL REFERENCES enrollments(id),
    kind TEXT NOT NULL,
    grade REAL NOT NULL,
    taken_at TEXT NOT NULL
);
CREATE TABLE attendance (
    id INTEGER PRIMARY KEY,
    enrollment_id INTEGER NOT NULL REFERENCES enrollments(id),
    attended_on TEXT NOT NULL,
    present INTEGER NOT NULL
);
"""


def build_edu(path: Path) -> None:
    rng = random.Random(37)
    conn = _connect(path)
    try:
        conn.executescript(EDU_SCHEMA)
        teachers = [
            ("Ana Torres", "ana.torres@example.com", "Data Science"),
            ("Luis Vega", "luis.vega@example.com", "Databases"),
            ("Marta Sol", "marta.sol@example.com", "Programming"),
            ("Diego Rey", "diego.rey@example.com", "Statistics"),
            ("Clara Peña", "clara.pena@example.com", "Cloud"),
            ("Iván Mora", "ivan.mora@example.com", "Security"),
        ]
        conn.executemany("INSERT INTO teachers (name, email, specialty) VALUES (?, ?, ?)", teachers)

        courses = [
            ("Data Science 101", 1, "Beginner", 30, 350),
            ("Advanced SQL", 2, "Advanced", 40, 420),
            ("Python Basics", 3, "Beginner", 35, 300),
            ("Advanced Python", 3, "Advanced", 45, 450),
            ("Applied Statistics", 4, "Intermediate", 40, 400),
            ("Machine Learning", 1, "Advanced", 50, 600),
            ("Cloud AWS", 5, "Intermediate", 45, 550),
            ("Web Security", 6, "Intermediate", 40, 480),
            ("Databases II", 2, "Intermediate", 40, 410),
            ("Data Visualization", 1, "Beginner", 25, 280),
        ]
        conn.executemany(
            "INSERT INTO courses (name, teacher_id, level, hours, price) VALUES (?, ?, ?, ?, ?)",
            courses,
        )

        first = [
            "Lucía",
            "Mateo",
            "Sofía",
            "Hugo",
            "Valentina",
            "Martín",
            "Elena",
            "Diego",
            "Carmen",
            "Pablo",
            "Ana",
            "Javier",
            "María",
            "Andrés",
            "Laura",
            "Sergio",
            "Paula",
            "Daniel",
            "Clara",
            "Iván",
            "Nuria",
            "Tomás",
            "Alba",
            "Raúl",
            "Marta",
            "Bruno",
            "Inés",
            "Óscar",
            "Silvia",
            "Adrián",
            "Rocío",
            "Manuel",
            "Aitana",
            "Gonzalo",
            "Vera",
            "Rubén",
            "Noa",
            "Alejandro",
            "Candela",
            "Héctor",
        ]
        last = [
            "García",
            "Fernández",
            "López",
            "Martínez",
            "Sánchez",
            "Pérez",
            "Gómez",
            "Rodríguez",
            "Ruiz",
            "Díaz",
            "Torres",
            "Navarro",
            "Vidal",
            "Romero",
        ]
        countries = ["Spain", "Mexico", "Colombia", "Argentina", "Chile", "Peru"]
        students = []
        for index in range(1, 41):
            students.append(
                (
                    index,
                    f"{first[index - 1]} {rng.choice(last)}",
                    f"student{index}@example.com",
                    rng.choice(countries),
                    _iso(date(2025, 1, 1) + timedelta(days=rng.randint(0, 500))),
                )
            )
        conn.executemany(
            "INSERT INTO students (id, name, email, country, enrolled_at) VALUES (?, ?, ?, ?, ?)",
            students,
        )

        enrollments = []
        enrollment_id = 1
        seen: set[tuple[int, int]] = set()
        for student in students:
            for course_id in rng.sample(range(1, 11), rng.randint(1, 4)):
                key = (student[0], course_id)
                if key in seen:
                    continue
                seen.add(key)
                enrollments.append(
                    (
                        enrollment_id,
                        student[0],
                        course_id,
                        _iso(date(2026, 1, 1) + timedelta(days=rng.randint(0, 240))),
                        rng.choices(["active", "completed"], [0.55, 0.45])[0],
                    )
                )
                enrollment_id += 1
        conn.executemany(
            "INSERT INTO enrollments (id, student_id, course_id, enrolled_at, status) VALUES (?, ?, ?, ?, ?)",
            enrollments,
        )

        exams = []
        exam_id = 1
        for enrollment in enrollments:
            for _ in range(rng.randint(1, 3)):
                grade = min(10.0, max(0.0, rng.gauss(6.4, 1.9)))
                if enrollment[1] % 7 == 0:
                    grade = max(0.0, grade - 3.5)
                exams.append(
                    (
                        exam_id,
                        enrollment[0],
                        rng.choice(["midterm", "final", "practical"]),
                        round(grade, 1),
                        _iso(date(2026, 2, 1) + timedelta(days=rng.randint(0, 250))),
                    )
                )
                exam_id += 1
        conn.executemany(
            "INSERT INTO exams (id, enrollment_id, kind, grade, taken_at) VALUES (?, ?, ?, ?, ?)",
            exams,
        )

        attendance = []
        attendance_id = 1
        for enrollment in enrollments:
            course_id = enrollment[2]
            probability = 0.55 if course_id == 2 else 0.82
            for session in range(6):
                attendance.append(
                    (
                        attendance_id,
                        enrollment[0],
                        _iso(date(2026, 3, 2) + timedelta(days=session * 7)),
                        int(rng.random() < probability),
                    )
                )
                attendance_id += 1
        conn.executemany(
            "INSERT INTO attendance (id, enrollment_id, attended_on, present) VALUES (?, ?, ?, ?)",
            attendance,
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Commerce with an irregular schema and no FKs
# ---------------------------------------------------------------------------

EDGE_SCHEMA = """
CREATE TABLE "Customers" (
    id INTEGER PRIMARY KEY,
    "Full Name" TEXT NOT NULL,
    email TEXT,
    city TEXT
);
CREATE TABLE "orders_2026" (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER,
    ordered_at TEXT NOT NULL,
    status TEXT NOT NULL,
    total REAL NOT NULL DEFAULT 0
);
CREATE TABLE order_lines (
    order_id INTEGER NOT NULL,
    line_number INTEGER NOT NULL,
    product_id INTEGER,
    quantity INTEGER NOT NULL,
    price REAL NOT NULL,
    PRIMARY KEY (order_id, line_number)
);
CREATE TABLE catalog (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    price REAL NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE VIRTUAL TABLE search_index USING fts5(name, description);
"""


def build_edge(path: Path) -> None:
    rng = random.Random(51)
    conn = _connect(path)
    try:
        conn.executescript(EDGE_SCHEMA)
        customers = [
            (1, "Ana García", "ana@example.com", "Madrid"),
            (2, "Bruno Díaz", "bruno@example.com", "Barcelona"),
            (3, "Carla Ruiz", "carla@example.com", "Valencia"),
            (4, "Lucía Ferrer", "lucia@example.com", "Seville"),
            (5, "Diego Sanz", "diego@example.com", "Bilbao"),
            (6, "Elena Moya", "elena@example.com", "Madrid"),
            (7, "Fabián Ortiz", "fabian@example.com", "Málaga"),
            (8, "Gloria Peña", "gloria@example.com", "Zaragoza"),
            (9, "Hugo León", "hugo@example.com", "Murcia"),
            (10, "Inés Vidal", "ines@example.com", "Granada"),
        ]
        conn.executemany(
            'INSERT INTO "Customers" (id, "Full Name", email, city) VALUES (?, ?, ?, ?)',
            customers,
        )

        products = [
            (1, "Atlas 14 Laptop", "Computing", 899.99),
            (2, "27-inch 4K Monitor", "Computing", 329.00),
            (3, "Mechanical Keyboard", "Computing", 79.99),
            (4, "Wireless Mouse", "Computing", 39.99),
            (5, "Wireless Headphones Pro", "Audio", 89.99),
            (6, "Bluetooth Mini Speaker", "Audio", 59.99),
            (7, "USB Microphone", "Audio", 119.00),
            (8, "Portable Speaker", "Audio", 49.99),
            (9, "LED Lamp", "Home", 24.99),
            (10, "Coffee Maker", "Home", 89.99),
            (11, "Ergonomic Chair", "Home", 199.00),
            (12, "HD Webcam", "Computing", 69.99),
        ]
        conn.executemany(
            "INSERT INTO catalog (id, name, category, price, active) VALUES (?, ?, ?, ?, 1)",
            products,
        )
        descriptions = {
            1: "Lightweight laptop with a 14-inch screen and 16 GB of RAM.",
            2: "27-inch 4K monitor with an IPS panel.",
            3: "Mechanical keyboard with tactile switches.",
            4: "Ergonomic wireless mouse with a USB receiver.",
            5: "Wireless headphones with noise cancellation.",
            6: "Compact Bluetooth speaker, water resistant.",
            7: "USB microphone for recording and streaming.",
            8: "Portable speaker with long battery life.",
            9: "Dimmable LED desk lamp.",
            10: "Programmable 12-cup coffee maker.",
            11: "Ergonomic chair with lumbar support.",
            12: "HD webcam with built-in microphone.",
        }
        conn.executemany(
            "INSERT INTO search_index (name, description) VALUES (?, ?)",
            [(product[1], descriptions[product[0]]) for product in products],
        )

        orders = []
        order_lines = []
        for order_id in range(1, 31):
            customer_id = rng.randint(1, 10)
            if order_id in {1, 6, 11, 16, 21}:
                customer_id = 4  # highest-spending customer
            ordered = date(2026, 1, 1) + timedelta(days=rng.randint(0, 300))
            orders.append(
                (
                    order_id,
                    customer_id,
                    _iso(ordered),
                    rng.choice(["paid", "shipped", "pending", "cancelled"]),
                )
            )
            line_count = rng.randint(1, 4)
            for line_number in range(1, line_count + 1):
                product = rng.choice(products)
                quantity = rng.choices([1, 2, 3, 5], [0.6, 0.25, 0.1, 0.05])[0]
                if customer_id == 4:
                    quantity += 2
                if order_id == 1 and line_number == 1:
                    # Guarantee a unique best-selling product for the evaluation.
                    product = products[7]
                    quantity = 20
                order_lines.append((order_id, line_number, product[0], quantity, product[3]))
        conn.executemany(
            'INSERT INTO "orders_2026" (id, customer_id, ordered_at, status, total) VALUES (?, ?, ?, ?, 0)',
            orders,
        )
        conn.executemany(
            "INSERT INTO order_lines (order_id, line_number, product_id, quantity, price) "
            "VALUES (?, ?, ?, ?, ?)",
            order_lines,
        )
        conn.execute(
            'UPDATE "orders_2026" SET total = ('
            "SELECT ROUND(SUM(quantity * price), 2) FROM order_lines "
            'WHERE order_lines.order_id = "orders_2026".id)'
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Mini library
# ---------------------------------------------------------------------------

MINI_SCHEMA = """
CREATE TABLE books (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    author TEXT NOT NULL,
    published_year INTEGER NOT NULL,
    genre TEXT NOT NULL,
    price REAL NOT NULL
);
CREATE TABLE sales (
    id INTEGER PRIMARY KEY,
    book_id INTEGER NOT NULL REFERENCES books(id),
    sold_on TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    unit_price REAL NOT NULL
);
"""


def build_mini(path: Path) -> None:
    rng = random.Random(67)
    conn = _connect(path)
    try:
        conn.executescript(MINI_SCHEMA)
        books = [
            ("Dune", "Frank Herbert", 1965, "Science Fiction", 18.5),
            ("Foundation", "Isaac Asimov", 1951, "Science Fiction", 16.0),
            ("Neuromancer", "William Gibson", 1984, "Science Fiction", 17.5),
            ("The Three-Body Problem", "Cixin Liu", 2008, "Science Fiction", 21.0),
            ("Solaris", "Stanislaw Lem", 1961, "Science Fiction", 15.5),
            ("One Hundred Years of Solitude", "Gabriel García Márquez", 1967, "Novel", 22.0),
            ("The Shadow of the Wind", "Carlos Ruiz Zafón", 2001, "Novel", 19.5),
            ("The Name of the Wind", "Patrick Rothfuss", 2007, "Novel", 24.0),
            ("The House of the Spirits", "Isabel Allende", 1982, "Novel", 20.0),
            ("Hopscotch", "Julio Cortázar", 1963, "Novel", 21.5),
            ("Pedro Páramo", "Juan Rulfo", 1955, "Novel", 14.0),
            ("Sapiens", "Yuval Noah Harari", 2011, "History", 26.0),
            ("Guns, Germs, and Steel", "Jared Diamond", 1997, "History", 23.5),
            ("The Russian Revolution", "Orlando Figes", 1996, "History", 28.0),
            ("A History of Spain", "Raymond Carr", 1982, "History", 25.0),
            ("Clean Code", "Robert C. Martin", 2008, "Technology", 32.0),
            ("The Pragmatic Programmer", "Hunt and Thomas", 1999, "Technology", 35.0),
            ("Designing Data-Intensive Applications", "Martin Kleppmann", 2017, "Technology", 39.0),
            ("Refactoring", "Martin Fowler", 1999, "Technology", 37.5),
            ("Fluent Python", "Luciano Ramalho", 2015, "Technology", 34.0),
        ]
        conn.executemany(
            "INSERT INTO books (title, author, published_year, genre, price) VALUES (?, ?, ?, ?, ?)",
            books,
        )
        sales = []
        for sale_id in range(1, 101):
            book_id = rng.randint(1, 20)
            if sale_id <= 12:
                book_id = 1  # Dune, the best seller
            quantity = rng.choices([1, 2, 3, 4], [0.5, 0.3, 0.15, 0.05])[0]
            sales.append(
                (
                    sale_id,
                    book_id,
                    _iso(date(2026, 1, 1) + timedelta(days=rng.randint(0, 300))),
                    quantity,
                    books[book_id - 1][4],
                )
            )
        conn.executemany(
            "INSERT INTO sales (id, book_id, sold_on, quantity, unit_price) VALUES (?, ?, ?, ?, ?)",
            sales,
        )
        conn.commit()
    finally:
        conn.close()
