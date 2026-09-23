"""Provider base class. All adapters must implement fetch() -> MarketSnapshot."""

from abc import ABC, abstractmethod
from models.snapshot import MarketSnapshot


class BaseProvider(ABC):
    """Abstract base for all data providers.

    Each adapter: fetch → parse → normalize → validate → MarketSnapshot.
    Adapters MUST NOT calculate trading verdicts.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable provider name."""
        ...

    @abstractmethod
    def fetch(self) -> MarketSnapshot:
        """Fetch latest data and return a normalized MarketSnapshot.

        Returns a snapshot with data_status="UNAVAILABLE" if fetch fails.
        Never raises exceptions to the caller — handle internally.
        """
        ...
