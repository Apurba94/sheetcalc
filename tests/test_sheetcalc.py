import random
import unittest

from sheetcalc import FUNCTIONS, CellError, Sheet, cell_name, parse_cell
from sheetcalc.formula import LAZY, col_letters, col_number, shift_formula


class Base(unittest.TestCase):
    def setUp(self):
        self.s = Sheet()

    def calc(self, formula):
        self.s.set("Z99", "=" + formula)
        return self.s.get("Z99")

    def err(self, formula, code):
        self.assertEqual(self.calc(formula), CellError(code), formula)


class TestNames(unittest.TestCase):
    def test_columns_round_trip(self):
        for n in (1, 26, 27, 52, 53, 702, 703, 18278):
            self.assertEqual(col_number(col_letters(n)), n)
        self.assertEqual(col_letters(27), "AA")
        self.assertEqual(parse_cell("AB12"), (12, 28))
        self.assertEqual(cell_name(12, 28), "AB12")


class TestFormulas(Base):
    def test_precedence(self):
        self.assertEqual(self.calc("1+2*3"), 7)
        self.assertEqual(self.calc("(1+2)*3"), 9)
        self.assertEqual(self.calc("2^3^2"), 64)
        self.assertEqual(self.calc("-2^2"), 4)
        self.assertEqual(self.calc("50%*4"), 2)
        self.assertEqual(self.calc('"a"&"b"&1'), "ab1")
        self.assertEqual(self.calc("1+1=2"), True)
        self.assertEqual(self.calc('"A"="a"'), True)

    def test_references(self):
        self.s.set("A1", 2)
        self.s.set("A2", "=A1*10")
        self.s.set("A3", "=A2+$A$1")
        self.assertEqual(self.s.get("A3"), 22)

    def test_ranges(self):
        for i in range(1, 6):
            self.s.set(f"A{i}", i)
        self.s.set("A6", "text")
        self.assertEqual(self.calc("SUM(A1:A6)"), 15)
        self.assertEqual(self.calc("AVERAGE(A1:A5)"), 3)
        self.assertEqual(self.calc("COUNT(A1:A7)"), 5)
        self.assertEqual(self.calc("COUNTA(A1:A7)"), 6)
        self.assertEqual(self.calc("MAX(A1:A5)-MIN(A1:A5)"), 4)
        self.assertEqual(self.calc("MEDIAN(A1:A5)"), 3)

    def test_blanks_act_as_zero(self):
        self.assertEqual(self.calc("Q1+1"), 1)
        self.assertEqual(self.calc('Q1&"x"'), "x")

    def test_logic(self):
        self.assertEqual(self.calc('IF(1>2,"y","n")'), "n")
        self.assertEqual(self.calc("IF(1<2,1)"), 1)
        self.assertEqual(self.calc("AND(TRUE,1>0)"), True)
        self.assertEqual(self.calc("OR(FALSE,1>2)"), False)
        self.assertEqual(self.calc("NOT(FALSE)"), True)

    def test_if_only_evaluates_the_chosen_branch(self):
        self.assertEqual(self.calc("IF(TRUE,1,1/0)"), 1)
        self.assertEqual(self.calc("IFERROR(1/0,7)"), 7)

    def test_text_functions(self):
        self.assertEqual(self.calc('LEFT("hello",2)&RIGHT("hello",2)&MID("hello",2,3)'), "heloell")
        self.assertEqual(self.calc('LEN("abc")'), 3)
        self.assertEqual(self.calc('TRIM("  a   b ")'), "a b")
        self.assertEqual(self.calc('SUBSTITUTE("aXb","X","-")'), "a-b")
        self.assertEqual(self.calc('FIND("l","hello")'), 3)

    def test_math_and_dates(self):
        self.assertEqual(self.calc("ROUND(2.567,2)"), 2.57)
        self.assertEqual(self.calc("ROUND(-2.5,0)"), -3)
        self.assertEqual(self.calc("MOD(7,3)"), 1)
        self.assertEqual(self.calc("INT(-1.5)"), -2)
        self.assertEqual(self.calc("YEAR(DATE(2026,11,2))"), 2026)
        self.assertEqual(self.calc("DATE(2026,3,1)-DATE(2026,2,1)"), 28)
        self.assertEqual(self.calc("DATE(1900,1,1)"), 2)

    def test_conditional_aggregates(self):
        rows = [("rice", 10), ("oil", 5), ("rice", 20), ("salt", 1)]
        for i, (a, b) in enumerate(rows, 1):
            self.s.set(f"A{i}", a)
            self.s.set(f"B{i}", b)
        self.assertEqual(self.calc('SUMIF(A1:A4,"rice",B1:B4)'), 30)
        self.assertEqual(self.calc('COUNTIF(B1:B4,">4")'), 3)
        self.assertEqual(self.calc('AVERAGEIF(A1:A4,"rice",B1:B4)'), 15)
        self.assertEqual(self.calc('COUNTIF(A1:A4,"<>rice")'), 2)

    def test_lookups(self):
        for i, (a, b) in enumerate([(10, "low"), (20, "mid"), (30, "high")], 1):
            self.s.set(f"A{i}", a)
            self.s.set(f"B{i}", b)
        self.assertEqual(self.calc("VLOOKUP(20,A1:B3,2)"), "mid")
        self.assertEqual(self.calc("VLOOKUP(25,A1:B3,2,TRUE)"), "mid")
        self.err("VLOOKUP(25,A1:B3,2)", "#N/A")
        self.err("VLOOKUP(20,A1:B3,3)", "#REF!")
        self.assertEqual(self.calc("MATCH(30,A1:A3)"), 3)
        self.assertEqual(self.calc("INDEX(B1:B3,2)"), "mid")
        self.assertEqual(self.calc("INDEX(A1:B3,3,2)"), "high")
        self.err("INDEX(A1:B3,9,1)", "#REF!")

    def test_at_least_forty_functions(self):
        self.assertGreaterEqual(len(FUNCTIONS) + len(LAZY), 40)


