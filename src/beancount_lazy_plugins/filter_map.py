"""
A Beancount plugin that allows to apply filter+map operations over transactions in your ledger.

Filters are the same as Fava filters and even use the same code.
Possible operations are adding tags and metadata. A lot of effects can be achieved by using these and
other plugins in combination.
"""

import ast
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Set, Dict, Any

from beancount.core.data import Custom, Posting, Transaction
from fava.core.fava_options import FavaOptions
from fava.core.filters import LEXER, AccountFilter, AdvancedFilter, TimeFilter
from beancount.parser.grammar import ValueType


class OperationParams(Enum):
    TIME = "time"
    ACCOUNT = "account"
    ADVANCED = "filter"

    ADD_TAGS = "addTags"
    ADD_META = "addMeta"
    SET_PAYEE = "setPayee"
    SET_NARRATION = "setNarration"


ALL_OPERATION_PARAMS = [
    OperationParams.TIME,
    OperationParams.ACCOUNT,
    OperationParams.ADVANCED,
    OperationParams.ADD_TAGS,
    OperationParams.ADD_META,
    OperationParams.SET_PAYEE,
    OperationParams.SET_NARRATION,
]

__plugins__ = ["filter_map"]


@dataclass
class OperationConfig:
    """Configuration for a filter-map operation."""
    entry: Custom

    time: Optional[str] = None
    account: Optional[str] = None
    filter: Optional[str] = None

    addTags: Optional[str] = None
    addMeta: Optional[str] = None
    setPayee: Optional[str] = None
    setNarration: Optional[str] = None
    filters: List[Any] = field(default_factory=list)
    needed: List[Any] = field(default_factory=list)
    tagValues: List[str] = field(default_factory=list)
    times_applied: int = 0  # Track how many times this filter was applied


def apply_set_action(action_value: str, current_value: str) -> str:
    """Apply a set action to a value.
    
    Supports the following formats:
    - "new value" - replaces the entire value
    - "replace:{'old':'new', ...}" - replaces each 'old' with 'new' in the current value
    - "prefix:text" - adds text at the beginning of the current value
    - "suffix:text" - adds text at the end of the current value
    - "upper:" - the current value in upper case (e.g. every payee: `filter: "payee:'.'"`)

    Args:
        action_value: The action specification
        current_value: The current value to modify
    
    Returns:
        The modified value
    """
    if action_value.startswith("replace:"):
        replace_spec = action_value[8:]  # Remove "replace:" prefix
        try:
            replacements = ast.literal_eval(replace_spec)
            if isinstance(replacements, dict):
                result = current_value or ""
                for old, new in replacements.items():
                    result = result.replace(old, new)
                return result
        except (ValueError, SyntaxError):
            pass  # Invalid format, fall through to default behavior
    elif action_value.startswith("prefix:"):
        prefix = action_value[7:]  # Remove "prefix:" prefix
        return prefix + (current_value or "")
    elif action_value.startswith("suffix:"):
        suffix = action_value[7:]  # Remove "suffix:" prefix
        return (current_value or "") + suffix
    elif action_value == "upper:":
        return (current_value or "").upper()
    # Default behavior: replace entire value
    return action_value


def matches_filter(entry, filter):
    if isinstance(filter, TimeFilter):
        return (
            entry.date >= filter.date_range.begin and entry.date < filter.date_range.end
        )
    else:
        return len(filter.apply([entry])) > 0


def needed_keys(value):
    """The metadata keys without which the advanced filter ``value`` takes no transaction — when it reads nothing
    but metadata keys (of the transaction, or of its postings in ``any(…)``) and has nothing that holds without them
    (``-``, ``all(…)``, tags, links, bare strings or amounts, fields like ``payee`` or ``account``); else None. A
    transaction with none of the keys, nor any of its postings, is not taken: the 36 IBAN rules of a ledger read only
    ``account_iban`` and the like, which a third of the transactions have."""
    try:
        tokens = list(LEXER.lex(value))
    except Exception:
        return None
    scope = []
    keys = set()
    previous = None
    for token in tokens:
        kind = token.type
        if kind == "ANY":
            scope.append("posting")
        elif kind == "(":
            scope.append("group")
        elif kind == ")":
            if not scope:
                return None
            scope.pop()
        elif kind == "KEY":
            # Fava reads an attribute when the object has one by that name (a field, or a method of the tuple)
            if hasattr(Posting if "posting" in scope else Transaction, token.value):
                return None
            keys.add(token.value)
        elif kind == "STRING" and previous is not None and previous.type == "EQ_OP":
            pass
        elif kind == "NUMBER" and previous is not None and previous.type == "CMP_OP":
            pass
        elif kind in ("EQ_OP", ","):
            pass
        elif kind == "CMP_OP" and previous is not None and previous.type == "KEY":
            pass
        else:
            return None  # ALL, "-", a tag, a link, a bare string or amount
        previous = token
    return frozenset(keys) if keys and not scope else None


def _posting_keys(entry):
    found = set()
    for p in entry.postings:
        if p.meta:
            found.update(p.meta)
    return found


