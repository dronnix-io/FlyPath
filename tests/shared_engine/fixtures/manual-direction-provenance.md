# Manual direction provenance

These invariants formalize the existing plugin implementation: direction is
counterclockwise from local UTM grid North. The current plugin tooltip describes
clockwise-from-North behavior and is incorrect. Because survey lines are
bidirectional, a strip's reverse bearing is equivalent modulo 180 degrees.

The 70-degree expected geographic bearing is stated only for a fixture near the
UTM central meridian. Elsewhere, grid convergence changes the geographic
bearing; the shared engine must return that bearing rather than relabeling the
input grid angle.

The fixture checks interpretation only. Full route coordinates, strip counts,
and geographic reference values will be added after independent verification;
they must not be copied blindly from either existing engine.
