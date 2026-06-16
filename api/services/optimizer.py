
import logging
from typing import List, Tuple

logger = logging.getLogger(__name__)

TANK_RANGE_MILES = 500   # maximum range on a full tank
MPG = 10                 # miles per gallon
INF = float("inf")


def optimize(
    stations_on_route: List[dict],
    total_distance_miles: float,
    tank_range: float = TANK_RANGE_MILES,
    mpg: float = MPG,
) -> Tuple[List[dict], float]:
    
    if not stations_on_route:
        logger.warning("No stations on route – cannot optimize.")
        return [], 0.0

    # Build node list (virtual start, stations, virtual end)
    start_node = {"route_dist": 0.0, "retail_price": 0.0, "_is_start": True}
    end_node   = {"route_dist": total_distance_miles, "retail_price": 0.0, "_is_end": True}
    nodes = [start_node] + sorted(stations_on_route, key=lambda s: s["route_dist"]) + [end_node]
    n = len(nodes)

    # Dijkstra / DP state arrays
    dp   = [INF] * n   # dp[i] stores the minimum cost to reach node i
    prev = [-1]  * n   # predecessor trackers for backtracking

    dp[0] = 0.0

    for i in range(n - 1):
        if dp[i] == INF:
            continue  # node is unreachable

        price_i = nodes[i]["retail_price"]

        for j in range(i + 1, n):
            dist = nodes[j]["route_dist"] - nodes[i]["route_dist"]

            # Break early since nodes are sorted by distance
            if dist > tank_range:
                break

            # Calculate cost of fuel purchased at station i to get to station j
            gallons  = dist / mpg
            leg_cost = gallons * price_i

            new_cost = dp[i] + leg_cost
            if new_cost < dp[j]:
                dp[j]   = new_cost
                prev[j] = i

    # Verify if destination is reachable
    if dp[n - 1] == INF:
        logger.error("Route is infeasible: no station sequence covers the full distance.")
        return [], INF

    # Backtrack to reconstruct the optimal path
    path = []
    idx  = n - 1
    while idx != -1:
        path.append(idx)
        idx = prev[idx]
    path.reverse()

    # Build final response details
    fuel_stops: list[dict] = []
    total_cost = 0.0

    for k in range(len(path) - 1):
        i = path[k]
        j = path[k + 1]
        node = nodes[i]

        if node.get("_is_start") or node.get("_is_end"):
            continue  # ignore start/end placeholders

        dist     = nodes[j]["route_dist"] - node["route_dist"]
        gallons  = dist / mpg
        cost     = gallons * node["retail_price"]
        total_cost += cost

        fuel_stops.append({
            **{k: v for k, v in node.items() if not k.startswith("_")},
            "fuel_gallons": round(gallons, 2),
            "fuel_cost_usd": round(cost, 2),
            "route_distance_from_start_miles": round(node["route_dist"], 1),
        })

    logger.info(
        "Optimizer: %d stops, total cost $%.2f, route %.1f miles",
        len(fuel_stops), total_cost, total_distance_miles,
    )
    return fuel_stops, round(total_cost, 2)
