# Migration map

## First phase

| Capability | Shared owner | Status |
| --- | --- | --- |
| 2D grid geometry and route ordering | engine grid/route modules | Complete in `v0.1.0`; both clients use it |
| Automatic direction | engine grid module | Complete in `v0.1.0`; both clients use it |
| WGS84 area and route distance | engine measurements module | Complete in `v0.1.0`; both clients use it |
| Route-level time, photo, and battery estimates | engine statistics module | Complete in `v0.2.0`; both clients use it |
| Versioned aircraft and camera values | engine drone profiles | Complete in `v0.3.0`; both clients use them |
| Flight splitting | engine route module | Split primitives are shared; split policy, recovery, and per-flight results remain in clients |
| Photo/action plan | planned engine result | Pending; KMZ writers still decide actions |
| Recovery and complete mission totals | planned engine result | Pending; current shared statistics are route-only |
| Versioned `plan_2d` contract, warnings, and errors | planned engine API | Pending; Django currently composes a partial adapter result |

The target is for website and plugin adapters to convert UI state to one shared
request and render one shared result. KMZ writers will retain XML packaging but
must stop recalculating routes, splits, actions, and estimates.

## Later phases

- Corridor geometry and routing
- Terrain sampling and terrain-following route changes
- Cross-platform controller delivery where supported
