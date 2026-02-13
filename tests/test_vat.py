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
        """23% VAT from 123 PLN gross → 23 PLN VAT."""
        assert _compute_vat(Decimal("123.00"), Decimal("0.23")) == Decimal("23.00")

    def test_reduced_rate(self):
        """8% VAT from 108 PLN gross → 8 PLN VAT."""
        assert _compute_vat(Decimal("108.00"), Decimal("0.08")) == Decimal("8.00")

    def test_rounding(self):
        """VAT with rounding: 100 PLN * 0.23 / 1.23 = 18.699... → 18.70."""
        assert _compute_vat(Decimal("100.00"), Decimal("0.23")) == Decimal("18.70")

    def test_negative_amount(self):
        """Negative gross amount (income) preserves sign."""
        result = _compute_vat(Decimal("-123.00"), Decimal("0.23"))
        assert result == Decimal("-23.00")

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
        """Load a ledger string and run the VAT plugin."""
        entries, errors, options_map = loader.load_string(ledger_text)
        assert not errors, f"Loader errors: {errors}"
        return vat(entries, options_map, config or self.PLUGIN_CONFIG)

    def _get_transactions(self, entries):
        return [e for e in entries if isinstance(e, Transaction)]

    def test_basic_expense_vat(self):
        """Expense posting: 123 PLN gross → 100 PLN net + 23 PLN VAT input."""
        ledger = """
option "operating_currency" "PLN"

1970-01-01 open Expenses:Office
1970-01-01 open Assets:Bank:Checking
1970-01-01 open Assets:VAT:Input

2025-01-15 * "Office Supplies" #vat
  Expenses:Office     123.00 PLN
  Assets:Bank:Checking
"""
        entries, errors = self._load_and_run(ledger)
        assert not errors

        txns = self._get_transactions(entries)
        assert len(txns) == 1
        tx = txns[0]

        # Should have 3 postings: Expenses (net), VAT Input, Assets (unchanged)
        assert len(tx.postings) == 3

        expense_posting = next(p for p in tx.postings if p.account == "Expenses:Office")
        assert expense_posting.units == Amount(Decimal("100.00"), "PLN")

        vat_posting = next(p for p in tx.postings if p.account == "Assets:VAT:Input")
        assert vat_posting.units == Amount(Decimal("23.00"), "PLN")

        bank_posting = next(p for p in tx.postings if p.account == "Assets:Bank:Checking")
        assert bank_posting.units == Amount(Decimal("-123.00"), "PLN")

    def test_basic_income_vat(self):
        """Income posting: -1230 PLN gross → -1000 PLN net + -230 PLN VAT output."""
        ledger = """
option "operating_currency" "PLN"

1970-01-01 open Income:Services
1970-01-01 open Assets:Bank:Checking
1970-01-01 open Liabilities:Taxes:VAT:Output

2025-01-15 * "Client Invoice" #vat
  Assets:Bank:Checking   1230.00 PLN
  Income:Services       -1230.00 PLN
"""
        entries, errors = self._load_and_run(ledger)
        assert not errors

        txns = self._get_transactions(entries)
        assert len(txns) == 1
        tx = txns[0]

        assert len(tx.postings) == 3

        income_posting = next(p for p in tx.postings if p.account == "Income:Services")
        assert income_posting.units == Amount(Decimal("-1000.00"), "PLN")

        vat_posting = next(p for p in tx.postings if p.account == "Liabilities:Taxes:VAT:Output")
        assert vat_posting.units == Amount(Decimal("-230.00"), "PLN")

        bank_posting = next(p for p in tx.postings if p.account == "Assets:Bank:Checking")
        assert bank_posting.units == Amount(Decimal("1230.00"), "PLN")

    def test_no_vat_tag_unchanged(self):
        """Transaction without #vat tag should not be modified."""
        ledger = """
option "operating_currency" "PLN"

1970-01-01 open Expenses:Office
1970-01-01 open Assets:Bank:Checking

2025-01-15 * "Regular purchase"
  Expenses:Office     123.00 PLN
  Assets:Bank:Checking
"""
        entries, errors = self._load_and_run(ledger)
        assert not errors

        txns = self._get_transactions(entries)
        assert len(txns) == 1
        tx = txns[0]

        # Should remain 2 postings, unchanged
        assert len(tx.postings) == 2

        expense_posting = next(p for p in tx.postings if p.account == "Expenses:Office")
        assert expense_posting.units == Amount(Decimal("123.00"), "PLN")

    def test_multiple_expense_postings(self):
        """Multiple Expenses postings in one transaction: VAT extracted from each."""
        ledger = """
option "operating_currency" "PLN"

1970-01-01 open Expenses:Office
1970-01-01 open Expenses:Software
1970-01-01 open Assets:Bank:Checking
1970-01-01 open Assets:VAT:Input

2025-01-15 * "Mixed invoice" #vat
  Expenses:Office     123.00 PLN
  Expenses:Software   246.00 PLN
  Assets:Bank:Checking
"""
        entries, errors = self._load_and_run(ledger)
        assert not errors

        txns = self._get_transactions(entries)
        assert len(txns) == 1
        tx = txns[0]

        # 2 expense postings (net) + 2 VAT postings + 1 bank = 5
        assert len(tx.postings) == 5

        office = next(p for p in tx.postings if p.account == "Expenses:Office")
        assert office.units == Amount(Decimal("100.00"), "PLN")

        software = next(p for p in tx.postings if p.account == "Expenses:Software")
        assert software.units == Amount(Decimal("200.00"), "PLN")

        vat_postings = [p for p in tx.postings if p.account == "Assets:VAT:Input"]
        assert len(vat_postings) == 2
        vat_total = sum(p.units.number for p in vat_postings)
        assert vat_total == Decimal("69.00")  # 23 + 46

        bank_posting = next(p for p in tx.postings if p.account == "Assets:Bank:Checking")
        assert bank_posting.units == Amount(Decimal("-369.00"), "PLN")

    def test_auto_balanced_posting(self):
        """Transaction with auto-balanced posting (no explicit amount)."""
        ledger = """
option "operating_currency" "PLN"

1970-01-01 open Expenses:Office
1970-01-01 open Assets:Bank:Checking
1970-01-01 open Assets:VAT:Input

2025-01-15 * "Office Supplies" #vat
  Expenses:Office     123.00 PLN
  Assets:Bank:Checking
"""
        entries, errors = self._load_and_run(ledger)
        assert not errors

        txns = self._get_transactions(entries)
        tx = txns[0]

        # Verify the transaction has correct structure
        expense_posting = next(p for p in tx.postings if p.account == "Expenses:Office")
        assert expense_posting.units == Amount(Decimal("100.00"), "PLN")

    def test_custom_config(self):
        """Custom rate and account names via config."""
        ledger = """
option "operating_currency" "PLN"

1970-01-01 open Expenses:Food
1970-01-01 open Assets:Bank:Checking
1970-01-01 open Assets:Tax:VATInput

2025-01-15 * "Groceries" #vat
  Expenses:Food     108.00 PLN
  Assets:Bank:Checking
"""
        config = """{
            'rate': '0.08',
            'input_account': 'Assets:Tax:VATInput',
        }"""
        entries, errors = self._load_and_run(ledger, config)
        assert not errors

        txns = self._get_transactions(entries)
        tx = txns[0]

        expense_posting = next(p for p in tx.postings if p.account == "Expenses:Food")
        assert expense_posting.units == Amount(Decimal("100.00"), "PLN")

        vat_posting = next(p for p in tx.postings if p.account == "Assets:Tax:VATInput")
        assert vat_posting.units == Amount(Decimal("8.00"), "PLN")

    def test_assets_liabilities_untouched(self):
        """Assets and Liabilities postings are never modified."""
        ledger = """
option "operating_currency" "PLN"

1970-01-01 open Expenses:Office
1970-01-01 open Liabilities:CreditCard
1970-01-01 open Assets:VAT:Input

2025-01-15 * "Paid by card" #vat
  Expenses:Office           123.00 PLN
  Liabilities:CreditCard   -123.00 PLN
"""
        entries, errors = self._load_and_run(ledger)
        assert not errors

        txns = self._get_transactions(entries)
        tx = txns[0]

        card_posting = next(p for p in tx.postings if p.account == "Liabilities:CreditCard")
        assert card_posting.units == Amount(Decimal("-123.00"), "PLN")
