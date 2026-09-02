from omega.application.visual_asset_engine import (
    ResolvedVisualAsset,
    VisualAssetCandidate,
    VisualAssetEngine,
    VisualAssetProvider,
    VisualAssetRequest,
)


class VisualAssetOrchestratorError(Exception):
    pass


class VisualAssetOrchestrator:
    def __init__(
        self,
        engine: VisualAssetEngine,
        providers: list[VisualAssetProvider],
    ):
        if not providers:
            raise VisualAssetOrchestratorError("No providers registered")

        self._engine = engine
        self._providers: dict[str, VisualAssetProvider] = {}

        for provider in providers:
            name = provider.provider_name
            if not isinstance(name, str) or not name.strip():
                raise VisualAssetOrchestratorError("Provider name cannot be blank")
            name = name.strip()
            if name in self._providers:
                raise VisualAssetOrchestratorError(f"Duplicate provider name: {name}")
            self._providers[name] = provider

    async def resolve(self, request: VisualAssetRequest) -> ResolvedVisualAsset:
        all_candidates: list[VisualAssetCandidate] = []

        sorted_provider_names = sorted(self._providers.keys())
        for name in sorted_provider_names:
            provider = self._providers[name]
            try:
                candidates = await provider.search(request, limit=5)
            except Exception as e:
                raise VisualAssetOrchestratorError("Provider search failed") from e

            for candidate in candidates:
                if candidate.provider != name:
                    raise VisualAssetOrchestratorError(
                        "Provider candidate identity mismatch"
                    )

            all_candidates.extend(candidates)

        if not all_candidates:
            raise VisualAssetOrchestratorError("No candidates found")

        best_candidate = self._engine.select_candidate(request, all_candidates)
        if not best_candidate:
            raise VisualAssetOrchestratorError("No suitable candidates selected")

        target_provider = self._providers.get(best_candidate.provider)
        if not target_provider:
            raise VisualAssetOrchestratorError("Selected candidate provider is not registered")

        try:
            return await target_provider.fetch(best_candidate)
        except Exception as e:
            raise VisualAssetOrchestratorError("Provider fetch failed") from e
