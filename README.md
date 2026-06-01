# Nordic Hike Planner

[![CI](https://github.com/AlessioDiB/nordic_hike_planner/actions/workflows/ci.yml/badge.svg)](https://github.com/AlessioDiB/nordic_hike_planner/actions/workflows/ci.yml)
> A multi-day hut-to-hut trip planner for the Norwegian and Swedish mountains.
> Given a starting hut and a number of days, it works out the best path through
> the Hardangervidda plateau using A\* graph search and Naismith's rule for
> walking-time estimates.

---

## Why this exists

I've been planning to do a Nordic hut traverse for years. The map is on my wall;
the trip keeps slipping. When this came up as a "have fun" assignment, I
took it as an excuse to build the planning tool I keep wishing existed —
something that takes a starting point, a number of days, and produces a
sensible plan over real terrain.

Hardangervidda is Norway's largest mountain plateau and the heart of the DNT
hut network. I curated a small dataset of 12 huts in the region and built
the planner around it.

---

## What it does

A small command-line tool and HTTP API for planning multi-day hiking trips:

```text
$ nordic-hike --start finse --days 5 --goal haukeliseter

                                       Trip plan
┏━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━┓
┃ Day ┃ From        ┃ To           ┃ Distance ┃ Ascent ┃ Est. time ┃
┡━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━┩
│   1 │ Finse 1222  │ Kjeldebu     │  19.0 km │  250 m │     4.2 h │
│   2 │ Kjeldebu    │ Sandhaug     │  15.0 km │  350 m │     3.6 h │
│   3 │ Sandhaug    │ Litlos       │  22.0 km │  380 m │     5.0 h │
│   4 │ Litlos      │ Hellevassbu  │  14.0 km │  290 m │     3.3 h │
│   5 │ Hellevassbu │ Haukeliseter │  17.0 km │  180 m │     3.7 h │
└─────┴─────────────┴──────────────┴──────────┴────────┴───────────┘
╭─ Summary ──────────────────────────────────────────────────────────╮
│ Total distance: 87.0 km   Total ascent: 1450 m   Total walking ... │
╰────────────────────────────────────────────────────────────────────╯
```

The same planner is also exposed as an HTTP service via FastAPI, so the CLI
and the API share exactly the same implementation.
---

## How to run it

```bash
# Install
pip install -e ".[dev]"

# Run the CLI
nordic-hike --start finse --days 5 --goal haukeliseter

# Or run the HTTP API
uvicorn nordic_hike_planner.api:app --port 8000
# Then open http://localhost:8000/docs for the interactive Swagger UI
```

To run the test suite, lint, type-checks, and pytest in one go:

```bash
# On Linux / macOS:
make check

# On Windows (or anywhere Python is installed):
python check.py
```

The Docker image can be built with:

```bash
docker build -t nordic-hike-planner .
docker run --rm -p 8000:8000 nordic-hike-planner
```

---

## How it works

### The algorithm

Trip planning is a graph search problem. Each hut is a node; each marked trail
between two huts is an edge weighted by distance and elevation gain. Finding
a good multi-day trip is the same as finding a low-cost path of the right
length through this graph.

I used **A\* search** with **great-circle distance as the heuristic**.
A\* is provably optimal when the heuristic never overestimates the true cost
(an *admissible* heuristic), and great-circle distance is admissible here
because no walking route can be shorter than a straight line on the Earth's
surface. In practice this means A\* explores far fewer paths than Dijkstra
would, without sacrificing optimality.

When the user doesn't specify a goal hut, A\* gracefully degrades to Dijkstra —
the heuristic returns 0 and the search bounds itself by trip length.

### Walking time: Naismith's rule

Time estimates come from **Naismith's rule** (William Naismith, 1892):
*allow one hour per 5 km of horizontal distance, plus one hour per 600 m of
ascent*. It's the rule most Nordic guidebooks still use. There are more
sophisticated alternatives (Tobler, Langmuir), but Naismith is what
hikers actually recognise, and the differences are smaller than other
sources of uncertainty in route planning.

### Cost function

The planner minimises a weighted combination of distance and elevation gain:

```text
cost = distance_km + elevation_weight × elevation_gain_m / 1000
```

The default weight is 6 — meaning 1000 m of climbing is treated as roughly
equivalent to 6 km of flat walking, close to Naismith's own ratio. Users can
tune this via the `--elevation-weight` flag if they prefer easier or steeper
routes.

There's also a soft, quadratic penalty for deviating from the target daily
distance, so the planner prefers days near `target_km_per_day` without
forbidding deviation.

---

## Project structure

```
src/nordic_hike_planner/
├── models.py        # Pydantic domain models (Hut, Edge, DayPlan, Trip)
├── repository.py    # Data loading; graph construction
├── scoring.py       # Naismith's rule and edge-cost function
├── planner.py       # A* search
├── api.py           # FastAPI service
└── cli.py           # Typer-based command-line client

tests/               # pytest + Hypothesis property tests (70 tests)
data/                # Hand-curated Hardangervidda dataset (12 huts)
```

A few design choices worth flagging:

- **Repository pattern.** The planner depends on a `HutRepository` protocol,
  not on the JSON loader directly. Swapping in an OpenStreetMap source or
  a database would be a new class, not a change to the planner.
- **Pure scoring module.** Naismith's rule and the cost function are pure
  functions, separated from the search algorithm. *What we optimise for*
  and *how we search* are independent concerns.
- **Immutable models.** All domain models are frozen Pydantic objects.
  This makes them hashable (useful for the graph search), trivially
  comparable, and free of an entire class of mutation bugs.
- **Validate at the boundary, trust inside.** Pydantic validates everything
  at construction time. Once a `Trip` exists, every consumer can trust it
  represents a real, continuous, sensibly-numbered plan.

---

## Limitations and simplifications

A few things I deliberately did *not* do, and why:

- **Hand-curated dataset, not scraped or OSM-derived.** I considered scraping
  DNT's website and using OpenStreetMap's Overpass API, but for a one-week
  project the value-per-hour was better spent on the routing logic and the
  engineering hygiene than on data plumbing. The data layer is abstracted
  behind the `HutRepository` interface, so swapping the source later is
  straightforward.
- **Elevation gain modelled as symmetric per edge.** Walking A→B and B→A
  are modelled with the same elevation gain. This is wrong in principle —
  one direction is mostly ascent, the other mostly descent. But Naismith's
  rule only counts ascent, so the practical impact is that we overestimate
  walking time on descent legs. For Hardangervidda's gentle plateau terrain
  this is acceptable; for steeper regions (Jotunheimen, the Lyngen Alps)
  I'd model directional ascent properly.
- **No hut availability, no weather.** Real trip planning needs to check
  whether huts are open in the chosen month and whether the weather is
  walkable. I've left both as future work.
- **One hut per day.** Each day is one edge in the graph. Some real
  traverses combine multiple short legs into one day — the model doesn't
  currently support that.
- **No web UI.** I considered an HTMX-based interactive view, but judged
  the engineering-per-hour was better spent on the planner, the API, and
  test coverage. The FastAPI service exposes a Swagger UI at `/docs` which
  is enough for a portfolio piece.
- **Dockerfile not test-built locally.** I don't have Docker installed on
  my dev machine (Windows). The GitHub Actions CI builds the image on
  every push and smoke-tests it against `/health`, so the build is
  verified — just not by me, locally.

---

## What I'd do next

With another week or two:

- **Hut availability and seasonal awareness** — refuse to plan a January
  trip through summer-only huts, integrate with DNT's booking calendar.
- **Multi-region datasets** — Jotunheimen, Rondane, Sarek (Sweden) using
  a shared `HutRepository` interface. The infrastructure is already there.
- **Directional elevation** — separate ascent for each direction of an
  edge, with Langmuir's correction for steep descent.
- **A small HTMX front-end** — server-rendered, no SPA. The JD called this
  out and I'd like to see what it feels like in practice.
- **A simple GeoJSON export** so the plan can be loaded into any map
  viewer.

---

## Tech stack

- Python 3.11+
- [FastAPI](https://fastapi.tiangolo.com/) for the HTTP service
- [Typer](https://typer.tiangolo.com/) + [rich](https://rich.readthedocs.io/) for the CLI
- [Pydantic](https://docs.pydantic.dev/) for models and validation
- [pytest](https://docs.pytest.org/) + [Hypothesis](https://hypothesis.readthedocs.io/) for tests
- [ruff](https://docs.astral.sh/ruff/) and [mypy](http://mypy-lang.org/) for lint and type-checking
- Docker for containerisation, GitHub Actions for CI

---

Thanks for reading this far. If you'd like to talk it through or have
questions about any of the decisions, I'd love to chat.

— Alessio