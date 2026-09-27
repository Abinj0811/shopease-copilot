"""Seed the six app tables with deterministic, fictional en_IN data.

Usage:
    uv run python data/seed_db.py            # seed if empty, else report counts
    uv run python data/seed_db.py --reset    # wipe the six tables and reseed

Reads config/seed.yaml and config/business_rules.yaml. Same random_seed ->
identical rows and order numbers on every --reset. Runs in one transaction,
so a failure never leaves half-seeded data. Only touches the app database
(POSTGRES_DB), never LiteLLM's own.

Edge-case orders are added on top of n_orders and are detectable from the
data alone; regular orders are generated so they never match these rules:
  boundary_return_date        delivered, days since delivery == return window
  one_day_past_return_window  delivered, days since delivery == window + 1
  cancelled_after_shipped     shipped, with a cancel_request_ticket_category ticket
  cod_payment                 payment_mode "cod" and has a refund
  high_value_order            total_inr > high_value_order_inr
  repeated_refunds            two or more refunds on one order
("days since delivery" = as_of_date minus the delivery date in `timezone`.)
"""

import argparse
import random
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import psycopg
import yaml
from faker import Faker
from psycopg import sql
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

ROOT = Path(__file__).resolve().parent.parent
# Plain `python data/seed_db.py` only puts data/ on sys.path, not the repo root.
sys.path.insert(0, str(ROOT))

from common.business_rules import (
    BusinessRules,
    OrderStatus,
    load_business_rules,
)
from common.settings import SettingsError, load_settings

SEED_PATH = ROOT / "config" / "seed.yaml"

# Insert order respects foreign keys.
TABLES = ("customers", "products", "orders", "order_items", "refunds", "tickets")
EDGE_CASES = (
    "boundary_return_date",
    "one_day_past_return_window",
    "cancelled_after_shipped",
    "cod_payment",
    "high_value_order",
    "repeated_refunds",
)
COD = "cod"
SECONDS_PER_DAY = 24 * 60 * 60


class ConfigError(Exception):
    """config/seed.yaml or business_rules.yaml can't produce a valid dataset."""


def _check_distribution(value: dict[str, float]) -> dict[str, float]:
    if not value or any(p < 0 for p in value.values()):
        raise ValueError("must be non-empty with no negative probabilities")
    if abs(sum(value.values()) - 1.0) > 1e-6:
        raise ValueError(f"must sum to 1.0 (sums to {sum(value.values())})")
    return value


class PriceRange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    price_min_inr: int = Field(gt=0)
    price_max_inr: int = Field(gt=0)

    @model_validator(mode="after")
    def _min_below_max(self) -> "PriceRange":
        if self.price_min_inr > self.price_max_inr:
            raise ValueError("price_min_inr must not exceed price_max_inr")
        return self


class SeedConfig(BaseModel):
    """Validated config/seed.yaml."""

    model_config = ConfigDict(extra="forbid")

    random_seed: int
    as_of_date: date
    timezone: str
    n_customers: int = Field(gt=0)
    n_products: int = Field(gt=0)
    n_orders: int = Field(ge=0)
    order_no_start: int = Field(gt=0)
    order_history_days: int = Field(gt=0)
    items_per_order_max: int = Field(ge=1)
    status_distribution: dict[OrderStatus, float]
    payment_mode_distribution: dict[str, float]
    customer_tier_distribution: dict[str, float]
    metro_customer_share: float = Field(ge=0, le=1)
    metro_cities: list[str] = Field(min_length=1)
    ticket_rate: float = Field(ge=0, le=1)
    ticket_categories: list[str] = Field(min_length=1)
    cancel_request_ticket_category: str
    brands: list[str] = Field(min_length=1)
    product_catalog: dict[str, PriceRange] = Field(min_length=1)
    edge_cases_per_category: dict[str, int]

    @field_validator(
        "status_distribution",
        "payment_mode_distribution",
        "customer_tier_distribution",
    )
    @classmethod
    def _distributions(cls, value: dict[str, float]) -> dict[str, float]:
        return _check_distribution(value)

    @field_validator("edge_cases_per_category")
    @classmethod
    def _known_edge_cases(cls, value: dict[str, int]) -> dict[str, int]:
        if set(value) != set(EDGE_CASES):
            raise ValueError(f"keys must be exactly: {', '.join(EDGE_CASES)}")
        if any(n < 0 for n in value.values()):
            raise ValueError("counts must be zero or greater")
        return value

    @model_validator(mode="after")
    def _cross_checks(self) -> "SeedConfig":
        if COD not in self.payment_mode_distribution:
            raise ValueError(f"payment_mode_distribution must include '{COD}'")
        if len(self.payment_mode_distribution) < 2:
            raise ValueError("payment_mode_distribution needs a non-COD mode too")
        if self.cancel_request_ticket_category in self.ticket_categories:
            raise ValueError(
                "cancel_request_ticket_category must not be in ticket_categories"
            )
        if self.n_products < len(self.product_catalog):
            raise ValueError("n_products must be at least the number of categories")
        return self


