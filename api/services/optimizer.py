"""
Dynamic Programming fuel-stop optimizer.

Problem:
    Given N candidate stations along a route (sorted by distance from start),
    find the cheapest subset of stops such that:
      - No two consecutive stops (including START and END) are > 500 miles apart
      - Total cost = Σ (gallons_needed_for_next_leg × price_at_current_stop)

Algorithm: O(N²) DP with path reconstruction.

Assumption:
    - Vehicle starts with a full tank (500-mile range) at START.
    - At each stop, we buy exactly enough fuel to drive to the NEXT stop.
    - The fuel already in the tank at START is "sunk cost" (not charged).
"""
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
    """
    Find the cheapest sequence of fuel stops.

    Args:
        stations_on_route: list of station dicts, each must have:
            - route_dist (float): miles from route start
            - retail_price (float): USD/gallon
            - name, city, state, latitude, longitude
        total_distance_miles: route length
        tank_range: max miles without refueling
        mpg: fuel efficiency

    Returns:
        (fuel_stops, total_cost_usd)
        fuel_stops: list of station dicts augmented with:
            - fuel_gallons
            - fuel_cost_usd
            - route_distance_from_start_miles
    """
    if not stations_on_route:
        logger.warning("No stations on route – cannot optimize.")
        return [], 0.0

    # ── Build node list ──────────────────────────────────────────────────────
    start_node = {"route_dist": 0.0, "retail_price": 0.0, "_is_start": True}
    end_node   = {"route_dist": total_distance_miles, "retail_price": 0.0, "_is_end": True}

    nodes = [start_node] + sorted(stations_on_route, key=lambda s: s["route_dist"]) + [end_node]
    n = len(nodes)

    # ── DP ───────────────────────────────────────────────────────────────────
    dp   = [INF] * n   # cheapest total cost to reach node i
    prev = [-1]  * n   # predecessor index for path reconstruction

    dp[0] = 0.0

    for i in range(n - 1):
        if dp[i] == INF:
            continue  # unreachable node

        price_i = nodes[i]["retail_price"]

        for j in range(i + 1, n):
            dist = nodes[j]["route_dist"] - nodes[i]["route_dist"]

            if dist > tank_range:
                break  # nodes are sorted, so further nodes are also out of range

            gallons  = dist / mpg
            # Cost of fuel bought at node i to drive to node j.
            # START has price 0 (already have a full tank).
            leg_cost = gallons * price_i

            new_cost = dp[i] + leg_cost
            if new_cost < dp[j]:
                dp[j]   = new_cost
                prev[j] = i

    # ── Check feasibility ────────────────────────────────────────────────────
    if dp[n - 1] == INF:
        logger.error("Route is infeasible: no station sequence covers the full distance.")
        return [], INF

    # ── Reconstruct path ─────────────────────────────────────────────────────
    path = []
    idx  = n - 1
    while idx != -1:
        path.append(idx)
        idx = prev[idx]
    path.reverse()

    # ── Build result ──────────────────────────────────────────────────────────
    fuel_stops: list[dict] = []
    total_cost = 0.0

    for k in range(len(path) - 1):
        i = path[k]
        j = path[k + 1]
        node = nodes[i]

        if node.get("_is_start") or node.get("_is_end"):
            continue  # Skip virtual endpoints

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
