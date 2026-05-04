"""Generation runs — HTTP routes, runner, SSE replay (Slice 3a).

Architect §Generation runner pins the design: in-process ``asyncio``
task pool, one ``generation_runs`` row + dense ``generation_run_items``
rows per run, SSE replay sourced from those items. PLAN §Generation
runner durability pins the per-sentence transaction shape and OCC
discipline.
"""
