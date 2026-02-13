"""
Tests for the VAT plugin.
"""
from decimal import Decimal

import pytest
from beancount import loader
from beancount.core.data import Transaction
from beancount.core.amount import Amount

from beancount_lazy_plugins.vat import vat, _compute_vat


class TestComputeVat:
    """Tests for the VAT computation helper."""

    def test_standard_rate(self):
        assert _compute_vat(Decimal("123.00"), Decimal("0.23")) == Decimal("23.00")

    def test_reduced_rate(self):
        assert _compute_vat(Decimal("108.00"), Decimal("0.08")) == Decimal("8.00")

    def test_rounding(self):
        assert _compute_vat(Decimal("100.00"), Decimal("0.23")) == Decimal("18.70")

    def test_zero(self):
        assert _compute_vat(Decimal("0"), Decimal("0.23")) == Decimal("0.00")


class TestVatPlugin:
    """Integration tests for the VAT plugin."""

    PLUGIN_CONFIG = """{
        'rate': '0.23',
        'input_account': 'Assets:VAT:Input',
        'output_account': 'Liabilities:Taxes:VAT:Output',
    }"""

    def _load_and_run(self, ledger_text, config=None):
        entries, errors, options_map = loader.load_string(ledger_text)
        assert not errors, f"Loader errors: {errors}"
        return vat(entries, options_map, config or self.PLUGIN_CONFIG)

    def _get_transactions(self, entries):
        return [e for e in entries if isinstance(e, Transaction)]

    def _find_posting(self, tx, account):
        return next(p for p in tx.postings if p.account == account)

    def _find_postings(self, tx, account):
        return [p for p in tx.postings if p.account == account]

    def test_basic_expense(self):
        """123 PLN gross expense → 100 net + 23 VAT."""
        ledger = """
option "operating_currency" "PLN"
1970-01-01 open Expenses:Office
1970-01-01 open Assets:Bank
1970-01-01 open Assets:VAT:Input

2025-01-15 * "Supplies" #vat
  Expenses:Office      123.00 PLN
  Assets:Bank         -123.00 PLN
"""
        entries, errors = self._load_and_run(ledger)
        assert not errors
        tx = self._get_transactions(entries)[0]

        assert self._find_posting(tx, "Expenses:Office").units == Amount(Decimal("100.00"), "PLN")
        assert self._find_posting(tx, "Assets:VAT:Input").units == Amount(Decimal("23.00"), "PLN")
        assert self._find_posting(tx, "Assets:Bank").units == Amount(Decimal("-123.00"), "PLN")

    def test_basic_income(self):
        """-1230 PLN income → -1000 net + -230 VAT output."""
        ledger = """
option "operating_currency" "PLN"
1970-01-01 open Income:Services
1970-01-01 open Assets:Bank
1970-01-01 open Liabilities:Taxes:VAT:Output

2025-01-15 * "Invoice" #vat
  Assets:Bank          1230.00 PLN
  Income:Services     -1230.00 PLN
"""
        entries, errors = self._load_and_run(ledger)
        assert not errors
        tx = self._get_transactions(entries)[0]

        assert self._find_posting(tx, "Income:Services").units == Amount(Decimal("-1000.00"), "PLN")
        assert self._find_posting(tx, "Liabilities:Taxes:VAT:Output").units == Amount(Decimal("-230.00"), "PLN")
        assert self._find_posting(tx, "Assets:Bank").units == Amount(Decimal("1230.00"), "PLN")

    def test_no_vat_tag_unchanged(self):
        """Transaction without #vat tag is not modified."""
        ledger = """
option "operating_currency" "PLN"
1970-01-01 open Expenses:Office
1970-01-01 open Assets:Bank

2025-01-15 * "Regular purchase"
  Expenses:Office      123.00 PLN
  Assets:Bank         -123.00 PLN
"""
        entries, errors = self._load_and_run(ledger)
        assert not errors
        tx = self._get_transactions(entries)[0]
        assert len(tx.postings) == 2
        assert self._find_posting(tx, "Expenses:Office").units == Amount(Decimal("123.00"), "PLN")

    def test_mixed_postings_vat_from_gross(self):
        """VAT calculated from full gross (2000), deducted only from Expenses."""
        ledger = """
option "operating_currency" "PLN"
1970-01-01 open Expenses:Gifts
1970-01-01 open Assets:Bank
1970-01-01 open Assets:Receivables:People:Teresa
1970-01-01 open Assets:VAT:Input

2026-02-13 * "SFERIS" "Pixel 10" #vat
  Assets:Bank                          -2000.00 PLN
  Expenses:Gifts                        1500.00 PLN
  Assets:Receivables:People:Teresa       500.00 PLN
"""
        entries, errors = self._load_and_run(ledger)
        assert not errors
        tx = self._get_transactions(entries)[0]

        # VAT from 2000 gross = 2000 * 23/123 = 373.98
        vat_amount = Decimal("373.98")
        assert self._find_posting(tx, "Assets:VAT:Input").units == Amount(vat_amount, "PLN")
        # Expenses reduced by full VAT
        assert self._find_posting(tx, "Expenses:Gifts").units == Amount(Decimal("1500.00") - vat_amount, "PLN")
        # Assets postings unchanged
        assert self._find_posting(tx, "Assets:Bank").units == Amount(Decimal("-2000.00"), "PLN")
        assert self._find_posting(tx, "Assets:Receivables:People:Teresa").units == Amount(Decimal("500.00"), "PLN")

    def test_multiple_expense_postings(self):
        """VAT distributed proportionally across multiple Expenses."""
        ledger = """
option "operating_currency" "PLN"
1970-01-01 open Expenses:Office
1970-01-01 open Expenses:Software
1970-01-01 open Assets:Bank
1970-01-01 open Assets:VAT:Input

2025-01-15 * "Mixed invoice" #vat
  Expenses:Office      123.00 PLN
  Expenses:Software    246.00 PLN
  Assets:Bank         -369.00 PLN
"""
        entries, errors = self._load_and_run(ledger)
        assert not errors
        tx = self._get_transactions(entries)[0]

        # VAT from 369 gross = 369 * 23/123 = 69.00
        assert self._find_posting(tx, "Assets:VAT:Input").units == Amount(Decimal("69.00"), "PLN")
        # Office: 123 - 69*(123/369) = 123 - 23 = 100
        assert self._find_posting(tx, "Expenses:Office").units == Amount(Decimal("100.00"), "PLN")
        # Software: 246 - 69*(remainder) = 246 - 46 = 200
        assert self._find_posting(tx, "Expenses:Software").units == Amount(Decimal("200.00"), "PLN")

    def test_auto_balanced_expense(self):
        """Expense with auto-balanced amount (filled by beancount loader)."""
        ledger = """
option "operating_currency" "PLN"
1970-01-01 open Expenses:Office
1970-01-01 open Assets:Bank
1970-01-01 open Assets:VAT:Input

2025-01-15 * "Supplies" #vat
  Expenses:Office
  Assets:Bank         -123.00 PLN
"""
        entries, errors = self._load_and_run(ledger)
        assert not errors
        tx = self._get_transactions(entries)[0]

        assert self._find_posting(tx, "Expenses:Office").units == Amount(Decimal("100.00"), "PLN")
        assert self._find_posting(tx, "Assets:VAT:Input").units == Amount(Decimal("23.00"), "PLN")

    def test_custom_config(self):
        """Custom rate and account names."""
        ledger = """
option "operating_currency" "PLN"
1970-01-01 open Expenses:Food
1970-01-01 open Assets:Bank
1970-01-01 open Assets:Tax:VATInput

2025-01-15 * "Groceries" #vat
  Expenses:Food        108.00 PLN
  Assets:Bank         -108.00 PLN
"""
        config = "{'rate': '0.08', 'input_account': 'Assets:Tax:VATInput'}"
        entries, errors = self._load_and_run(ledger, config)
        assert not errors
        tx = self._get_transactions(entries)[0]

        assert self._find_posting(tx, "Expenses:Food").units == Amount(Decimal("100.00"), "PLN")
        assert self._find_posting(tx, "Assets:Tax:VATInput").units == Amount(Decimal("8.00"), "PLN")

    def test_liabilities_untouched(self):
        """Liabilities postings are never modified."""
        ledger = """
option "operating_currency" "PLN"
1970-01-01 open Expenses:Office
1970-01-01 open Liabilities:CreditCard
1970-01-01 open Assets:VAT:Input

2025-01-15 * "Card purchase" #vat
  Expenses:Office           123.00 PLN
  Liabilities:CreditCard   -123.00 PLN
"""
        entries, errors = self._load_and_run(ledger)
        assert not errors
        tx = self._get_transactions(entries)[0]

        assert self._find_posting(tx, "Liabilities:CreditCard").units == Amount(Decimal("-123.00"), "PLN")
        assert self._find_posting(tx, "Expenses:Office").units == Amount(Decimal("100.00"), "PLN")
        assert self._find_posting(tx, "Assets:VAT:Input").units == Amount(Decimal("23.00"), "PLN")

    def test_transaction_balances(self):
        """Verify the modified transaction sums to zero."""
        ledger = """
option "operating_currency" "PLN"
1970-01-01 open Expenses:Gifts
1970-01-01 open Assets:Bank
1970-01-01 open Assets:Receivables
1970-01-01 open Assets:VAT:Input

2026-01-15 * "Purchase" #vat
  Assets:Bank          -2000.00 PLN
  Expenses:Gifts        1500.00 PLN
  Assets:Receivables     500.00 PLN
"""
        entries, errors = self._load_and_run(ledger)
        assert not errors
        tx = self._get_transactions(entries)[0]

        total = sum(p.units.number for p in tx.postings)
        assert total == Decimal("0"), f"Transaction does not balance: {total}"

    def test_mixed_income_and_expense(self):
        """When Income and Expenses coexist, VAT is from Income only. Expenses untouched."""
        ledger = """
option "operating_currency" "PLN"
1970-01-01 open Assets:Bank
1970-01-01 open Expenses:Insurance
1970-01-01 open Income:Roedl
1970-01-01 open Liabilities:Taxes:VAT:Output

2026-01-29 * "ROEDL" "Invoice minus insurance" #vat
  Assets:Bank          72706.25 PLN
  Expenses:Insurance      79.00 PLN
  Income:Roedl        -72785.25 PLN
"""
        entries, errors = self._load_and_run(ledger)
        assert not errors
        tx = self._get_transactions(entries)[0]

        # VAT from Income = 72785.25 * 23/123 = 13610.25
        vat_amount = _compute_vat(Decimal("72785.25"), Decimal("0.23"))

        # Income reduced (less negative)
        income = self._find_posting(tx, "Income:Roedl")
        assert income.units == Amount(Decimal("-72785.25") + vat_amount, "PLN")

        # Output VAT posting (negative)
        vat_posting = self._find_posting(tx, "Liabilities:Taxes:VAT:Output")
        assert vat_posting.units == Amount(-vat_amount, "PLN")

        # Expenses untouched
        expense = self._find_posting(tx, "Expenses:Insurance")
        assert expense.units == Amount(Decimal("79.00"), "PLN")

        # Bank untouched
        bank = self._find_posting(tx, "Assets:Bank")
        assert bank.units == Amount(Decimal("72706.25"), "PLN")

        # Verify balance
        total = sum(p.units.number for p in tx.postings)
        assert total == Decimal("0"), f"Does not balance: {total}"

