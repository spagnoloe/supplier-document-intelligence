"""FX rate source: (currency, date) → rate to USD, or None when no rate is available."""

from collections.abc import Callable
from datetime import date

FxSource = Callable[[str, date], float | None]
