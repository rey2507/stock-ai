"""Contract ranking and grouping for option suitability scores."""

from __future__ import annotations

from typing import List, Dict

from providers.suitability_calculator import SuitabilityScore


class ContractRanker:
    """Rank option contracts by suitability."""

    def rank_contracts(self, scores: List[SuitabilityScore], sort_by: str = "overall_score") -> List[SuitabilityScore]:
        if sort_by == "overall_score":
            return sorted(scores, key=lambda s: s.overall_score, reverse=True)
        elif sort_by == "recommendation":
            order = {"BUY": 5, "ACCEPT": 4, "CAUTION": 3, "AVOID": 2, "DO_NOT_TRADE": 1}
            return sorted(scores, key=lambda s: (order.get(s.recommendation, 0), s.overall_score), reverse=True)
        elif sort_by == "theta_efficiency":
            return sorted(scores, key=lambda s: s.theta_efficiency, reverse=True)
        return scores

    def group_by_recommendation(self, scores: List[SuitabilityScore]) -> Dict[str, List[SuitabilityScore]]:
        grouped = {"BUY": [], "ACCEPT": [], "CAUTION": [], "AVOID": [], "DO_NOT_TRADE": []}
        for score in scores:
            grouped[score.recommendation].append(score)
        for group in grouped.values():
            group.sort(key=lambda s: s.overall_score, reverse=True)
        return grouped
