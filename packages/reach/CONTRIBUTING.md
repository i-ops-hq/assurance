# Contributing

## The rules that are not negotiable

**Read a graph; never build one, and never add an edge.** This package interprets what a producer
wrote. A dependency it inferred itself would be a prediction, and it would be indistinguishable in the
output from one the code shows. If a producer's shape needs reading differently, change the reader,
pinned to a real file that producer wrote, in `tests/fixtures`.

**Confidence is never mixed.** A symbol is reached by extracted edges only when extracted edges alone
reach it. Anything that changes the walk keeps that true, and the tests say so.

**Say what could not be determined, at the same weight.** Staleness is reported first, every time, and
what was not followed is reported last, every time. A report that reads complete when it is not is the
one defect this package exists not to have.

## Setup

```
pip install -e ".[dev]"
python -m pytest
python -m mypy --strict assurance_reach
```