def matching(entries, filters, needed=None, posting_keys=None):
    """The positions of the entries that every filter takes (a filter's ``apply`` keeps the entries it takes, in
    order; a time filter compares dates, as :func:`matches_filter` does — its ``apply`` would clamp the entries).
    ``needed`` (per filter: :func:`needed_keys` or None) and ``posting_keys`` (per entry: the keys of its postings'
    metadata) skip the entries a filter cannot take before Fava tests the rest."""
    chosen = list(range(len(entries)))
    for n, f in enumerate(filters):
        if not chosen:
            break
        if isinstance(f, TimeFilter):
            begin, end = f.date_range.begin, f.date_range.end
            chosen = [j for j in chosen if begin <= entries[j].date < end]
            continue
        keys = needed[n] if needed else None
        if keys is not None and posting_keys is not None:
            chosen = [j for j in chosen
                      if (entries[j].meta and not keys.isdisjoint(entries[j].meta))
                      or not keys.isdisjoint(posting_keys[j])]
            if not chosen:
                break
        kept = f.apply([entries[j] for j in chosen])
        taken = []
        k = 0
        for j in chosen:
            if k < len(kept) and kept[k] is entries[j]:
                taken.append(j)
                k += 1
        if k != len(kept):  # a filter that does not keep the order: one entry at a time
            taken = [j for j in chosen if matches_filter(entries[j], f)]
        chosen = taken
    return chosen


def apply_operation(op, entry):
    """``entry`` with the operation's tags, metadata, payee and narration (counted in ``times_applied``)."""
    op.times_applied += 1  # Increment the apply count
    new_tags = entry.tags
    if op.addTags:
        new_tags = set(entry.tags)
        new_tags.update(op.tagValues)
    new_meta = entry.meta
    if op.addMeta:
        new_meta_dict = ast.literal_eval(op.addMeta)
        new_meta.update(new_meta_dict)

    # Handle SET_PAYEE and SET_NARRATION operations
    new_payee = entry.payee
    if op.setPayee:
        new_payee = apply_set_action(op.setPayee, entry.payee or "")

    new_narration = entry.narration
    if op.setNarration:
        new_narration = apply_set_action(op.setNarration, entry.narration or "")

    return Transaction(
        new_meta,
        entry.date,
        flag=entry.flag,
        payee=new_payee,
        narration=new_narration,
        tags=new_tags,
        links=entry.links,
        postings=entry.postings,
    )


def filter_map(entries, options_map, config_str=None):
    presets = {}
    # read presets first
    for entry in entries:
        if (
            isinstance(entry, Custom)
            and entry.type == "filter-map"
            and entry.values[0].value.strip() == "preset"
        ):
            presets[entry.meta["name"]] = entry.meta

    # then form all operations
    operations = []
    for entry in entries:
        if (
            isinstance(entry, Custom)
            and entry.type == "filter-map"
            and entry.values[0].value.strip() == "apply"
        ):
            # Create a new OperationConfig instance
            config = OperationConfig(entry=entry)
            
            # Apply preset if available
            if "preset" in entry.meta:
                preset_name = entry.meta["preset"]
                preset_data = presets[preset_name]
                for param in ALL_OPERATION_PARAMS:
                    if param.value in preset_data:
                        setattr(config, param.value, preset_data[param.value])
            
            # Apply direct parameters
            for param in ALL_OPERATION_PARAMS:
                if param.value in entry.meta:
                    setattr(config, param.value, entry.meta[param.value])

            operations.append(config)

    # pre-calculate operation parameters defined by configuration
    for op in operations:
        filters = []

        if op.time:
            filters.append(
                TimeFilter(options_map, FavaOptions(), op.time)
            )
        if op.account:
            filters.append(AccountFilter(op.account))
        if op.filter:
            filters.append(AdvancedFilter(op.filter))

        # Store pre-calculated values
        op.filters = filters
        op.needed = [needed_keys(op.filter) if isinstance(f, AdvancedFilter) else None for f in filters]

        if op.addTags:
            op.tagValues = op.addTags.replace("#", "").split(" ")

    # now apply all operations to all transactions, one operation at a time over all of them: an entry's state after
    # operation k depends only on its own state after the operations before, so this gives what applying every
    # operation to one entry after another gives — without calling each filter once per entry (a third of the load of
    # a ledger with 96 rules and 10 000 transactions)
    new_entries = []
    positions = []
    for entry in entries:
        if (
            isinstance(entry, Custom)
            and entry.type == "filter-map"
            and entry.values[0].value.strip() == "apply"
        ):
            # ignore filter-map apply entries
            continue
        if isinstance(entry, Transaction):
            positions.append(len(new_entries))
        new_entries.append(entry)

    transactions = [new_entries[i] for i in positions]
    screened = any(keys is not None for op in operations for keys in op.needed)
    posting_keys = [_posting_keys(t) for t in transactions] if screened else None  # postings never change here
    for op in operations:
        for j in matching(transactions, op.filters, op.needed, posting_keys):
            transactions[j] = apply_operation(op, transactions[j])
    for i, transaction in zip(positions, transactions):
        new_entries[i] = transaction

    filter_map_entries = []
    # Add apply counts as metadata to the filter-map apply entries
    for i, op in enumerate(operations):
        entry = op.entry
        entry.meta["_timesApplied"] = op.times_applied
        # for better visibility in Fava
        if op.addTags:
            entry.values.append(ValueType(op.addTags, str))
        if op.filter:
            entry.values.append(ValueType(op.filter, str))

        filter_map_entries.append(entry)

    return filter_map_entries + new_entries, []