class TestErrors(Base):
    def test_values(self):
        self.err("1/0", "#DIV/0!")
        self.err('"a"+1', "#VALUE!")
        self.err("NOSUCH(1)", "#NAME?")
        self.err("unknownname", "#NAME?")
        self.err("SQRT(-1)", "#NUM!")
        self.err("AVERAGE(Q1:Q5)", "#DIV/0!")

    def test_errors_spread_to_dependents(self):
        self.s.set("A1", "=1/0")
        self.s.set("A2", "=A1+1")
        self.s.set("A3", "=SUM(A1:A2)")
        self.assertEqual(self.s.get("A3"), CellError("#DIV/0!"))
        self.s.set("A1", 5)
        self.assertEqual(self.s.get("A3"), 11)

    def test_iserror_catches(self):
        self.s.set("A1", "=1/0")
        self.assertEqual(self.calc("ISERROR(A1)"), True)
        self.assertEqual(self.calc("IFERROR(A1,0)"), 0)

    def test_bad_syntax_is_an_error_value(self):
        self.s.set("A1", "=1+")
        self.assertEqual(self.s.get("A1"), CellError("#ERROR!"))
        self.s.set("A2", "=A1")
        self.assertEqual(self.s.get("A2"), CellError("#ERROR!"))


class TestGraph(Base):
    def test_direct_cycle(self):
        self.s.set("A1", "=A1+1")
        self.assertEqual(self.s.get("A1"), CellError("#CYCLE!"))

    def test_long_cycle_and_recovery(self):
        self.s.set("A1", "=B1")
        self.s.set("B1", "=C1")
        self.s.set("C1", "=A1")
        self.s.set("D1", "=A1+1")
        self.assertEqual(self.s.get("B1"), CellError("#CYCLE!"))
        self.assertEqual(self.s.get("D1"), CellError("#CYCLE!"))
        self.s.set("C1", 5)
        self.assertEqual(self.s.get("D1"), 6)

    def test_only_affected_cells_recalculate(self):
        items = {f"A{i}": i for i in range(1, 201)}
        items.update({f"B{i}": f"=A{i}*2" for i in range(1, 201)})
        self.s.set_many(items)
        self.s.evaluations = 0
        self.s.set("A7", 100)
        self.assertEqual(self.s.evaluations, 1)
        self.assertEqual(self.s.get("B7"), 200)

    def test_diamond_evaluates_each_cell_once_in_order(self):
        self.s.set_many({"A1": 1, "B1": "=A1+1", "B2": "=A1*2", "C1": "=B1+B2"})
        self.s.evaluations = 0
        self.s.set("A1", 10)
        self.assertEqual(self.s.evaluations, 3)
        self.assertEqual(self.s.get("C1"), 31)

    def test_long_chain_does_not_hit_the_recursion_limit(self):
        n = 5000
        items = {"A1": 1}
        items.update({f"A{i}": f"=A{i - 1}+1" for i in range(2, n + 1)})
        self.s.set_many(items)
        self.assertEqual(self.s.get(f"A{n}"), n)
        self.s.set("A1", 2)
        self.assertEqual(self.s.get(f"A{n}"), n + 1)

    def test_replacing_a_formula_drops_old_links(self):
        self.s.set("A1", 1)
        self.s.set("B1", "=A1")
        self.s.set("B1", 9)
        self.s.evaluations = 0
        self.s.set("A1", 2)
        self.assertEqual(self.s.evaluations, 0)
        self.assertEqual(self.s.get("B1"), 9)

    def test_clearing_a_cell(self):
        self.s.set("A1", 5)
        self.s.set("B1", "=A1+1")
        self.s.set("A1", None)
        self.assertEqual(self.s.get("B1"), 1)

    def test_named_cells(self):
        self.s.set("A1", 3)
        self.s.set("B1", "=rate*2")
        self.assertEqual(self.s.get("B1"), CellError("#NAME?"))
        self.s.define("rate", "A1")
        self.assertEqual(self.s.get("B1"), 6)

    def test_incremental_matches_full_recalculation(self):
        rng = random.Random(4)
        cols = "ABCDE"
        for _ in range(1500):
            target = f"{rng.choice(cols)}{rng.randint(1, 8)}"
            kind = rng.randrange(4)
            if kind == 0:
                raw = rng.randint(-9, 9)
            elif kind == 1:
                raw = f"={rng.choice(cols)}{rng.randint(1, 8)}+{rng.randint(1, 5)}"
            elif kind == 2:
                raw = f"=SUM({rng.choice(cols)}1:{rng.choice(cols)}{rng.randint(2, 8)})"
            else:
                raw = None
            self.s.set(target, raw)
        fresh = Sheet.from_json(self.s.to_json())
        for key in self.s.cells:
            self.assertEqual(self.s.get(key), fresh.get(key), cell_name(*key))


