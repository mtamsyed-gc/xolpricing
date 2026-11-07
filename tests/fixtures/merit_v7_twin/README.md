# merit_v7_twin (frozen)

The Merit sample that the stored Designer run of the Merit route used (`tests/reconciliation/designer_twin/merit`,
Designer 2026.2 on the owner's laptop, 2026-10-22; files as rebuilt for Merit v7.6.0, contract IP4-10, on 2026-10-28).
`tests/test_designer_twin.py` runs the simulator on these files and compares with that Designer run. Do not rebuild
this folder: the contract test's sample is `tests/fixtures/merit_v7`, which follows Merit's current outputs. Replace
this folder only together with a new Designer run of `tools/check_merit.bat` stored in `designer_twin/merit`.
