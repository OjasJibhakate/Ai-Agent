"""Synthetic, labelled retrieval dataset (docs/MEMORY.md section 10). Fully deterministic.

About 500 active memories (plus 10 deleted ones) and 125 queries in seven categories. Distractors
share vocabulary with the targets on purpose, so lexical-only and vector-only both have to work.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field


@dataclass
class Item:
    key: str
    text: str
    mtype: str = "semantic"
    age_days: float = 0.0
    entities: list[tuple[str, str]] = field(default_factory=list)
    supersedes: str | None = None  # key of the item this one replaces
    deleted: bool = False


@dataclass
class Query:
    text: str
    category: str
    gold: list[str] = field(default_factory=list)  # any of these counts as a hit
    forbidden: list[str] = field(default_factory=list)  # must never be returned
    gold_all: bool = False  # entity queries: recall is the fraction of gold found


# (memory, paraphrased question) - the question shares almost no content words with the memory.
PARAPHRASE = [
    ("Prefers meetings after 14:00", "when in the day do I like to have calls with people"),
    ("Allergic to penicillin", "which antibiotics can't I take"),
    ("Flight to Delhi booked for 12 November, seat 14A", "when am I travelling to the capital"),
    ("Wife's birthday is on 2 March", "when should I buy a gift for my spouse"),
    ("Uses VS Code with the Vim extension", "which editor do I code in"),
    ("Dentist appointment on 3 November at 10:30", "when do I need to see the tooth doctor"),
    ("Passport expires in March 2027", "when do I have to renew my travel document"),
    ("Vegetarian, no eggs", "what food restrictions do I have"),
    ("Monthly rent of 18000 is due on the 5th", "when do I pay for my flat"),
    ("Team standup is at 09:30 every weekday", "what time is the daily sync with my team"),
    ("Car service is due at 40000 km", "when does my vehicle need maintenance"),
    ("Prefers dark mode in all editors", "do I like a light or dark theme"),
    ("Sister Meera lives in Bangalore", "where does my sibling live"),
    ("Gym membership renews on 1 December", "when does my fitness club subscription expire"),
    ("Learning Rust on weekends", "what programming language am I studying"),
    ("Blood group is B positive", "what is my blood type"),
    ("Favourite coffee is a flat white", "what do I usually order at a cafe"),
    ("Home loan EMI is auto-debited on the 10th", "when is my mortgage payment taken"),
    ("Laptop warranty ends in June 2027", "until when is my computer covered by a guarantee"),
    ("Does not like phone calls before 10am", "what time can people ring me"),
    (
        "The payments service standard is Python 3.12",
        "which interpreter version does that project need",
    ),
    ("Reads on the Kindle before bed", "what do I do to wind down at night"),
    ("Daughter's school pickup is at 15:45", "when do I collect my child"),
    ("Income tax filing deadline is 31 July", "when are my returns due"),
    ("Favourite restaurant is Spice Route on MG Road", "where do I like to eat out"),
    ("Prefers aisle seats on flights", "where do I like to sit on planes"),
    ("Uses Linux on the office desktop", "which operating system is on my work computer"),
    ("Meditates for 10 minutes at 6:30 am", "what is my morning routine"),
    ("The cat is called Mochi", "what is my pet's name"),
    (
        "Reviews with Ravi happen every first Monday",
        "when do I meet my manager for performance talks",
    ),
    ("Wants to keep Fridays free of meetings", "which day should I keep clear of calls"),
    ("Mild allergy to peanuts", "which nuts should I stay away from"),
    ("Bike helmet size is L", "what size headgear do I wear when cycling"),
    (
        "Anniversary dinner booked at the Taj on 14 February",
        "where are we celebrating our wedding day",
    ),
    ("Prefers email over WhatsApp for work", "how should colleagues get in touch with me"),
    ("Mobile plan renews on the 22nd", "when does my phone recharge expire"),
    ("Doctor recommends 8 hours of sleep", "how much rest should I get"),
    ("Photo backup runs every Sunday", "how often are my pictures saved"),
    ("Keeps the car insurance papers in the blue folder", "where are my vehicle cover documents"),
]

NAMES = [
    "Priya", "Ravi", "Meera", "Arjun", "Kavya", "Sandeep", "Nisha", "Vikram",
    "Anjali", "Rohan", "Divya", "Karthik", "Pooja", "Imran", "Sneha",
]  # fmt: skip
TEAMS = ["platform", "payments", "growth", "design", "data", "security", "mobile", "support"]

TEMPORAL_TOPICS = [
    ("budget review", ["agreed to cut cloud spend", "postponed the hiring freeze", "approved the audit fee"]),
    ("sprint retro", ["wanted smaller pull requests", "flagged flaky tests", "praised the on-call rota"]),
    ("design review", ["rejected the dense table layout", "liked the empty states", "asked for dark mode"]),
    ("vendor call", ["negotiated a 10 percent discount", "delayed delivery by two weeks", "renewed the support plan"]),
    ("release planning", ["froze the branch on Thursday", "added a rollback checklist", "moved launch to Monday"]),
    ("security audit", ["found an exposed staging bucket", "required key rotation", "passed with minor notes"]),
    ("customer demo", ["went well except the export", "needs a shorter walkthrough", "got a request for SSO"]),
    ("hiring sync", ["shortlisted three backend candidates", "paused the intern programme", "added a design role"]),
    ("onboarding session", ["covered the deploy pipeline", "skipped the access requests", "needs a buddy assigned"]),
    ("incident review", ["traced the outage to a bad config", "added a canary stage", "assigned follow-ups to Ravi"]),
]  # fmt: skip

# (old fact, new fact, question about the current state)
CONTRADICTIONS = [
    ("Lives in Pune", "Moved to Bengaluru and lives there now", "which city do I live in"),
    ("Works as a backend engineer", "Promoted to staff engineer", "what is my job title"),
    (
        "Prefers meetings in the morning",
        "Prefers meetings after 15:00 now",
        "when do I like meetings",
    ),
    ("Gym day is Tuesday", "Switched gym day to Thursday", "which day do I go to the gym"),
    ("Uses Sublime Text", "Switched to Neovim", "what text editor do I use"),
    ("Drinks black tea", "Switched to green tea", "what tea do I drink"),
    ("Drives a Honda City", "Bought a Hyundai Creta", "what car do I drive"),
    ("Reports to Anil", "Now reports to Sunita", "who is my manager"),
    (
        "Sprint length is two weeks",
        "Sprint length changed to three weeks",
        "how long are our sprints",
    ),
    ("Lunch at 12:30", "Lunch moved to 13:30", "what time do I have lunch"),
    ("Commutes by bus", "Commutes by metro now", "how do I get to work"),
    ("Favourite language is Java", "Favourite language is Go", "which language do I like best"),
    ("Weekly review is on Friday", "Weekly review moved to Monday", "when is my weekly review"),
    ("Phone is a Pixel 6", "Phone is an iPhone 15", "which phone do I own"),
    ("Sleeps at 23:30", "Sleeps at 22:30 now", "when do I go to bed"),
]

DELETED = [
    ("Garage door code is hunter-blue", "what is the garage door code"),
    ("Uncle Sanjay owes me 5000 rupees", "who owes me money"),
    ("Surprise party for Meera planned at Cafe Zing", "what surprise did I plan for my sister"),
    ("Applying for a job at Orbitron Labs", "which company am I applying to"),
    ("Therapy session every Wednesday evening", "what do I do on Wednesday evenings"),
    ("Salary expectation is 42 lakhs", "how much salary do I expect"),
    ("Wi-Fi password for the guest network is mango-77", "what is the guest wifi password"),
    ("Secret gift: a telescope for Arjun", "what gift did I buy for Arjun"),
    ("Visa interview scheduled at the US consulate on 9 Dec", "when is my visa appointment"),
    ("Ex-landlord deposit dispute of 30000", "what was the deposit argument about"),
]

NO_ANSWER = [
    "what is my favourite dinosaur",
    "capital of Mongolia",
    "recipe for lasagna",
    "which football club do I support",
    "how many moons does Jupiter have",
    "what is my shoe size on Mars",
    "who won the cricket world cup in 1983",
    "my pottery class schedule",
    "what is the boiling point of tungsten",
    "name of my childhood goldfish",
    "which opera did I see in Vienna",
    "how do I knit a scarf",
    "best time to plant tomatoes",
    "what is my skydiving licence number",
    "translate hello into Swahili",
]

SHOPS = [
    "Reliance Digital",
    "Croma",
    "Big Bazaar",
    "Decathlon",
    "IKEA",
    "Amazon",
    "Flipkart",
    "Ratnadeep",
]
THINGS = ["a mixer grinder", "running shoes", "a desk lamp", "a yoga mat", "USB cables", "a water bottle",
          "a backpack", "noise cancelling headphones", "a monitor stand", "a kettle", "bedsheets", "a tripod"]  # fmt: skip
FILMS = [
    "Dune",
    "Interstellar",
    "Oppenheimer",
    "Inception",
    "Parasite",
    "Arrival",
    "Tenet",
    "Barbie",
]
PLACES = ["Goa", "Jaipur", "Udaipur", "Kochi", "Mysore", "Manali", "Hampi", "Pondicherry"]
TOPICS = ["the logo", "vendor contracts", "the Q3 roadmap", "travel policy", "the offsite", "tooling costs",
          "the migration", "interview loops", "the style guide", "capacity planning"]  # fmt: skip
BOOKS = ["Deep Work", "Atomic Habits", "The Pragmatic Programmer", "Sapiens", "Dune", "Educated"]
SERVICES = [
    "Netflix",
    "Spotify",
    "iCloud",
    "the gym",
    "the library",
    "a cloud server",
    "the newspaper",
]


def _filler(rng: random.Random, n: int) -> list[Item]:
    makers = [
        lambda: (
            f"Bought {rng.choice(THINGS)} from {rng.choice(SHOPS)} for {rng.randint(3, 90) * 100} rupees"
        ),
        lambda: f"Watched {rng.choice(FILMS)} with {rng.choice(NAMES)} on a Saturday night",
        lambda: f"Met {rng.choice(NAMES)} to talk about {rng.choice(TOPICS)}",
        lambda: f"Booked a weekend trip to {rng.choice(PLACES)} with {rng.choice(NAMES)}",
        lambda: f"Finished reading {rng.choice(BOOKS)} and lent it to {rng.choice(NAMES)}",
        lambda: f"Paid the {rng.choice(SERVICES)} invoice of {rng.randint(2, 40) * 50} rupees",
        lambda: (
            f"Ordered {rng.choice(THINGS)} online, delivery expected in {rng.randint(2, 9)} days"
        ),
        lambda: (
            f"Discussed {rng.choice(TOPICS)} with the {rng.choice(TEAMS)} team and noted two follow-ups"
        ),
        lambda: f"Cancelled the {rng.choice(SERVICES)} trial before it converted",
        lambda: f"Walked {rng.randint(4, 14)} km around {rng.choice(PLACES)} and took photos",
    ]
    items, seen = [], set()
    while len(items) < n:
        text = rng.choice(makers)()
        if text in seen:
            continue
        seen.add(text)
        items.append(Item(f"fill{len(items)}", text, "episodic", age_days=rng.uniform(3, 120)))
    return items


def build(seed: int = 7) -> tuple[list[Item], list[Query]]:
    rng = random.Random(seed)
    items: list[Item] = []
    queries: list[Query] = []

    for i, (text, question) in enumerate(PARAPHRASE):
        items.append(Item(f"para{i}", text, "preference", age_days=rng.uniform(5, 90)))
        queries.append(Query(question, "paraphrase", gold=[f"para{i}"]))

    # exact identifiers: tickets, ports, error codes, commit hashes
    issues = ["duplicate refund bug", "timeout in the export job", "flaky login test", "slow search page",
              "missing audit log entry", "wrong currency rounding"]  # fmt: skip
    for i in range(6):
        ticket = f"PAY-{rng.randint(1000, 9999)}"
        items.append(
            Item(
                f"exact_t{i}",
                f"Ticket {ticket}: {issues[i]}, assigned to {rng.choice(NAMES)}",
                "project",
                rng.uniform(1, 40),
            )
        )
        queries.append(Query(ticket, "exact", gold=[f"exact_t{i}"]))
    for i in range(5):
        port, host = rng.randint(3000, 9999), f"svc-{rng.choice(TEAMS)}-{rng.randint(10, 99)}"
        items.append(
            Item(
                f"exact_p{i}", f"Host {host} listens on port {port}", "project", rng.uniform(1, 40)
            )
        )
        queries.append(Query(f"port {port}", "exact", gold=[f"exact_p{i}"]))
    for i in range(5):
        code = f"0x8007{rng.randint(1000, 9999):04X}"
        items.append(
            Item(
                f"exact_e{i}",
                f"Error {code} raised by the {rng.choice(TEAMS)} installer",
                "project",
                rng.uniform(1, 40),
            )
        )
        queries.append(Query(code, "exact", gold=[f"exact_e{i}"]))
    for i in range(4):
        sha = "".join(rng.choice("0123456789abcdef") for _ in range(7))
        items.append(
            Item(
                f"exact_c{i}",
                f"Release v{rng.randint(1, 4)}.{rng.randint(0, 9)}.{rng.randint(0, 9)} deployed from commit {sha}",
                "project",
                rng.uniform(1, 40),
            )
        )
        queries.append(Query(sha, "exact", gold=[f"exact_c{i}"]))

    # entities: two memories per person; ask about the person, and about one specific fact
    for i, name in enumerate(NAMES):
        team, start = TEAMS[i % len(TEAMS)], rng.randint(1, 20)
        ent = [(name, "person")]
        items.append(
            Item(
                f"ent{i}a",
                f"{name} leads the {team} team and prefers async updates",
                "semantic",
                rng.uniform(5, 60),
                ent,
            )
        )
        items.append(
            Item(
                f"ent{i}b",
                f"{name} is on leave from {start} to {start + 4} next month",
                "episodic",
                rng.uniform(1, 10),
                ent,
            )
        )
        queries.append(
            Query(
                f"what do I know about {name}",
                "entity",
                gold=[f"ent{i}a", f"ent{i}b"],
                gold_all=True,
            )
        )

    # temporal: three similar notes per topic at different ages; the query says "yesterday"
    for i, (topic, outcomes) in enumerate(TEMPORAL_TOPICS):
        for j, (outcome, age) in enumerate(zip(outcomes, (1, 15, 70), strict=True)):
            items.append(Item(f"temp{i}_{j}", f"{topic.capitalize()}: {outcome}", "episodic", age))
        queries.append(
            Query(f"what happened in the {topic} yesterday", "temporal", gold=[f"temp{i}_0"])
        )

    # contradictions: only the newest fact may come back
    for i, (old, new, question) in enumerate(CONTRADICTIONS):
        items.append(Item(f"old{i}", old, "preference", age_days=rng.uniform(60, 120)))
        items.append(
            Item(f"new{i}", new, "preference", age_days=rng.uniform(1, 20), supersedes=f"old{i}")
        )
        queries.append(Query(question, "contradicted", gold=[f"new{i}"], forbidden=[f"old{i}"]))

    # deletions: added, then hard-deleted; nothing about them may come back
    for i, (text, question) in enumerate(DELETED):
        items.append(Item(f"del{i}", text, "semantic", age_days=rng.uniform(5, 50), deleted=True))
        queries.append(Query(question, "deleted", forbidden=[f"del{i}"]))

    for text in NO_ANSWER:
        queries.append(Query(text, "no_answer"))

    active = sum(
        1 for it in items if not it.deleted and not any(o.supersedes == it.key for o in items)
    )
    items.extend(_filler(rng, 500 - active))
    rng.shuffle(items)
    # supersedes/delete need their target inserted first
    order = {it.key: n for n, it in enumerate(items)}
    items.sort(
        key=lambda it: order.get(it.supersedes, -1) + 0.5 if it.supersedes else order[it.key]
    )
    return items, queries


if __name__ == "__main__":
    its, qs = build()
    print(len(its), "items,", len(qs), "queries")
    for cat in sorted({q.category for q in qs}):
        print(f"  {cat}: {sum(q.category == cat for q in qs)}")