@dataclass
class Customer:
    name: str
    email: str
    phone: str
    city: str
    tier: str
    created_at: datetime
    sla_days: int


@dataclass
class Product:
    sku: str
    name: str
    category: str
    price_inr: int
    warranty_months: int
    returnable_days: int


@dataclass
class Refund:
    amount_inr: int
    reason: str
    status: str
    created_at: datetime


@dataclass
class Ticket:
    category: str
    status: str
    summary: str
    created_at: datetime


@dataclass
class OrderSpec:
    customer: int
    placed_at: datetime
    status: str
    payment_mode: str
    delivered_at: datetime | None
    items: list[tuple[int, int]]  # (product index, qty)
    refunds: list[Refund] = field(default_factory=list)
    ticket: Ticket | None = None
    edge_case: str | None = None
    order_no: str = ""


class Generator:
    """Builds the whole dataset in memory, deterministically."""

    def __init__(self, cfg: SeedConfig, rules: BusinessRules) -> None:
        self.cfg = cfg
        self.rules = rules
        self.rng = random.Random(cfg.random_seed)
        self.fake = Faker("en_IN")
        self.fake.seed_instance(cfg.random_seed)
        try:
            self.tz = ZoneInfo(cfg.timezone)
        except ZoneInfoNotFoundError:
            raise ConfigError(f"unknown timezone {cfg.timezone!r}") from None
        self.as_of_end = datetime.combine(
            cfg.as_of_date + timedelta(days=1), time(), self.tz
        )
        self.threshold = rules.high_value_order_inr
        windows = set(rules.return_window_days.values())
        # Day offsets that would make a regular delivered order look like a
        # boundary / one-day-past edge case, whatever its category.
        self.avoid_days = windows | {w + 1 for w in windows}
        self.non_cod_modes = {
            k: v for k, v in cfg.payment_mode_distribution.items() if k != COD
        }

        self._check_catalog()
        self.customers = self._customers()
        self.products = self._products()
        self.returnable = [i for i, p in enumerate(self.products) if p.returnable_days]
        if not self.returnable:
            raise ConfigError("no product category has a return window above 0")
        self.orders: list[OrderSpec] = []

    # ---- helpers -------------------------------------------------------

    def _check_catalog(self) -> None:
        for category, prices in self.cfg.product_catalog.items():
            if prices.price_max_inr >= self.threshold:
                raise ConfigError(
                    f"product_catalog.{category}.price_max_inr must be below "
                    f"high_value_order_inr ({self.threshold})"
                )

    def _pick(self, distribution: dict[str, float]) -> str:
        keys = list(distribution)
        return self.rng.choices(keys, weights=list(distribution.values()))[0]

    def _days_ago(self, days: int) -> datetime:
        """A random moment on the calendar day `days` before as_of_date."""
        day = self.cfg.as_of_date - timedelta(days=days)
        start = datetime.combine(day, time(), self.tz)
        return start + timedelta(seconds=self.rng.randrange(SECONDS_PER_DAY))

    def _between(self, start: datetime, end: datetime) -> datetime:
        span = max(int((end - start).total_seconds()), 1)
        return start + timedelta(seconds=self.rng.randrange(span))

    def total(self, items: list[tuple[int, int]]) -> int:
        return sum(self.products[p].price_inr * qty for p, qty in items)

    def _regular_items(self, pool: list[int]) -> list[tuple[int, int]]:
        """1..items_per_order_max distinct products, total kept <= threshold."""
        count = self.rng.randint(1, self.cfg.items_per_order_max)
        items: list[tuple[int, int]] = []
        total = 0
        for p in self.rng.sample(pool, k=min(count, len(pool))):
            price = self.products[p].price_inr
            if total + price <= self.threshold:
                items.append((p, 1))
                total += price
        return items

    def _in_window_day(self, items: list[tuple[int, int]], sla: int) -> int:
        """A days-since-delivery inside every item's window, avoiding edge offsets."""
        window = min(self.products[p].returnable_days for p, _ in items)
        latest = self.cfg.order_history_days - sla
        options = [
            d for d in range(min(window, latest + 1)) if d not in self.avoid_days
        ]
        if not options:
            raise ConfigError(
                f"no valid in-window delivery day for a {window}-day return window; "
                "widen return windows or order_history_days"
            )
        return self.rng.choice(options)

    def _ticket(
        self, order: OrderSpec, category: str, status: str, summary: str
    ) -> Ticket:
        return Ticket(
            category, status, summary, self._between(order.placed_at, self.as_of_end)
        )

    def _named_category(self, preferred: str) -> str:
        """Use a configured ticket category if present, else the first one."""
        if preferred in self.cfg.ticket_categories:
            return preferred
        return self.cfg.ticket_categories[0]

    # ---- customers and products -----------------------------------------

    def _customers(self) -> list[Customer]:
        metro = set(self.cfg.metro_cities)
        customers = []
        for _ in range(self.cfg.n_customers):
            if self.rng.random() < self.cfg.metro_customer_share:
                city = self.rng.choice(self.cfg.metro_cities)
            else:
                city = self.fake.city()
                while city in metro:
                    city = self.fake.city()
            zone = "metro" if city in metro else "other"
            joined = self._days_ago(
                self.cfg.order_history_days
                + self.rng.randint(0, self.cfg.order_history_days)
            )
            customers.append(
                Customer(
                    name=self.fake.name(),
                    email=self.fake.unique.safe_email(),
                    phone=self.fake.phone_number(),
                    city=city,
                    tier=self._pick(self.cfg.customer_tier_distribution),
                    created_at=joined,
                    sla_days=getattr(self.rules.shipping_sla_days, zone),
                )
            )
        return customers

    def _products(self) -> list[Product]:
        categories = list(self.cfg.product_catalog)
        products = []
        for i in range(self.cfg.n_products):
            category = categories[i % len(categories)]
            prices = self.cfg.product_catalog[category]
            label = category.replace("_", " ").title()
            products.append(
                Product(
                    sku=f"SE-{category[:3].upper()}-{i + 1:04d}",
                    name=f"{self.rng.choice(self.cfg.brands)} {label} {i + 1:03d}",
                    category=category,
                    price_inr=self.rng.randint(
                        prices.price_min_inr, prices.price_max_inr
                    ),
                    warranty_months=self.rules.warranty_months_for(category),
                    returnable_days=self.rules.return_window_for(category),
                )
            )
        return products

    # ---- regular orders -------------------------------------------------

    def regular_orders(self) -> None:
        all_products = list(range(len(self.products)))
        for _ in range(self.cfg.n_orders):
            customer = self.rng.randrange(len(self.customers))
            sla = self.customers[customer].sla_days
            status = self._pick(self.cfg.status_distribution)
            refunds: list[Refund] = []

            if status == "delivered":
                items = self._regular_items(all_products)
                latest = self.cfg.order_history_days - sla
                options = [d for d in range(latest + 1) if d not in self.avoid_days]
                delivered = self._days_ago(self.rng.choice(options))
                placed = delivered - timedelta(days=sla)
                mode = self._pick(self.cfg.payment_mode_distribution)
            elif status == "returned":
                items = self._regular_items(self.returnable)
                delivered = self._days_ago(self._in_window_day(items, sla))
                placed = delivered - timedelta(days=sla)
                mode = self._pick(self.non_cod_modes)  # COD refunds are an edge case
                refunds.append(
                    Refund(
                        amount_inr=self.total(items),
                        reason="Product returned within the return window.",
                        status=self.rng.choice(["approved", "paid"]),
                        created_at=self._between(delivered, self.as_of_end),
                    )
                )
            elif status == "cancelled":
                items = self._regular_items(all_products)
                delivered = None
                placed = self._days_ago(
                    self.rng.randint(0, self.cfg.order_history_days)
                )
                mode = self._pick(self.cfg.payment_mode_distribution)
            else:  # placed, confirmed, shipped: not yet due for delivery
                items = self._regular_items(all_products)
                delivered = None
                placed = self._days_ago(self.rng.randint(0, sla - 1))
                mode = self._pick(self.cfg.payment_mode_distribution)

            order = OrderSpec(customer, placed, status, mode, delivered, items, refunds)
            if self.rng.random() < self.cfg.ticket_rate:
                category = self.rng.choice(self.cfg.ticket_categories)
                order.ticket = self._ticket(
                    order,
                    category,
                    self.rng.choice(["open", "resolved"]),
                    f"Customer contacted support: {category.replace('_', ' ')}.",
                )
            self.orders.append(order)

    # ---- edge-case orders -----------------------------------------------

    def edge_orders(self) -> None:
        counts = self.cfg.edge_cases_per_category
        by_price = sorted(
            self.returnable, key=lambda p: self.products[p].price_inr, reverse=True
        )
        for k in range(counts["boundary_return_date"]):
            self._window_edge(k, extra_days=0, name="boundary_return_date")
        for k in range(counts["one_day_past_return_window"]):
            self._window_edge(k, extra_days=1, name="one_day_past_return_window")
        for _ in range(counts["cancelled_after_shipped"]):
            self._cancelled_after_shipped()
        for k in range(counts["cod_payment"]):
            self._cod_refund(k)
        for k in range(counts["high_value_order"]):
            self._high_value(by_price[k % len(by_price)])
        for k in range(counts["repeated_refunds"]):
            self._repeated_refunds(k)

    def _customer(self) -> tuple[int, int]:
        c = self.rng.randrange(len(self.customers))
        return c, self.customers[c].sla_days

    def _returnable_product(self, k: int) -> int:
        """Cycle through returnable products so edge cases span categories."""
        return self.returnable[(k * 7) % len(self.returnable)]

    def _window_edge(self, k: int, extra_days: int, name: str) -> None:
        customer, sla = self._customer()
        p = self._returnable_product(k)
        delivered = self._days_ago(self.products[p].returnable_days + extra_days)
        order = OrderSpec(
            customer,
            delivered - timedelta(days=sla),
            "delivered",
            self._pick(self.non_cod_modes),
            delivered,
            [(p, 1)],
            edge_case=name,
        )
        order.ticket = self._ticket(
            order,
            self._named_category("return_request"),
            "open",
            "Customer wants to return the product.",
        )
        self.orders.append(order)

    def _cancelled_after_shipped(self) -> None:
        customer, sla = self._customer()
        order = OrderSpec(
            customer,
            self._days_ago(self.rng.randint(0, sla - 1)),
            "shipped",
            self._pick(self.cfg.payment_mode_distribution),
            None,
            self._regular_items(list(range(len(self.products)))),
            edge_case="cancelled_after_shipped",
        )
        order.ticket = self._ticket(
            order,
            self.cfg.cancel_request_ticket_category,
            "open",
            "Customer asked to cancel the order after it had shipped.",
        )
        self.orders.append(order)

    def _cod_refund(self, k: int) -> None:
        customer, sla = self._customer()
        items = [(self._returnable_product(k + 3), 1)]
        delivered = self._days_ago(self._in_window_day(items, sla))
        order = OrderSpec(
            customer,
            delivered - timedelta(days=sla),
            "returned",
            COD,
            delivered,
            items,
            edge_case="cod_payment",
        )
        order.refunds.append(
            Refund(
                self.total(items),
                "COD order returned; refund goes by bank transfer.",
                "approved",
                self._between(delivered, self.as_of_end),
            )
        )
        order.ticket = self._ticket(
            order,
            self._named_category("refund_status"),
            "open",
            "Customer asking when the COD refund will arrive.",
        )
        self.orders.append(order)

    def _high_value(self, p: int) -> None:
        customer, sla = self._customer()
        qty = self.threshold // self.products[p].price_inr + 1
        items = [(p, qty)]
        delivered = self._days_ago(self._in_window_day(items, sla))
        order = OrderSpec(
            customer,
            delivered - timedelta(days=sla),
            "delivered",
            self._pick(self.non_cod_modes),
            delivered,
            items,
            edge_case="high_value_order",
        )
        order.refunds.append(
            Refund(
                self.total(items),
                "Customer requested a full refund on a high-value order.",
                "requested",
                self._between(delivered, self.as_of_end),
            )
        )
        order.ticket = self._ticket(
            order,
            self._named_category("return_request"),
            "open",
            "Customer wants a full refund on a large order.",
        )
        self.orders.append(order)

    def _repeated_refunds(self, k: int) -> None:
        customer, sla = self._customer()
        items = [(self._returnable_product(k + 5), 1)]
        delivered = self._days_ago(self._in_window_day(items, sla))
        order = OrderSpec(
            customer,
            delivered - timedelta(days=sla),
            "delivered",
            self._pick(self.non_cod_modes),
            delivered,
            items,
            edge_case="repeated_refunds",
        )
        first = self._between(delivered, self.as_of_end)
        second = self._between(first, self.as_of_end)
        amount = self.total(items)
        order.refunds += [
            Refund(
                amount,
                "Refund requested; rejected after inspection.",
                "rejected",
                first,
            ),
            Refund(amount, "Customer requested a refund again.", "requested", second),
        ]
        order.ticket = self._ticket(
            order,
            self._named_category("refund_status"),
            "open",
            "Customer is asking for a refund again after a rejection.",
        )
        self.orders.append(order)

    # ---- finish ---------------------------------------------------------

    def finalize(self) -> None:
        """Number orders chronologically, so edge cases aren't a number range."""
        self.orders.sort(key=lambda o: o.placed_at)
        for i, order in enumerate(self.orders):
            order.order_no = f"SE-{self.cfg.order_no_start + i}"


