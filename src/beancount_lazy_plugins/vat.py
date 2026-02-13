"""
A Beancount plugin that automatically extracts VAT from transactions tagged #vat.

For Expenses postings, VAT is posted to an input VAT account (e.g. Assets:VAT:Input).
For Income postings, VAT is posted to an output VAT account (e.g. Liabilities:Taxes:VAT:Output).

Amounts in the original transaction are assumed to be gross (VAT-inclusive).
The plugin splits each applicable posting into a net amount and a VAT posting.

Usage in main.bean:
    plugin "beancount_lazy_plugins.vat" "{
      'rate': '0.23',
      'input_account': 'Assets:VAT:Input',
      'output_account': 'Liabilities:Taxes:VAT:Output',
    }"

Example:
    ; Before:
    2025-01-15 * "Office Supplies" #vat
      Expenses:Office     123.00 PLN
      Assets:Bank:Checking

    ; After (plugin transforms to):
    2025-01-15 * "Office Supplies" #vat
      Expenses:Office     100.00 PLN
      Assets:VAT:Input     23.00 PLN
      Assets:Bank:Checking
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

    The result is rounded to 2 decimal places. The sign of the result
    matches the sign of the gross amount.
    """
    vat = gross * rate / (1 + rate)
    return vat.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def vat(entries, options_map, config_str=None):
    """Beancount plugin: Extract VAT from #vat-tagged transactions.

    For each transaction tagged with #vat, the plugin processes all
    Expenses:* and Income:* postings:
    - Reduces the posting amount by the VAT component
    - Adds a new posting to the appropriate VAT account

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

        # Process this #vat transaction
        new_postings = []
        vat_postings = []

        for posting in entry.postings:
            # Only process postings with explicit amounts
            if posting.units is None:
                new_postings.append(posting)
                continue

            account = posting.account
            gross = posting.units.number
            currency = posting.units.currency

            if account.startswith("Expenses:"):
                # Expense posting: extract input VAT
                vat_amount = _compute_vat(gross, rate)
                net_amount = gross - vat_amount

                # Replace posting with net amount
                new_postings.append(
                    posting._replace(units=Amount(net_amount, currency))
                )
                # Add VAT posting
                vat_postings.append(
                    data.Posting(
                        account=input_account,
                        units=Amount(vat_amount, currency),
                        cost=None,
                        price=None,
                        flag=None,
                        meta=None,
                    )
                )

            elif account.startswith("Income:"):
                # Income posting: extract output VAT
                # Income amounts are typically negative
                vat_amount = _compute_vat(gross, rate)
                net_amount = gross - vat_amount

                # Replace posting with net amount
                new_postings.append(
                    posting._replace(units=Amount(net_amount, currency))
                )
                # Add VAT posting
                vat_postings.append(
                    data.Posting(
                        account=output_account,
                        units=Amount(vat_amount, currency),
                        cost=None,
                        price=None,
                        flag=None,
                        meta=None,
                    )
                )

            else:
                # Assets, Liabilities, etc. — leave untouched
                new_postings.append(posting)

        # Build modified transaction with VAT postings appended
        modified_entry = entry._replace(postings=new_postings + vat_postings)
        new_entries.append(modified_entry)

    return new_entries, errors
