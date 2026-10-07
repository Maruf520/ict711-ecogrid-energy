# EcoGrid Energy — prototype

ICT711 Advanced Software Engineering, Assessment 2 — Group 5
Md Maruf, Mohammad Riham Hossain, Md Salem Ahmed

Small Python prototype of our EcoGrid design. It has been divided into three enclosed contexts (Metering, Marketplace, Settlement) which communicate only by events, and It has a set of fitness functions that are executed per push on CI and it notifies us if this is broken. architecture starts drifting.  All this functionality is packaged in a single process with an in-memory event bus as the Kafka equivalent. The idea Was to demonstrate the design end-to-end without standing up real infrastructure.

## What's where


| File                              | Bounded context                                    | Owner                  |
| --------------------------------- | -------------------------------------------------- | ---------------------- |
| `src/ecogrid/metering.py`         | Metering (smart meter integration)                 | Md Maruf               |
| `src/ecogrid/marketplace.py`      | Marketplace (core domain)                          | Mohammad Riham Hossain |
| `src/ecogrid/settlement.py`       | Settlement (financial settlement)                  | Md Salem Ahmed         |
| `src/ecogrid/events.py`           | Shared kernel: event, event bus, 5-minute interval | All                    |
| `src/ecogrid/app.py`              | Wires the contexts together                        | All                    |
| `contracts/events.json`           | Event contracts (published language)               | All                    |
| `tests/test_fitness_functions.py` | Fitness functions                                  | All                    |




## Running it

```bash
python -m venv .venv
source .venv/bin/activate          
pip install -e . -r requirements-dev.txt

python -m ecogrid.demo              
pytest -v -s                        
ruff check .                        
bandit -r src -ll                   
```

The demo deliberately feeds in some bad data (a duplicate, a garbage message, an
impossible spike, a buyer with no money and a cloudy seller) so you can see how each
context deals with it. The last line should always say the ledger total is 0.

## Fitness functions


| ID    | Quality      | Check                                                              |
| ----- | ------------ | ------------------------------------------------------------------ |
| FF-01 | Evolvable    | Bounded contexts never import each other                           |
| FF-02 | Evolvable    | Every event matches its contract and has exactly one owner         |
| FF-03 | Maintainable | Cyclomatic complexity ≤ 10 per function (ruff)                     |
| FF-04 | Maintainable | Test coverage ≥ 85%                                                |
| FF-05 | Fast         | Ingestion handles ≥ 10,000 readings per second                     |
| FF-06 | Fast         | 99% of orders matched in ≤ 10 ms                                   |
| FF-07 | Secure       | Money is never created, lost or overdrawn (200 random scenarios)   |
| FF-08 | Secure       | Repeated or replayed events never pay out twice                    |
| FF-09 | Secure       | No medium+ Bandit issues, no known-vulnerable packages (pip-audit) |


All of these run in `.github/workflows/ci.yml` on every push and pull request.


## How we work

Everyone works on their own branch and opens a pull request, then asks one of the
others to review it. `main` is protected, so a PR needs one approval and a green CI run
before it can be merged.
