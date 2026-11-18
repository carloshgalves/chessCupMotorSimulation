# Progressive competency XP (#64, #191)

The career design uses six attributes on an integer 0–110 scale. Older
0–95 UI proposal is superseded by the 0–110 proposal; neither is a measured
chess rating. Every player starts the competitive rating at 2500. Attribute
vectors are independent of initial rating, and historical reference
profiles remain immutable. Future career instances persist their own vectors.

Version `competency-xp-v1-experimental` is a **proposed**, not yet balanced,
game economy. The owner specified rising marginal effort, not numeric costs.
For a level n in [0,109], the next +1 costs 100 + 2*n + floor(n*n/10) XP.
Each next step costs strictly more than the previous one. At 110 no more
purchases are allowed. Multi-step quotes sum every intermediate price;
there is no flat-rate discount. Example costs: 0→1 = 100; 50→51 = 450;
90→91 = 1090; 109→110 = 1506. XP never changes rating directly.

Implemented: pure integer pricing and read-only quote. NOT implemented:
credit grants, purchase transaction/ledger, UI, persistence, anti-farm rules,
training budgets, changes to search settings, and mapping the competency
increments to statistically measurable chess decisions. Price balance must
be calibrated jointly with the gain rate; changing this schedule later
requires a versioned economic migration. No uncalibrated null attribute
can be purchased. No implication that spending XP improves actual humans.

Before enabling a purchase, the related motor response must be verified.
Use the same matched positions/conditions to test small single-point
increments and larger cumulative increments, check effects on unrelated
competencies, and ensure legal moves and bounded tactical risks. Do not
claim an improved skill or a raised engine rating without evidence.
