from .base import MiningProvider
from .binance_spot import BinanceSpotKlineProvider
from .fake import DeterministicFakeProvider


PROVIDERS: dict[str, type[MiningProvider]] = {
    provider.provider_id: provider
    for provider in (BinanceSpotKlineProvider, DeterministicFakeProvider)
}


def get_provider(provider_id: str) -> type[MiningProvider]:
    if type(provider_id) is not str:
        raise ValueError("Mining provider ID must be a string.")
    try:
        return PROVIDERS[provider_id]
    except KeyError as exc:
        raise ValueError(f"Unknown mining provider: {provider_id}") from exc


def enabled_provider_ids(*, include_test: bool = False) -> frozenset[str]:
    """Return the provider IDs admitted by the current runtime policy."""
    return frozenset(
        provider_id
        for provider_id, provider in PROVIDERS.items()
        if include_test or not provider.test_only
    )


def public_providers(*, include_test: bool = False) -> list[dict]:
    values = []
    for provider_id, provider in sorted(PROVIDERS.items()):
        if provider_id not in enabled_provider_ids(include_test=include_test):
            continue
        values.append(provider.public_descriptor())
    return values
