# SheetCalc

The calculation engine behind a spreadsheet, written from scratch in pure Python. Use it as a library to give an application live formulas, or calculate CSV files from the command line.

```python
from sheetcalc import Sheet

s = Sheet()
s.set("A1", 1200)
s.set("A2", 800)
s.set("A3", "=SUM(A1:A2)")
s.set("B1", '=IF(A3>1500,"over budget","ok")')
print(s.get("A3"), s.get("B1"))     # 2000 over budget

s.set("A2", 100)                    # only the cells that depend on A2 recalculate
print(s.get("A3"), s.get("B1"))     # 1300 ok
```

```
python -m sheetcalc budget.csv result.csv
```

## Features

- A full formula language: arithmetic, `^`, `%`, `&`, comparisons, ranges and absolute references such as `$A$1`
- More than 50 functions, including `SUM`, `AVERAGE`, `IF`, `IFERROR`, `SUMIF`, `COUNTIF`, `VLOOKUP`, `INDEX`, `MATCH`, `ROUND`, `MEDIAN`, `STDEV`, text and date functions
- Spreadsheet error values: `#DIV/0!`, `#VALUE!`, `#NAME?`, `#REF!`, `#N/A`, `#NUM!` and `#CYCLE!`
- Copying a cell adjusts its relative references, as a paste does
- Named cells, plus CSV and JSON files

## How recalculation works

Every formula records the cells it reads, which forms a dependency graph. When a cell changes, the engine walks forward to find everything affected, sorts those cells so each one runs after its inputs, and evaluates each one once. Anything that cannot be sorted is part of a cycle, or depends on one, and shows `#CYCLE!`. This uses a queue, not recursion, so a chain of thousands of cells works.

## How it is tested

A random test makes 1,500 edits, then rebuilds the sheet from nothing and requires every cell to match, which proves that incremental recalculation never goes stale. Other tests count evaluations to prove that unrelated cells are left alone.

## Limits

This is an engine, not an application: it has no grid on screen, no charts, and only one sheet at a time. `VLOOKUP` with approximate matching expects sorted numbers. Some functions differ from Excel in edge cases.

## Tests

```
python -m unittest discover -s tests
```

MIT licence.
