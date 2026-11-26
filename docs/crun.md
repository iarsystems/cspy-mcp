# C-RUN: rules and messages

The `crun_*` tools drive C-RUN runtime checking in a debug session, in
CSpyServer2 or in iaride. They require an active debug session, like
`breakpoints_*` and `memory_*`.

## Rules

```
crun_list_rules()                          → the rules as the IDE's C-RUN Message Rules window shows them
crun_add_rule("AssertFailure")             → a rule for that message, anywhere
crun_add_rule("AssertFailure", file="/abs/main.c")
crun_add_rule("AssertFailure", file="/abs/main.c", start_line=10, start_col=1)
crun_get_default_action()                  → "STOP_AND_LOG" | "LOG" | "IGNORE"
crun_set_default_action("LOG")
```

`crun_list_rules` reads the `WIN_C_RUN_MESSAGE_RULES` list window, so each rule
has `index` and `cells` keyed by the window's column titles (Check, Source
File, Action). The last rule is the default one.

A new rule goes ahead of the existing ones and has the action `LOG`. The
default action applies to messages no rule matches.

A session without C-RUN fails with `error.code=CRUN_NOT_INITIALIZED`.

## Messages

`crun_get_messages()` returns the live runtime-check messages. It registers
this server as `crun.display` on first use and keeps what the backend pushes,
per namespace (the partner that sent them; `""` for an IDE). Only messages
pushed after that are seen, so call it before running the target.
`crun_clear_messages([namespace])` forgets them.

```
crun_get_messages()                 → {"receiving": true, "namespaces": ["core0"], "count": 2,
                                       "messages": [{"namespace": "core0", "name": "Division by zero",
                                                     "text": ..., "call_stack": [...], "sub_messages": [...]}]}
crun_get_messages(namespace="core0", max_messages=50)
```

If an IDE already registered `crun.display` (its own C-RUN Messages window),
`data.receiving` is false. Read that window instead:

```
listwindow_get_rows("WIN_C_RUN_MESSAGES")   → message text, source file, PC, core, nested call-stack rows
```

`WIN_C_RUN_MESSAGES` and `WIN_C_RUN_MESSAGE_RULES` are window ids, served by an
IDE through its `listwindow.snapshot` service.