def load_seed_config() -> SeedConfig:
    data = yaml.safe_load(SEED_PATH.read_text(encoding="utf-8"))
    return SeedConfig.model_validate(data)


def insert_returning_ids(
    cur: psycopg.Cursor, query: str, rows: list[tuple]
) -> list[int]:
    cur.executemany(query, rows, returning=True)
    ids = []
    while True:
        ids.append(cur.fetchone()[0])
        if not cur.nextset():
            break
    return ids


def write(conn: psycopg.Connection, gen: Generator) -> None:
    with conn.cursor() as cur:
        customer_ids = insert_returning_ids(
            cur,
            "INSERT INTO customers (name, email, phone, city, tier, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            [
                (c.name, c.email, c.phone, c.city, c.tier, c.created_at)
                for c in gen.customers
            ],
        )
        product_ids = insert_returning_ids(
            cur,
            "INSERT INTO products (sku, name, category, price_inr, warranty_months, "
            "returnable_days) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            [
                (
                    p.sku,
                    p.name,
                    p.category,
                    p.price_inr,
                    p.warranty_months,
                    p.returnable_days,
                )
                for p in gen.products
            ],
        )
        order_ids = insert_returning_ids(
            cur,
            "INSERT INTO orders (order_no, customer_id, placed_at, status, payment_mode, "
            "total_inr, delivered_at) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
            [
                (
                    o.order_no,
                    customer_ids[o.customer],
                    o.placed_at,
                    o.status,
                    o.payment_mode,
                    gen.total(o.items),
                    o.delivered_at,
                )
                for o in gen.orders
            ],
        )
        cur.executemany(
            "INSERT INTO order_items (order_id, product_id, qty, unit_price_inr) "
            "VALUES (%s, %s, %s, %s)",
            [
                (oid, product_ids[p], qty, gen.products[p].price_inr)
                for oid, o in zip(order_ids, gen.orders, strict=True)
                for p, qty in o.items
            ],
        )
        cur.executemany(
            "INSERT INTO refunds (order_id, amount_inr, reason, status, created_at) "
            "VALUES (%s, %s, %s, %s, %s)",
            [
                (oid, r.amount_inr, r.reason, r.status, r.created_at)
                for oid, o in zip(order_ids, gen.orders, strict=True)
                for r in o.refunds
            ],
        )
        cur.executemany(
            "INSERT INTO tickets (customer_id, order_id, category, status, summary, "
            "created_at) VALUES (%s, %s, %s, %s, %s, %s)",
            [
                (
                    customer_ids[o.customer],
                    oid,
                    o.ticket.category,
                    o.ticket.status,
                    o.ticket.summary,
                    o.ticket.created_at,
                )
                for oid, o in zip(order_ids, gen.orders, strict=True)
                if o.ticket
            ],
        )


