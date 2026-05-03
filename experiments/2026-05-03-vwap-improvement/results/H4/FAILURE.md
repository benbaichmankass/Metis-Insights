```
Traceback (most recent call last):
  File "/home/runner/work/ict-trading-bot/ict-trading-bot/scripts/training/run_experiment.py", line 33, in _safe_run
    out = fn(ctx)
          ^^^^^^^
  File "/home/runner/work/ict-trading-bot/ict-trading-bot/experiments/2026-05-03-vwap-improvement/hypotheses.py", line 251, in H4
    c5["anchored_vwap"] = c5["anchored_vwap"].astype(float)
                          ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/opt/hostedtoolcache/Python/3.12.13/x64/lib/python3.12/site-packages/pandas/core/generic.py", line 6541, in astype
    new_data = self._mgr.astype(dtype=dtype, errors=errors)
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/opt/hostedtoolcache/Python/3.12.13/x64/lib/python3.12/site-packages/pandas/core/internals/managers.py", line 611, in astype
    return self.apply("astype", dtype=dtype, errors=errors)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/opt/hostedtoolcache/Python/3.12.13/x64/lib/python3.12/site-packages/pandas/core/internals/managers.py", line 442, in apply
    applied = getattr(b, f)(**kwargs)
              ^^^^^^^^^^^^^^^^^^^^^^^
  File "/opt/hostedtoolcache/Python/3.12.13/x64/lib/python3.12/site-packages/pandas/core/internals/blocks.py", line 607, in astype
    new_values = astype_array_safe(values, dtype, errors=errors)
                 ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/opt/hostedtoolcache/Python/3.12.13/x64/lib/python3.12/site-packages/pandas/core/dtypes/astype.py", line 240, in astype_array_safe
    new_values = astype_array(values, dtype, copy=copy)
                 ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/opt/hostedtoolcache/Python/3.12.13/x64/lib/python3.12/site-packages/pandas/core/dtypes/astype.py", line 185, in astype_array
    values = _astype_nansafe(values, dtype, copy=copy)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/opt/hostedtoolcache/Python/3.12.13/x64/lib/python3.12/site-packages/pandas/core/dtypes/astype.py", line 134, in _astype_nansafe
    return arr.astype(dtype, copy=True)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^
TypeError: float() argument must be a string or a real number, not 'NAType'

```