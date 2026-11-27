# The IDE's logs: the logs tools

`logs_status`, `logs_read` and `logs_wait_for` read what the IDE logs: the
Build window, the Debug Log, Tool Output, Find in Files, ... Each of those is
a *category*, named by the IDE's id: `Log_Build`, `Log_Debug`,
`Log_Tool_Output`, `Log_Find_in_Files`, `Log_CMake_CMSIS`, ...
`project_build` and `project_configure_and_start_debug` also report the
build's warnings and errors from `Log_Build`, in `data.problems`.

These tools do not need a debug session.

## Where the logs come from

The IDE platform's log forwarder posts every log entry to the `LogService`s in
the service registry. The server hosts one (`client.logs` in iar-cspy) and
keeps what arrives, per category, so it can be read back at any time.

- A Thrift-enabled iaride runs a forwarder while remote control is on.
- A managed backend with an IarServiceLauncher (a full IAR installation) gets
  one loaded into the launcher.
- CSpyServer2 alone has none: `logs_status` says so in `data.note`. Its debug
  log is still in `debugger_get_log`.

The forwarder does not replay anything logged before the server listened, so
the server registers at startup, and again before every IDE service call
(after a backend restart, say). `data.listening_since` says from when the
logs are complete. `THRIFT_AUTO_LOG_SERVICE=0` turns this off.

## Reading: pages by stream position

Every entry has a `seq`, its position in the stream of all entries, which
never changes. `logs_read` returns a page of entries, oldest first, and says
where the page is:

```json
"page": {"returned": 100, "total": 412, "before": 0, "after": 312,
         "first_seq": 1803, "last_seq": 1902, "truncated": false}
```

`total` counts the entries that match the filters; `before`/`after` count
those outside the page on either side.

- no `after`/`before`: the newest `limit` entries (100 by default, at most 500).
- `after=page.last_seq`: the next page; also how to follow a log as it grows.
  `page.after == 0` means there is nothing newer.
- `before=page.first_seq`: the previous page.

Positions, not offsets: the logs grow while they are read, and the server
keeps at most 5000 entries per category. A page with very long lines can end
before `limit` (`truncated: true`, at about 20k characters); the next page
starts where it stopped.

Filters: `category`, `session` (`"current"`: since the IDE last restarted that
log, e.g. the last build; `"all"`; or a number), `problems=true` (warnings and
errors only), `min_severity`, `match` (case-insensitive text, or a regex with
`regex=true`). When a filter keeps an entry, the detail lines that follow it
(sub-entries) are kept too.

The severities (`DEBUG`, `USER`, `MINOR_INFO`, `INFO`, `WARNING`, `ERROR`,
`ALERT`, `SUPER`) rank how prominently the IDE shows a line, not how bad it
is. `ALERT` is urgent console text ("Resolving dependencies..."), `SUPER` a
headline or a tool that succeeded ("Build succeeded", the file being
compiled). So `min_severity="WARNING"` also returns those; the problems are
exactly `WARNING` and `ERROR`, which is what `problems=true` selects.

`format="text"` (the default) gives one line per entry: warnings and errors
get `Warning:`/`Error:` unless the text starts with that already, detail lines
are indented, a source position the text does not mention is put in front
(`path:line:col: `), and without a `category` every line starts with
`[Log_...]`. `format="entries"` gives the fields: `path`, `line`, `column`,
`severity`, `is_sub_entry`, `session`, `seq`, ...

```text
logs_read("Log_Build", problems=true)               # the last build's problems
logs_read("Log_Debug", limit=20)                    # the newest debug log lines
logs_read(after=1902)                               # everything new since seq 1902
```

## Waiting

`logs_wait_for(match, category, min_severity, after, timeout_ms)` returns the
first entry that matches, logged after `after` (default: from the call on).
A timeout is `ok: true` with `data.found: false`; `timeout_ms` is at most
60000. `data.cursor` is the position to continue from.

## Build problems

`project_build` takes a position before the build and afterwards puts the
`Log_Build` warnings and errors logged since into `data.problems`:

```json
"problems": {"errors": 1, "warnings": 1,
             "lines": ["/w/main.c:3:1: Error[Pe020]: x undefined", "  in function main", "..."],
             "truncated": false, "after": 1803, "cursor": 1902}
```

At most 50 lines; `logs_read("Log_Build", problems=true,
after=problems.after)` pages through the rest. `errors`/`warnings` count
`ERROR`/`WARNING` entries, not their detail lines. `problems` is null when the
server does not receive the IDE's logs; `output_tail` is there either way.

## Errors

`LOGS_UNAVAILABLE` (retryable): the server is not listening, because no
registry is reachable yet or `THRIFT_AUTO_LOG_SERVICE=0`. `INVALID_ARGUMENT`:
a bad severity, session, regex, position or format.