def table_counts(conn: psycopg.Connection) -> dict[str, int]:
    return {
        t: conn.execute(
            sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(t))
        ).fetchone()[0]
        for t in TABLES
    }


def print_counts(counts: dict[str, int]) -> None:
    width = max(len(t) for t in counts)
    for table, n in counts.items():
        print(f"  {table:<{width}}  {n}")


def fail(message: str, hint: str | None = None) -> int:
    print(f"FAIL  {message}")
    if hint:
        print(f"      hint: {hint}")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--reset", action="store_true", help="wipe the six tables and reseed"
    )
    args = parser.parse_args()

    try:
        cfg = load_seed_config()
        rules = load_business_rules()
        settings = load_settings()
    except (ValidationError, SettingsError, yaml.YAMLError) as exc:
        return fail(f"invalid configuration:\n{exc}")

    try:
        with psycopg.connect(
            host=settings.postgres_host,
            port=settings.postgres_port,
            user=settings.postgres_user,
            password=settings.postgres_password.get_secret_value(),
            dbname=settings.postgres_db,
            connect_timeout=settings.infra_check_timeout_seconds,
        ) as conn:
            missing = [
                t
                for t in TABLES
                if conn.execute("SELECT to_regclass(%s)", (f"public.{t}",)).fetchone()[
                    0
                ]
                is None
            ]
            if missing:
                return fail(
                    f"tables missing: {', '.join(missing)}",
                    "run `uv run alembic -c db/alembic.ini upgrade head` first",
                )

            counts = table_counts(conn)
            if any(counts.values()) and not args.reset:
                print("Already seeded; nothing changed (use --reset to regenerate).")
                print_counts(counts)
                return 0

            if args.reset:
                conn.execute(
                    sql.SQL("TRUNCATE {} RESTART IDENTITY CASCADE").format(
                        sql.SQL(", ").join(sql.Identifier(t) for t in TABLES)
                    )
                )

            gen = Generator(cfg, rules)
            gen.regular_orders()
            gen.edge_orders()
            gen.finalize()
            write(conn, gen)
            counts = table_counts(conn)
    except ConfigError as exc:
        return fail(f"config: {exc}")
    except psycopg.Error as exc:
        reason = (
            str(exc).strip().splitlines()[-1]
            if str(exc).strip()
            else type(exc).__name__
        )
        return fail(f"database error: {reason}", "is `docker compose up -d` running?")

    print(
        f"Seeded ({'after reset' if args.reset else 'fresh'}), as_of_date {cfg.as_of_date}:"
    )
    print_counts(counts)
    print("\nEdge-case orders:")
    for name in EDGE_CASES:
        numbers = [o.order_no for o in gen.orders if o.edge_case == name]
        print(f"  {name:<27}  {', '.join(numbers) or '(none)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
