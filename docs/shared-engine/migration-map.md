# Migration map

## First phase

| Current plugin responsibility | Current website responsibility | Shared-engine owner |
| --- | --- | --- |
| `grid_planner.generate_flight_grid` | `mission-route.planGeographicMission` | 2D geometry and route |
| `grid_planner.find_optimal_direction` | `mission-route.findBestDirection` | Auto direction |
| `grid_route` and `split_waypoints` | planner-state route splitting | Route ordering and flights |
| dialog statistics methods | main/mission-route estimates | Measurements and estimates |
| hardware drone JSON | database drone profile | Versioned drone profile |
| WPML action decisions | WPML action decisions | Photo/action plan |

Website and plugin adapters convert UI state to the shared request and render
the shared result. Existing KMZ writers retain XML packaging but serialize the
shared route, flights, and actions without recalculation.

## Later phases

- Corridor geometry and routing
- Terrain sampling and terrain-following route changes
- Cross-platform controller delivery where supported
