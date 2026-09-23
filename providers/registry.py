"""Provider registry — maps provider names to live data provider classes."""

from providers.base import BaseProvider

PROVIDERS: dict[str, type[BaseProvider]] = {}

# Lazy-load providers to avoid import errors if dependencies not installed
try:
    from providers.angel_provider import AngelProvider
    PROVIDERS["AngelProvider"] = AngelProvider
except (ImportError, Exception):
    pass

try:
    from providers.web_source import WebSourceProvider
    PROVIDERS["WebSource"] = WebSourceProvider
except (ImportError, Exception):
    pass

try:
    from providers.capital_flows_provider import CapitalFlowsProvider
    PROVIDERS["CapitalFlows"] = CapitalFlowsProvider
except (ImportError, Exception):
    pass

try:
    from providers.macro_provider import MacroProvider
    PROVIDERS["Macro"] = MacroProvider
except (ImportError, Exception):
    pass

try:
    from providers.nse_options_provider import NSEOptionsProvider
    PROVIDERS["NSEOptions"] = NSEOptionsProvider
except (ImportError, Exception):
    pass

try:
    from providers.sector_provider import SectorProvider
    PROVIDERS["Sector"] = SectorProvider
except (ImportError, Exception):
    pass

try:
    from providers.factor_direction_provider import FactorDirectionProvider
    PROVIDERS["FactorDirection"] = FactorDirectionProvider
except (ImportError, Exception):
    pass

try:
    from providers.greeks_provider import GreeksProvider
    PROVIDERS["Greeks"] = GreeksProvider
except (ImportError, Exception):
    pass


def get_provider(name: str, **kwargs) -> BaseProvider:
    """Get a provider instance by name."""
    if name not in PROVIDERS:
        raise ValueError(f"Unknown provider: {name}. Available: {list(PROVIDERS.keys())}")
    return PROVIDERS[name](**kwargs)


def list_providers() -> list[str]:
    """List available provider names."""
    return list(PROVIDERS.keys())
