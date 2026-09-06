# Deterministic vegetation workflow

## Public workflow

1. `world_context` observes the exact allowed world and current selection.
2. `vegetation_catalog` returns the exact server-owned allowlist and hash.
3. `vegetation_plan` validates one polygon-compatible selected closed Shape,
   generates a bounded candidate pool, performs one terrain batch, and stores an
   immutable plan. It does not mutate Workbench.
4. `vegetation_apply` accepts only the stored lowercase SHA-256 `plan_id` and a
   separate canonical non-nil RFC-4122 UUID idempotency key. It never accepts
   client-provided transforms or prefab paths.

Apply never saves the world. Layers are not created. The user manually creates
and selects `MCP_Preview` or `MCP_Vegetation`, manually saves if desired, and
later performs the live one-Ctrl+Z acceptance test.

## Planner algorithm `enfusion-mcp-pcg32-poisson-v1`

The planner uses the reference PCG-XSH-RR 64/32 algorithm with 64-bit state,
32-bit output, multiplier `6364136223846793005`, fixed stream selector 54, and
the reference two-step seed initialization. Public seed is a uint32 and seed 0
is valid. PCG32 is reproducibility machinery, never an operation ID or security
random source.

The generator uses the old state for XSH-RR output and explicit 32/64-bit masks.
The reference `seed=42, stream=54` first outputs are:

```text
a15c02b7 7b47f409 ba1d3330 83d2f293 bfa4784b cbed606e
```

Candidate generation consumes X then Z draws per bounding-box attempt.
Candidate coordinates use unsigned multiply-high mapping into a lower-inclusive,
upper-exclusive box formed by polygon-AABB ∩ terrain-bounds. X/Z then make an
IEEE-754 binary32 round-trip to match Enforce's wire/runtime precision before
duplicate, containment, bounds, spacing, echo, and hash checks. After the one terrain batch, each accepted
placement consumes prefab-ticket, yaw, then scale draws; a fixed scale still
consumes its draw. This exact schedule is part of the algorithm version.

The candidate target is `max(64, count*10)`, capped at 1000. Bounding-box
rejection has a hard 10,000-attempt cap. There is no unbounded loop and no
per-point TCP operation.

## Geometry and terrain

- Wire-canonical coordinates are quantized to integer micrometres using decimal
  round-half-even.
- Polygon predicates and X/Z squared spacing use integers.
- Boundaries count as inside. Exact integer spacing equality passes the primary
  geometry test; a candidate can still be conservatively rejected by the
  additional binary32 handler-emulation check after wire rounding.
- Cardinality is 3–1000 vertices; duplicate/collapsed vertices, collinearity,
  insufficient area, backtracking/overlapping edges, and self-intersection are
  rejected.
- After binary32 conversion and micrometre quantization, the ring is rotated to
  its lexicographically smallest full `(x,y,z)` sequence across both traversal
  directions. Changing only the selected Shape's cyclic start vertex or winding
  therefore preserves its polygon, Shape hash, placements, plan ID, and entity
  namespace.
- Rectangle, triangle, and simple concave polygons are supported. Splines are
  rejected by the bridge's polygon-compatible subtype gate.
- Candidate points must be inside both polygon and terrain bounds.
- Terrain response cardinality/order is fixed to the request. Every echoed X/Z
  is checked against its corresponding candidate; a mismatch rejects that
  candidate as `invalid_terrain_result` rather than trusting or relocating it.
- A hit requires finite terrain Y and all three finite normal components.
- Normal length must be nonzero and within 1% of unit length; the bridge itself
  also normalizes its returned vector.
- Slope is `degrees(atan2(hypot(normalX, normalZ), normalY))`, quantized to a
  microdegree. Equality passes this primary comparison, but the conservative
  binary32 emulation of the handler threshold can still reject it after wire
  rounding.
- Spacing must pass both exact integer squared distance and an emulation of the
  handler's binary32 `dx*dx + dz*dz < spacingSquared` comparison.