class TestCopy(Base):
    def test_shift(self):
        self.assertEqual(shift_formula("A1+$B1+C$1+$D$1", 1, 1), "B2+$B2+D$1+$D$1")
        self.assertEqual(shift_formula("SUM(A1:B2)", 2, 0), "SUM(A3:B4)")
        self.assertEqual(shift_formula('"A1"&A1', 1, 0), '"A1"&A2')
        self.assertEqual(shift_formula("A1", -1, 0), "#REF!")

    def test_copy_cell(self):
        self.s.set_many({"A1": 2, "A2": 3, "B1": "=A1*$A$1"})
        self.s.copy("B1", "B2")
        self.assertEqual(self.s.raw("B2"), "=A2*$A$1")
        self.assertEqual(self.s.get("B2"), 6)


class TestFiles(Base):
    def test_csv_round_trip(self):
        s = Sheet.from_csv('2,3,=A1*B1\nname,,=C1+1\n')
        self.assertEqual(s.get("C2"), 7)
        self.assertEqual(s.to_csv(), "2,3,6\nname,,7\n")
        self.assertEqual(s.to_csv(values=False), "2,3,=A1*B1\nname,,=C1+1\n")

    def test_json_round_trip(self):
        self.s.set_many({"A1": 2, "B1": "=A1+total"})
        self.s.define("total", "A1")
        again = Sheet.from_json(self.s.to_json())
        self.assertEqual(again.get("B1"), 4)

    def test_display(self):
        self.s.set_many({"A1": "=1/3", "A2": "=1/0", "A3": "=2+2", "A4": "=1=1"})
        self.assertEqual(self.s.display("A1"), "0.3333333333")
        self.assertEqual(self.s.display("A2"), "#DIV/0!")
        self.assertEqual(self.s.display("A3"), "4")
        self.assertEqual(self.s.display("A4"), "TRUE")
        self.assertEqual(self.s.display("A9"), "")


if __name__ == "__main__":
    unittest.main()
