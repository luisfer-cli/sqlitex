"""Demo database for trying SQLiteX without your own data."""

from __future__ import annotations

import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from sqlitex.errors import DatabaseError

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE categories (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT
);

CREATE TABLE products (
    id INTEGER PRIMARY KEY,
    category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE RESTRICT,
    name TEXT NOT NULL,
    price REAL NOT NULL CHECK (price >= 0),
    stock INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE customers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    city TEXT,
    country TEXT NOT NULL DEFAULT 'Spain',
    segment TEXT NOT NULL,
    registered_at TEXT NOT NULL
);

CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    ordered_at TEXT NOT NULL,
    status TEXT NOT NULL,
    channel TEXT NOT NULL,
    total REAL NOT NULL DEFAULT 0
);

CREATE TABLE order_items (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    unit_price REAL NOT NULL
);

CREATE TABLE employees (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    salary REAL NOT NULL,
    manager_id INTEGER REFERENCES employees(id),
    hired_at TEXT NOT NULL
);

CREATE TABLE reviews (
    id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id),
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    comment TEXT,
    reviewed_at TEXT NOT NULL
);

CREATE TABLE price_history (
    id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id),
    old_price REAL,
    new_price REAL,
    changed_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_orders_customer ON orders(customer_id);
CREATE INDEX idx_orders_date ON orders(ordered_at);
CREATE INDEX idx_order_items_order ON order_items(order_id);
CREATE INDEX idx_products_category ON products(category_id);

CREATE TRIGGER trg_price_history
AFTER UPDATE OF price ON products
WHEN OLD.price <> NEW.price
BEGIN
    INSERT INTO price_history (product_id, old_price, new_price)
    VALUES (OLD.id, OLD.price, NEW.price);
END;

CREATE VIEW product_sales_summary AS
SELECT
    p.id AS product_id,
    p.name AS product,
    c.name AS category,
    COUNT(oi.id) AS lines,
    COALESCE(SUM(oi.quantity), 0) AS units_sold,
    ROUND(COALESCE(SUM(oi.quantity * oi.unit_price), 0), 2) AS revenue
FROM products p
JOIN categories c ON c.id = p.category_id
LEFT JOIN order_items oi ON oi.product_id = p.id
GROUP BY p.id, p.name, c.name;

