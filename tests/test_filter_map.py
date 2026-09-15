import textwrap

from beancount import loader
from beancount.core.data import Transaction

from beancount_lazy_plugins.filter_map import apply_set_action


def test_apply_set_action_formats():
    assert apply_set_action("ALLEGRO", "Allegro.pl sp. z o.o.") == "ALLEGRO"
    assert apply_set_action("replace:{'sp. z o.o.': ''}", "Allegro sp. z o.o.") == "Allegro "
    assert apply_set_action("prefix:PL ", "Allegro") == "PL Allegro"
    assert apply_set_action("suffix: PL", "Allegro") == "Allegro PL"
    assert apply_set_action("upper:", "Żabka Z1234") == "ŻABKA Z1234"
    assert apply_set_action("upper:", "") == ""


def test_payees_mapped_then_upper_cased():
    ledger = textwrap.dedent("""
        plugin "beancount_lazy_plugins.filter_map"

        2026-01-01 open Assets:Bank
        2026-01-01 open Expenses:Shopping

        2026-01-01 custom "filter-map" "apply"
          filter: "payee:'^allegro'"
          setPayee: "Allegro"

        2026-01-01 custom "filter-map" "apply"
          filter: "payee:'.'"
          setPayee: "upper:"

        2026-02-01 * "allegro.pl sp. z o.o." "Books"
          Assets:Bank        -20.00 PLN
          Expenses:Shopping

        2026-02-02 * "Żabka Z1234" "Snacks"
          Assets:Bank         -5.00 PLN
          Expenses:Shopping

        2026-02-03 * "Card fee"
          Assets:Bank         -1.00 PLN
          Expenses:Shopping
    """)
    entries, errors, _ = loader.load_string(ledger)
    assert not errors
    txns = [e for e in entries if isinstance(e, Transaction)]
    assert [t.payee for t in txns] == ["ALLEGRO", "ŻABKA Z1234", None]
