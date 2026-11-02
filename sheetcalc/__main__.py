"""Calculate a CSV of values and formulas: python -m sheetcalc input.csv [output.csv]"""
import sys

from . import Sheet


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    with open(argv[1], encoding="utf-8", newline="") as f:
        sheet = Sheet.from_csv(f.read())
    result = sheet.to_csv()
    if len(argv) > 2:
        with open(argv[2], "w", encoding="utf-8", newline="") as f:
            f.write(result)
    else:
        sys.stdout.write(result)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