- Rejection statistics name candidate-attempt, polygon, bounds, duplicate,
  invalid terrain, no terrain, Y bounds, normal, slope, spacing, unused, and
  accepted counts.
- If 1..`count-1` placements survive, planning succeeds with warning
  `UNDERFILLED`. Zero valid placements returns `NO_VALID_PLACEMENTS` with the
  same `UNDERFILLED` warning and complete rejection statistics, and stores no
  unusable plan. This diagnostic result has no `plan_id` and cannot be applied.

Palette weights are decimal-quantized to positive integer units and normalized
over the complete uint32 ticket space. A single multiply-high ticket chooses an
exact allowlisted resource. Yaw is lower-inclusive/360-exclusive and scale is
bounded by the requested range.

The staged handler writes yaw and scale through the editor source using checked
`WorldEditorAPI.SetVariableValue` calls for `angleY` and `scale` inside the single
entity action, then verifies the observed transform using `GetYawPitchRoll()`.
Source support is documented in `ENFORCE_BRIDGE.md`; compilation and
transform/Undo round trips remain unverified in live Workbench. The mutation
gate remains false.

## Canonical immutable plan

The hashed JSON contains only fixed ASCII keys, exact resource strings, and
integer fixed-point values:

- schema/algorithm and quantization versions;
- exact world, bridge build/protocol, subscene, active layer and terrain bounds;
- selected Shape name/class/hash and world-coordinate polygon;
- seed, requested count, spacing/slope/scale constraints, and provisional
  terrain-Y epsilon (50,000 µm / 0.05 m);
- exact catalog version/hash and normalized ordered palette;
- ordered final placement index, prefab, position µm, yaw µdegrees, scale ppm;
- deterministic rejection statistics and target layer.

Serialization is UTF-8, sorted-key, compact project-canonical JSON with NaN,
Infinity, duplicate keys, and lone Unicode surrogates rejected. It is not
claimed to be full RFC 8785/JCS. `plan_id` is lowercase SHA-256 of these bytes.

`created_at`, `expires_at`, plan/operation state, and entity names are excluded
from the hash. Names are derived afterward as:

```text
EnfusionMCP_<full 64-hex plan_id>_<decimal placement index>
```

This avoids a hash/name cycle while keeping stable reconciliation identity.

TTL is 30 minutes and immutable. Repeating identical input returns the original
ledger row and never renews expiry or resets applied/unknown/undone state. An
expired identical plan stays expired; a new workflow must deliberately change a
hashed input such as seed.

## Apply and reconciliation

Before the first mutation request, SQLite binds `plan_id` one-to-one to the
canonical idempotency UUID and uses that same UUID as `operation_id`. The
operation is committed as `SENDING`, then exactly one mutation call is made
under the exclusive target lock.

Before any context request, the service validates the exact palette allowlist,
weights, and numeric precision as well as the public input schema. Immediately
before committing SENDING, the ledger atomically checks TTL and unresolved
operations for the whole endpoint, including different plans.

Any post-send transport/response uncertainty becomes `UNKNOWN`. A repeated
call with the same plan/key sends only `mode=reconcile`; it never sends create
again. A different key conflicts. Reconciliation classifies:

- `COMPLETE`: every deterministic entity matches → confirm `APPLIED` only when
  compatible with durable history;
- `PARTIAL`: some but not all match → `PARTIAL_OPERATION`, create nothing;
- `CHANGED`: a name exists with differing prefab/layer/transform →
  `ENTITY_CONFLICT`;
- `NONE`: after confirmed application → explicit `UNDONE`; after an uncertain
  application → remain `UNKNOWN` and keep the target blocked. Never recreate
  the old operation.

Rollback status is recorded as verified only when every created entity deletion
and absence check plus action end succeeds. Otherwise state is
`ROLLBACK_FAILED` or `UNKNOWN`; success is never inferred.

The current production catalog is empty/unverified, so real plan/apply is
intentionally fail-closed at Checkpoint C. Planner and coordinator properties
are demonstrated with synthetic exact catalogs and fake bridges only.
