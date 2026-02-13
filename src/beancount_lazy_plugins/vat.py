"""
A Beancount plugin that automatically extracts VAT from transactions tagged #vat.

Income postings take priority:
  - If Income postings exist → VAT is calculated from Income amounts (output VAT).
    Expenses are left untouched.
  - If only Expenses exist → VAT is calculated from the total gross (sum of
    negative postings) and deducted from Expenses (input VAT).

For Expenses: VAT is posted to an input VAT account (e.g. Assets:VAT:Input).
For Income: VAT is posted to an output VAT account (e.g. Liabilities:Taxes:VAT:Output).
Assets and Liabilities postings are never modified.

Usage in main.bean:
    plugin "beancount_lazy_plugins.vat" "{
      'rate': '0.23',
      'input_account': 'Assets:VAT:Input',
      'output_account': 'Liabilities:Taxes:VAT:Output',
    }"
"""

import ast
from decimal import Decimal, ROUND_HALF_UP

from beancount.core import data
from beancount.core.amount import Amount
from beancount.core.number import D

__plugins__ = ("vat",)

DEFAULT_RATE = Decimal("0.23")
DEFAULT_INPUT_ACCOUNT = "Assets:VAT:Input"
DEFAULT_OUTPUT_ACCOUNT = "Liabilities:Taxes:VAT:Output"
VAT_TAG = "vat"


def _parse_config(config_str):
    """Parse plugin configuration string."""
    if not config_str:
        return {}
    return ast.literal_eval(config_str)


def _compute_vat(gross, rate):
    """Compute VAT amount from a gross (VAT-inclusive) value.

    VAT = gross * rate / (1 + rate)

    The result is rounded to 2 decimal places.
    """
    vat = gross * rate / (1 + rate)
    return vat.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def vat(entries, options_map, config_str=None):
    """Beancount plugin: Extract VAT from #vat-tagged transactions.

    Income postings take priority: if present, VAT is calculated from the
    total Income amount (output VAT) and Expenses are left untouched.
    If no Income postings exist, VAT is calculated from the total gross
    (sum of negative postings) and deducted from Expenses (input VAT).

    If there are multiple adjustable postings, the VAT deduction is
    distributed proportionally across them.

    Args:
      entries: A list of directives.
      options_map: A parser options dict.
      config_str: A configuration string in dict format.
    Returns:
      A tuple of entries and errors.
    """
    config = _parse_config(config_str)
    rate = Decimal(config.get("rate", str(DEFAULT_RATE)))
    input_account = config.get("input_account", DEFAULT_INPUT_ACCOUNT)
    output_account = config.get("output_account", DEFAULT_OUTPUT_ACCOUNT)

    new_entries = []
    errors = []

    for entry in entries:
        if not isinstance(entry, data.Transaction):
            new_entries.append(entry)
            continue

        if not entry.tags or VAT_TAG not in entry.tags:
            new_entries.append(entry)
            continue

        # Determine currency from first posting with units
        currency = next(
            (p.units.currency for p in entry.postings if p.units is not None),
            None,
        )
        if currency is None:
            new_entries.append(entry)
            continue

        # Identify Expenses and Income postings
        expense_indices = [
            i
            for i, p in enumerate(entry.postings)
            if p.account.startswith("Expenses:") and p.units is not None
        ]
        income_indices = [
            i
            for i, p in enumerate(entry.postings)
            if p.account.startswith("Income:") and p.units is not None
        ]

        if income_indices:
            # Income takes priority: VAT from Income amounts (output VAT)
            # Expenses are left untouched
            gross = abs(
                sum(
                    (entry.postings[i].units.number for i in income_indices),
                    Decimal("0"),
                )
            )
            vat_amount = _compute_vat(gross, rate)
            vat_account = output_account
            vat_posting_amount = -vat_amount
            adjustable_indices = income_indices
            adjustment_sign = Decimal("1")  # make Income less negative
        elif expense_indices:
            # No Income: VAT from total gross (sum of negative postings)
            negative_amounts = [
                p.units.number
                for p in entry.postings
                if p.units is not None and p.units.number < 0
            ]
            if not negative_amounts:
                new_entries.append(entry)
                continue
            gross = abs(sum(negative_amounts, Decimal("0")))
            vat_amount = _compute_vat(gross, rate)
            vat_account = input_account
            vat_posting_amount = vat_amount
            adjustable_indices = expense_indices
            adjustment_sign = Decimal("-1")  # reduce Expenses
        else:
            # No Expenses or Income postings to adjust
            new_entries.append(entry)
            continue

        # 4. Distribute VAT deduction across adjustable postings proportionally
        new_postings = list(entry.postings)

        total_adjustable = sum(
            abs(entry.postings[i].units.number) for i in adjustable_indices
        )

        remaining_vat = vat_amount
        for idx, orig_idx in enumerate(adjustable_indices):
            posting = entry.postings[orig_idx]

            if idx == len(adjustable_indices) - 1:
                # Last posting gets the remainder (handles rounding)
                adj = remaining_vat
            else:
                proportion = abs(posting.units.number) / total_adjustable
                adj = (vat_amount * proportion).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
                remaining_vat -= adj

            adjusted_amount = posting.units.number + (adjustment_sign * adj)
            new_postings[orig_idx] = posting._replace(
                units=Amount(adjusted_amount, posting.units.currency)
            )

        # 5. Add VAT posting
        vat_posting = data.Posting(
            account=vat_account,
            units=Amount(vat_posting_amount, currency),
            cost=None,
            price=None,
            flag=None,
            meta=None,
        )
        new_postings.append(vat_posting)

        # Build modified transaction
        modified_entry = entry._replace(postings=new_postings)
        new_entries.append(modified_entry)

    return new_entries, errors
