# Planner constraints and limitations

`deadline` means the activity must finish by that instant, inclusive. `latest_end`
is an additional hard finish limit; the earlier bound wins. `earliest_start` is a
hard start bound. Priority, importance, energy and goal scores cannot override them.
Fixed/protected activities obey the same constraints as flexible activities.

Ordinary overdue work remains unscheduled. `recover_missed_deadline: true` explicitly
allows recovery work only when the deadline predates the planning window. It never
relaxes a future deadline or `latest_end`. The original deadline remains in the
output and explanation identifies late recovery, without claiming timely completion.
Only a trusted user/application choice should set that flag. Existing captures default
to false; producers that want overdue recovery must opt in explicitly.

Dependencies mean finish-to-start precedence in elapsed time: B.start >= A.end.
Every dependency must actually be scheduled. Travel reservations cannot overlap
other activities, and durations/travel are computed in UTC across DST folds/gaps.
Fixed successors constrain the latest possible finish of their flexible prerequisites.
If a required fixed event has a missing, unscheduled or reversed prerequisite, the
planner returns an empty infeasible proposal rather than showing an invalid schedule.

The algorithm remains greedy. A partial/infeasible result means this algorithm did
not find a complete valid placement; it is not a mathematical proof that no complete
schedule exists. It may fail to arrange long prerequisite chains before fixed events
even when moving earlier choices would work. A future bounded global optimizer must
preserve these hard constraints, deterministic results and auditable explanations.

Regression coverage: `tests/test_planner_constraints.py` tests the reported bugs,
fixed constraints, missed-deadline policy, impossible prerequisites, input permutation
invariance across generated small dependency chains, and DST travel. This is not a
claim of exhaustive fuzzing or global optimality.
