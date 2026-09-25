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


def test_rules_apply_in_order_and_see_what_the_rules_before_did():
    """Each rule runs over all transactions in turn (a third of a ledger's load was one filter call per rule and
    transaction): a later rule sees the payee, tags and metadata the earlier ones set, as before."""
    ledger = textwrap.dedent("""
        plugin "beancount_lazy_plugins.filter_map"

        2026-01-01 open Assets:Bank
        2026-01-01 open Expenses:Shopping

        2026-01-01 custom "filter-map" "apply"
          filter: "payee:'^shop'"
          setPayee: "SHOP"
          addMeta: "{'shop_id': 'x1'}"

        2026-01-01 custom "filter-map" "apply"
          filter: "shop_id:'^x1$'"
          addTags: "#shop"

        2026-01-01 custom "filter-map" "apply"
          filter: "payee:'^SHOP$'"
          setNarration: "prefix:[shop] "

        2026-02-01 * "shop one" "Books"
          Assets:Bank        -20.00 PLN
          Expenses:Shopping

        2026-02-02 * "Other" "Snacks"
          Assets:Bank         -5.00 PLN
          Expenses:Shopping
    """)
    entries, errors, _ = loader.load_string(ledger)
    assert not errors
    shop, other = [e for e in entries if isinstance(e, Transaction)]
    assert (shop.payee, shop.narration, shop.meta["shop_id"], "shop" in shop.tags) == ("SHOP", "[shop] Books", "x1", True)
    assert (other.payee, other.narration, "shop_id" in other.meta, "shop" in other.tags) == ("Other", "Snacks", False, False)


def test_rules_of_metadata_keys_skip_transactions_without_them_and_nothing_else():
    ledger = textwrap.dedent("""
        plugin "beancount_lazy_plugins.filter_map"

        2026-01-01 open Assets:Bank
        2026-01-01 open Expenses:Shopping

        2026-01-01 custom "filter-map" "apply"
          filter: "(account_iban:'^PL1$', any(account_iban:'^PL1$'))"
          addMeta: "{'account_name': 'Main'}"

        2026-01-01 custom "filter-map" "apply"
          filter: "-account_iban:'^PL1$'"
          addTags: "#elsewhere"

        2026-02-01 * "A" "On the posting"
          Assets:Bank        -20.00 PLN
            account_iban: "PL1"
          Expenses:Shopping

        2026-02-02 * "B" "On the transaction"
          account_iban: "PL1"
          Assets:Bank         -5.00 PLN
          Expenses:Shopping

        2026-02-03 * "C" "Nowhere"
          Assets:Bank         -1.00 PLN
          Expenses:Shopping
    """)
    entries, errors, _ = loader.load_string(ledger)
    assert not errors
    a, b, c = [e for e in entries if isinstance(e, Transaction)]
    assert [t.meta.get("account_name") for t in (a, b, c)] == ["Main", "Main", None]
    assert ["elsewhere" in t.tags for t in (a, b, c)] == [True, False, True]  # a negation is never skipped


def test_needed_keys_only_for_filters_that_need_them():
    from beancount_lazy_plugins.filter_map import needed_keys

    assert needed_keys("(account_iban:'^PL1$', any(account_bban:'^1$'))") == {"account_iban", "account_bban"}
    assert needed_keys("amount_pln > 10") == {"amount_pln"}
    for value in ("payee:'^shop'", "-account_iban:'x'", "all(account_iban:'x')", "#tag", "^link", "shop",
                  "any(account:'Expenses')", "account_iban:'x' payee:'y'", "> 10", "count:'x'"):
        assert needed_keys(value) is None, value
