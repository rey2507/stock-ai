# Providers package exports

from providers.news_provider import (
    NewsProvider,
    RSSNewsProvider,
    ReutersNewsProvider,
    MoneycontrolNewsProvider,
    EconomicTimesNewsProvider,
    MintNewsProvider,
    OfficialEventsProvider,
    get_default_providers,
)

from providers.news_aggregator import (
    NewsAggregator,
    fetch_market_news,
    DeduplicationResult,
)

from providers.news_cache import (
    NewsCache,
    get_news_cache,
    set_news_cache,
)

__all__ = [
    "NewsProvider",
    "RSSNewsProvider",
    "ReutersNewsProvider",
    "MoneycontrolNewsProvider",
    "EconomicTimesNewsProvider",
    "MintNewsProvider",
    "OfficialEventsProvider",
    "get_default_providers",
    "NewsAggregator",
    "fetch_market_news",
    "DeduplicationResult",
    "NewsCache",
    "get_news_cache",
    "set_news_cache",
]