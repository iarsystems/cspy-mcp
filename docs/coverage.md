# Code coverage

The `coverage_*` tools collect and read code coverage in a debug session, in
CSpyServer2 or in iaride. They require an active debug session, and the
backend offers coverage only once the program is loaded and only when the
session's driver supports it (the simulator does).

```
coverage_get_status()                      → supported, enabled, initialized, session_id
coverage_set_enabled(true)                 → count executions from now on
  ... run the target (debugger_go_and_wait_for_core_state, breakpoints, steps) ...
coverage_get_report()                      → per function, with program totals
coverage_get_report(level="step", match="main.c", uncovered_only=true)
                                           → the statements in main.c that did not (fully) run
coverage_reset()                           → count again from zero
coverage_get_xml()                         → the backend's raw <cpMetaDataInfo> XML
```

Enable coverage before running: executions are counted only while it is on.
The backend saves the on/off setting with the session's settings (the `.dnx`
file), so later sessions of the same program start with it.

## Reading the report

`coverage_get_report` returns `data.program` (the totals) and `data.items` at
the chosen `level`: `"module"`, `"function"` (default) or `"step"`. `match`
keeps items whose module, function or source file contains the text
(case-insensitive); `uncovered_only` keeps those not fully executed;
`max_items` caps the list, with `data.count` and `data.truncated` saying how
many matched.

The counts work at two levels:

- A program, module or function has `total_count` **steps** (statements, or
  parts of one, as the debugger steps through them), of which `exec_count`
  executed fully. `percent` is that ratio.
- A step's own `exec_count`/`total_count` count the machine-code units it is
  made of, so a step can be partly executed: a condition whose branch went
  only one way. Step items say so in `status`: `"executed"`, `"partial"` or
  `"not_executed"`.

A run of the `arm_main` example, for instance, reported 96 of 122 steps
executed: 96 steps `executed`, 9 `partial` and 17 `not_executed`.

`refresh=true` (default) reads the latest counts from the target first. The
first report of a session also builds the program structure, which can take a
moment on a large program.

## Failures

The tools return `ok=false` envelopes. A driver without coverage, or a backend
that has not registered the `codecoverage` service (before the program is
loaded), fails with a message saying so; `coverage_get_status` reports
`supported: false` when the service is there but the driver lacks coverage.

`program.name` is what the backend reports, which in an IDE can be the IDE's
current project rather than the debugged one.
