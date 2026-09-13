"""Text frontend adapted from the model-matched classic VITS Space."""

from . import cleaners


def text_to_sequence(text, symbols, cleaner_names):
    """Convert text to symbol IDs after running the configured model cleaners."""
    symbol_to_id = {symbol: index for index, symbol in enumerate(symbols)}
    sequence = []
    clean_text = _clean_text(text, cleaner_names)
    for symbol in clean_text:
        symbol_id = symbol_to_id.get(symbol)
        if symbol_id is not None:
            sequence.append(symbol_id)
    return sequence, clean_text


def _clean_text(text, cleaner_names):
    for name in cleaner_names:
        cleaner = getattr(cleaners, name, None)
        if cleaner is None:
            raise RuntimeError(f"Unknown VITS text cleaner: {name}")
        text = cleaner(text)
    return text
