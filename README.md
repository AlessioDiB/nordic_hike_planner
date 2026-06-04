# Nordic Hike Planner

[![CI](https://github.com/AlessioDiB/nordic_hike_planner/actions/workflows/ci.yml/badge.svg)](https://github.com/AlessioDiB/nordic_hike_planner/actions/workflows/ci.yml)
> A multi-day hut-to-hut trip planner for the Norwegian and Swedish mountains.
> Tell it where you're starting and how many days you have. It picks the best
> path through the Hardangervidda plateau, using A\* graph search and a hiking
> rule from 1892. Yes, 1892. Walking hasn't changed much.

---

## Why this exists

The brief for this assignment was, in full: *"Have fun."*

That's it. No spec, no problem statement, no "build a banking thing." Just
have fun.

I'd been planning a Nordic hut traverse for about three years. The map is on
my wall. The trip keeps slipping because life happens and weather happens and
nobody's ever planned a trip without saying "let's do it next year." So when
"have fun" arrived in my inbox, I took it as cosmic permission to finally
build the tool I keep wishing existed.

The result is this: give it a starting hut and a number of days, and it works
out a sensible route through Hardangervidda — Norway's largest mountain
plateau and the spiritual home of the DNT hut network. I hand-curated a
dataset of 12 real huts in the region (yes, the coordinates are real; yes,
the trails are real; no, you should not actually use this for real trip
planning without checking weather and hut availability — see the
*Limitations* section if you enjoy being disappointed).

Due to the current pandemic of vibecoders believing to be
the next reincarnation of Bill Gates and Steve Jobs, a mention of how and
where AI has been used, is due. This will allow the project to be fully
transparent and honest.
The followings have been created and/or heavily supported by use of AI:
- Creation of testing Python files, in tests folder.
- The acquisition of real hiking data from databases. More specifically, from open source Norway-based databases.
- Addition of code comments. Due to tight deadlines, an LLM has been asked to analyze the code I created.
- Grammar checks and spelling correction of this README file.
- Debugging few but very stubborns and difficult issues.

---

## What it does

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
and the API share exactly the same implementation. (Which is good. Two
implementations of the same logic is one of the few mistakes worse than
*no* implementation.)

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

To run lint, type-checks, and tests in one go:

```bash
# On Linux / macOS:
make check

# On Windows:
python check.py
```

Docker:

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

I used **A\* search** with **great-circle distance as the heuristic**. A\* is
provably optimal when the heuristic never overestimates the true cost (an
*admissible* heuristic), and great-circle distance is admissible here because
no walking route can be shorter than a straight line on the Earth's surface.
In practice this means A\* explores far fewer paths than Dijkstra would,
without sacrificing optimality.

When the user doesn't specify a goal hut, A\* gracefully degrades to Dijkstra
— the heuristic returns 0 and the search bounds itself by trip length.

### Walking time: Naismith's rule

Time estimates come from **Naismith's rule** (William Naismith, 1892):
*allow one hour per 5 km of horizontal distance, plus one hour per 600 m of
ascent*. It's the rule most Nordic guidebooks still use. There are more
sophisticated alternatives (Tobler, Langmuir), but Naismith is what hikers
actually recognise, and the differences are smaller than other sources of
uncertainty in route planning. Also, naming things after Victorian
mountaineers is just the right kind of fun.

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

A non-exhaustive list of things this planner won't do for you, and which
will become abundantly clear if you try to use it for an actual trip:

- **The dataset is hand-curated, not scraped.** I considered scraping DNT's
  website and using OpenStreetMap's Overpass API, but for a one-week
  project the value-per-hour was better spent on the routing logic than
  on data plumbing. The data layer is abstracted behind the
  `HutRepository` interface, so swapping the source later is one new
  class, not a rewrite. (Twelve huts. It's not exactly Wikipedia.)
- **Elevation is symmetric per edge.** Walking A→B and B→A use the same
  ascent value, which is wrong in principle — one direction is mostly up,
  the other mostly down. Naismith only counts ascent, so the practical
  effect is that descents get over-estimated. For Hardangervidda's gentle
  plateau this is fine. For Jotunheimen or the Lyngen Alps, I'd fix it.
- **No hut availability. No weather. No bear warnings.** Real trip planning
  needs all of these. If you arrive at Kjeldebu in January and it's closed
  and snowing, this tool will offer no comfort.
- **One hut per day.** Each day is exactly one edge in the graph. Real
  traversers sometimes combine two short legs into one day; this planner
  doesn't.
- **No web UI.** I considered an HTMX-based interactive view, but judged
  the engineering-per-hour was better spent on the planner, the API, and
  test coverage. The FastAPI service exposes a Swagger UI at `/docs` which
  is enough for a project of this size.
- **Dockerfile not built locally.** No Docker installed on my dev machine
  (Windows; long story; mostly involves WSL2 and a missed Saturday).
  The GitHub Actions CI builds the image on every push and smoke-tests it
  against `/health`, so the build is verified — just not by me, in person,
  with my own eyes.

---

## What I'd do next

With another week or two:

- **Hut availability and seasonal awareness.** Refuse to plan a January
  trip through summer-only huts. Eventually integrate with DNT's booking
  calendar.
- **More regions.** Jotunheimen, Rondane, Sarek. The `HutRepository`
  interface is already there for it.
- **Directional elevation.** Real ascent per direction, with Langmuir's
  correction for steep descent.
- **A small HTMX front-end.** Server-rendered, no SPA. The JD called this
  out and I'd genuinely like to see what it feels like.
- **GeoJSON export** so the plan can be opened in any map viewer. This is
  the actual feature I'd build first.

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

Thanks for reading this far. If you'd like to talk it through, dig into any
of the decisions, or just argue about whether Naismith's rule is too
optimistic for unfit Italians (it is), I'd love to chat.

— Alessio