PRAGMA user_version = 1;
"""


def create_demo_database(path: Path, *, force: bool = False) -> Path:
    """Create an e-commerce SQLite database with realistic data."""

    path = path.expanduser().resolve()
    if path.exists():
        if not force:
            raise DatabaseError(f"File {path} already exists. Use --force to overwrite it.")
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)

    rng = random.Random(42)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA)
        categories = [
            ("Computing", "Laptops, monitors, and peripherals"),
            ("Audio", "Headphones, speakers, and microphones"),
            ("Home", "Small appliances and accessories"),
            ("Sports", "Sports gear and technical clothing"),
            ("Books", "Technical and leisure books"),
        ]
        conn.executemany("INSERT INTO categories (name, description) VALUES (?, ?)", categories)

        product_specs = [
            ("Atlas 14 Laptop", 1),
            ("Vega 27 Monitor", 1),
            ("Nova Mechanical Keyboard", 1),
            ("Pulse Optical Mouse", 1),
            ("Aura Pro Headphones", 2),
            ("Orbit Mini Speaker", 2),
            ("Studio One Microphone", 2),
            ("Bruma Coffee Maker", 3),
            ("Lumen Desk Lamp", 3),
            ("Cirrus Robot Vacuum", 3),
            ("10 kg Steel Dumbbells", 4),
            ("Flow Yoga Mat", 4),
            ("Trail 360 Running Shoes", 4),
            ("Trek Hiking Backpack", 4),
            ("SQL in Depth", 5),
            ("Python for Data", 5),
            ("A History of Computing", 5),
            ("Clara HD Webcam", 1),
            ("USB-C Dock Hub", 1),
            ("1 TB SSD Drive", 1),
            ("Zen Ergonomic Chair", 3),
            ("750 ml Thermal Bottle", 4),
            ("Pulse Sports Watch", 4),
            ("Beat Bluetooth Speaker", 2),
        ]
        start = date(2025, 1, 1)
        products = []
        for index, (name, category_id) in enumerate(product_specs, start=1):
            price = round(rng.uniform(12, 780), 2)
            stock = rng.randint(0, 240)
            created = start + timedelta(days=rng.randint(0, 420))
            products.append(
                (index, category_id, name, price, stock, rng.random() > 0.12, created.isoformat())
            )
        conn.executemany(
            "INSERT INTO products (id, category_id, name, price, stock, active, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            products,
        )

        cities = [
            ("Madrid", "Spain"),
            ("Barcelona", "Spain"),
            ("Valencia", "Spain"),
            ("Seville", "Spain"),
            ("Bilbao", "Spain"),
            ("Mexico City", "Mexico"),
            ("Bogotá", "Colombia"),
            ("Buenos Aires", "Argentina"),
            ("Santiago", "Chile"),
        ]
        first_names = [
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
        ]
        last_names = [
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
        segments = ["consumer", "business", "freelancer"]
        customers = []
        for index in range(1, 46):
            name = f"{rng.choice(first_names)} {rng.choice(last_names)}"
            email = f"customer{index}@example.com"
            city, country = rng.choice(cities)
            registered = start + timedelta(days=rng.randint(0, 500))
            customers.append(
                (index, name, email, city, country, rng.choice(segments), registered.isoformat())
            )
        conn.executemany(
            "INSERT INTO customers (id, name, email, city, country, segment, registered_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            customers,
        )

        employees = [
            (1, "Elena Ruiz", "General Management", 92000.0, None, "2019-02-01"),
            (2, "Marcos Gil", "Sales Director", 71000.0, 1, "2020-05-11"),
            (3, "Sara Peña", "Sales", 38000.0, 2, "2021-09-20"),
            (4, "Iker Soler", "Sales", 36500.0, 2, "2022-01-17"),
            (5, "Nora Campos", "Customer Support", 32000.0, 2, "2022-11-02"),
            (6, "Luis Ferrer", "Data Analyst", 45000.0, 1, "2023-03-14"),
        ]
        conn.executemany(
            "INSERT INTO employees (id, name, role, salary, manager_id, hired_at) VALUES (?, ?, ?, ?, ?, ?)",
            employees,
        )

        statuses = ["pending", "paid", "shipped", "delivered", "cancelled"]
        status_weights = [0.08, 0.22, 0.22, 0.40, 0.08]
        channels = ["web", "app", "store", "phone"]
        order_items: list[tuple[int, int, int, int, float]] = []
        orders: list[tuple[int, int, str, str, str, float]] = []
        line_id = 1
        for order_id in range(1, 161):
            customer_id = rng.randint(1, 45)
            ordered = start + timedelta(days=rng.randint(0, 640))
            status = rng.choices(statuses, weights=status_weights, k=1)[0]
            channel = rng.choices(channels, weights=[0.55, 0.25, 0.15, 0.05], k=1)[0]
            total = 0.0
            chosen = rng.sample(products, rng.randint(1, 4))
            for product in chosen:
                quantity = rng.choices([1, 2, 3, 5], weights=[0.65, 0.22, 0.09, 0.04], k=1)[0]
                unit_price = round(product[3] * rng.uniform(0.9, 1.05), 2)
                total += quantity * unit_price
                order_items.append((line_id, order_id, product[0], quantity, unit_price))
                line_id += 1
            orders.append((order_id, customer_id, ordered.isoformat(), status, channel, round(total, 2)))

        conn.executemany(
            "INSERT INTO orders (id, customer_id, ordered_at, status, channel, total) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            orders,
        )
        conn.executemany(
            "INSERT INTO order_items (id, order_id, product_id, quantity, unit_price) VALUES (?, ?, ?, ?, ?)",
            order_items,
        )

        comments = [
            "Very good quality, I would buy again.",
            "It arrived earlier than expected.",
            "Does exactly what it promises.",
            "Fair price for what it offers.",
            "It was not what I expected.",
            "Excellent value for money.",
            "The packaging arrived damaged.",
            "Works perfectly.",
            "Recommended for daily use.",
            None,
            None,
            "Support replied quickly.",
        ]
        reviews = []
        for index in range(1, 91):
            product = rng.choice(products)
            reviews.append(
                (
                    index,
                    product[0],
                    rng.randint(1, 45),
                    rng.choices([1, 2, 3, 4, 5], weights=[0.05, 0.08, 0.17, 0.35, 0.35], k=1)[0],
                    rng.choice(comments),
                    (start + timedelta(days=rng.randint(30, 650))).isoformat(),
                )
            )
        conn.executemany(
            "INSERT INTO reviews (id, product_id, customer_id, rating, comment, reviewed_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            reviews,
        )

        # A few price changes so the trigger produces history rows.
        conn.execute("UPDATE products SET price = ROUND(price * 1.05, 2) WHERE id IN (2, 5, 11)")
        conn.commit()
    except sqlite3.Error as exc:
        raise DatabaseError(f"Could not create the demo database: {exc}") from exc
    finally:
        conn.close()
    return path
