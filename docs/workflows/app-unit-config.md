# App Unit Config Operations (S-009 PR #2)

## How it works

The App unit lets you enable/disable individual strategies (and other
list-based unit entries) by editing `config/units.yaml` and calling
`Coordinator.reload_units()`.  No restart required.

```
Edit config/units.yaml  (set enabled: false on a strategy)
         ↓
Coordinator.reload_units()
         ↓
_cfg refreshed in-process; list_strategies() reflects the change
         ↓
Alert pushed → dashboards queue (source="app")
```

## Enabling / disabling a strategy

In `config/units.yaml`, set `enabled: true` or `enabled: false` on any
strategy entry:

```yaml
units:
  strategies:
    - name: breakout_confirmation
      enabled: false    # ← set this to disable
      service: ict-trader-breakout
      ...
    - name: vwap
      enabled: true     # ← default, or omit entirely
      ...
```

Entries with **no** `enabled` field are treated as enabled.

## Triggering a reload

**From Python:**
```python
from src.core.coordinator import Coordinator

coord = Coordinator()
result = coord.reload_units()
# result = {
#   "reloaded": True,
#   "units_path": "config/units.yaml",
#   "strategy_count": 4,
#   "enabled_strategies": ["ict", "vwap", "killzone"],
# }
```

**From Telegram** (wire the `/reload_units` command to call this):
```
/reload_units
→ ✅ Units reloaded: 3 enabled strategies
```

## Reading enabled units programmatically

```python
from src.units import load_enabled_units, list_enabled_strategies

# Full filtered config dict
units = load_enabled_units("config/units.yaml")
enabled_strats = units["strategies"]   # only enabled entries

# Just names
names = list_enabled_strategies()  # ["ict", "vwap", "killzone"]
```

## Rules

- Never modify `config/units.yaml` from runtime code — only human or App config flow
- `reload_units()` is safe to call at any time; it does not interrupt in-flight trades
- After reload, `list_strategies()` reflects the new state; existing `OrderPackage` objects are unaffected
- Disabling a strategy prevents it from appearing in `dashboard_stats()` strategy rows but does not cancel open positions
