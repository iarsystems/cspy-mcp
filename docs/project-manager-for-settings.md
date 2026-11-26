# Using ProjectManager for project settings

> ## ⚠ NONE OF THIS IS A CONTRACT — IT CAN CHANGE AT ANY MOMENT, WITHOUT WARNING
>
> **Every option id, value encoding, element id, category name, error string and
> JSON shape in this document is a snapshot of one toolchain on one version.**
> They are internal implementation details of IAR Embedded Workbench, not a
> published API. They change between products, between target architectures,
> between releases — and nothing announces it when they do. Upstream says as
> much of the presentation document: it "is not meant to be accessed directly by
> clients".
>
> **Therefore: never hardcode anything from this document.** Discover it at
> runtime, every time:
>
> 1. **read** the options (`GetOptionsForConfiguration`) for the project in
>    front of you;
> 2. **match** on what came back — ids, `elements`, `type`, `enabled`;
> 3. **write** back only a value you just saw (an element `id`), and
> 4. **re-read** `Apply*`'s return value to learn what was actually set.
>
> Treat every table below as *an illustration of the shape* and a starting point
> for matching — never as a lookup table to copy values out of. Code that
> hardcodes `"DLibFull"`, `"RuntimeChecking.@GenRtcEnable"` or `"IJET_ID"` is
> code that will break silently on a customer's installation. Code that reads,
> matches and writes back will not.

Background for implementing MCP tools that read and write project options —
changing the target device, the runtime library and its configuration file, the
compiler optimization level, the debug probe, enabling C-RUN, and so on.

**The short version:** use ProjectManager, not OptionsService. Everything you
need is already on the ProjectManager Thrift interface, it is already reachable
from this server today through `projectmanager_call`, and it works against a
Thrift-enabled `iaride` as well as against a managed `IarServiceLauncher`.

This document is self-contained: the interface, the data shapes, the option ids
and the value encodings are all reproduced here. It draws on docs in the IAR
Embedded Workbench monorepo that you may not have access to; those are listed at
the end for anyone who does.

The value encodings, the error shapes and the registry contents below were
checked against a live Thrift-enabled `iaride` (IAR Embedded Workbench 9.7.0.0,
ARM toolchain) on an ARM project; passages marked *verified* come from that
session rather than from upstream prose. Option ids and encodings do vary by
toolchain and version, so the standing advice — read first, write back an id you
saw — holds regardless.

## Contents

1. [Why not OptionsService](#why-not-optionsservice)
2. [The interface](#the-interface)
3. [How options are modelled](#how-options-are-modelled)
4. [Discovering options, and the presentation JSON](#discovering-options-and-the-presentation-json)
5. [Worked examples](#worked-examples)
6. [Transactions: configuration working copies](#transactions-configuration-working-copies)
7. [Four gotchas](#four-gotchas)
8. [Behaviour against a live iaride](#behaviour-against-a-live-iaride)
9. [Build actions](#build-actions)
10. [Where new tools belong](#where-new-tools-belong)
11. [Further reading](#further-reading)

## Why not OptionsService

OptionsService is the *presentation* layer, not the capability layer. It serves
the category tree and the option tree as DdGui XML — widget types, labels,
choice lists, help text — which is what you need to **render an options dialog**.
It also fans a single ProjectManager option out into many editable sub-options:
build actions become `1 + N + 5N` nodes for N actions.

None of that is needed to set an option by id. The `options.py` module docstring
in this repo already says so, and so does the upstream documentation: the
presentation JSON "is not meant to be accessed directly by clients", and the
category list "is not necessary to be able to address individual options".

There is also a hard practical reason. OptionsService is **not registered on the
remote-control registry** of a running GUI `iaride` — only the headless
`IarServiceLauncher` path hosts it. ProjectManager *is* registered there. So
against a live IDE, ProjectManager is the only one of the two you can reach.

*Verified*: the registry of a running `iaride` held exactly `breakpoints`,
`codecoverage`, `com.iar.thrift.service.projectmanager`, `crun.backend`,
`debugger`, `debugger.contextmanager`, `debugger.memory`, `disassembly`,
`listwindow.snapshot` and `sourcelookup`. No `com.iar.OptionsService`, and no
`com.iar.thrift.service.manager` either — which means `ide_services_ensure(
"options")` cannot start it on demand there, and fails with *"not registered,
and the backend has no com.iar.thrift.service.manager service to start it
with"*. That is not a misconfiguration to fix; it is the shape of the
remote-control registry, and the reason the `options_*` tools are simply
unavailable against a GUI IDE.

Two further traits make OptionsService a poor fit for automation:

- it holds **one session at a time**, and creating a session silently discards
  any active one — including the one the IDE's own options dialog is holding;
- its start/stop is a boolean flag, not a refcount, so whoever stops it first
  tears it down for everyone.

## The interface

| Method | Purpose |
|---|---|
| `GetOptionsForConfiguration(prj, configName, optionIds = [])` | read options for a configuration; empty id list means **all** |
| `GetOptionsForNode(prj, node, configName, optionIds = [])` | same, for a file or group node |
| `GetOptionsForProject(prj, optionIds = [])` | project-global options (e.g. CMake) |
| `VerifyOptionsForConfiguration(prj, configName, optionsToSet)` | resolve final values **without** changing the project |
| `VerifyOptionsForNode(prj, node, configName, optionsToSet)` | same, for a node |
| `ApplyOptionsForConfiguration(prj, configName, optionsToSet)` | write; returns what was **actually** set |
| `ApplyOptionsForNode(prj, node, configName, optionsToSet)` | same, for a node |
| `RemoveOptionsForNode(prj, node, configName)` | drop a node's local overrides |
| `CreateConfigWorkingCopy(prj, originalConfigName)` | begin a transaction; returns a working copy id |
| `CreateConfigNodeWorkingCopy(prj, originalConfigName, node)` | node-scoped variant |
| `ApplyConfigWorkingCopy(prj, workingCopyId)` | commit the copy over its original |
| `DiscardConfigWorkingCopy(prj, workingCopyId)` | throw the copy away |

Supporting calls: `GetConfigurations(prj)` lists a project's configurations,
`GetLoadedProjects()` lists the projects currently open in the workspace, and
`GetProject(filePath)` resolves a path to a `ProjectContext` — **it throws if the
project is not already loaded**, it will not open a file from disk.

For discovery and presentation, covered in
[its own section](#discovering-options-and-the-presentation-json):
`GetOptionCategories(prj, configName)` and `GetOptionCategoriesForNode(...)`
return `OptionCategory` records of `{id, optionIds}`;
`GetPresentationForOptionsAsJson(toolchainId)` returns the resolved presentation
document; `GetToolchains()` supplies the `toolchainId`; and
`ExpandArgVars(input, prj, configName, throwOnFailure)` resolves `$TOOLKIT_DIR$`
and friends.

`prj` is a `ProjectContext`, which for these purposes is just
`{"filename": "/abs/path/to/Project.ewp"}`.

Each option comes back as an `OptionDescription`:

| Field | Type | Meaning |
|---|---|---|
| `id` | string | unique option id |
| `value` | string | the value **as persisted in the project file** |
| `type` | `OptionType` | see below |
| `elements` | list | choices/sub-elements, each with `id`, `label`, `enabled`, `data`, `description` |
| `enabled` | bool | whether the IDE would show it enabled |
| `visible` | bool | whether the IDE would show it |
| `canBeLocal` | bool | whether a node may override it locally |

`OptionType` is `Check=0, Edit=1, EditB=2, List=3, Radio=4, CheckList=5,
BuildActions=6, DeviceSelection=7, CMSISDevice=8, CMakeSettings=9, PathList=10,
EditHexInteger=11, JsonKeyValueMap=12, CheckTree=13`.

## How options are modelled

Options are a **flat list keyed by a unique id string**. There are no tool
objects or setting containers to navigate. An id carries its settings owner as a
prefix: `General.`, `ICCARM.`, `ILINK.`, `C-SPY.`, `RuntimeChecking.`, …

Scope is one of three, and they are separate calls:

- **per configuration** — "Debug", "Release";
- **per node within a configuration** — a file or group overriding the
  configuration, which is what `canBeLocal` reports;
- **per project**, global across configurations (CMake settings, mostly).

One flat list covers **both build and debug options**, even though they persist
to different files on disk (`.ewp` for build, `.ewd` for debugger, `.ewt` for
C-STAT). You do not need to care which file an option lives in.

`value` is always a string, but **not always the project file's own encoding** —
for `List` and `Radio` the service translates, and that distinction is the one
most likely to cost you a call:

| Option kind | Encoding | Example |
|---|---|---|
| `Check` (boolean) | `"On"`/`"Off"` **or** `"true"`/`"false"` — per option, see [below](#checkboxes-have-no-domain-and-two-spellings) | `ICCARM.@IExtraOptionsCheck` = `"On"`; `C-STAT.AnalyzeTimeoutEnabled` = `"true"` |
| `List` / `Radio` | the **`id` of the chosen entry in `elements`** | `General.@GRuntimeLibSelect` = `"DLibFull"`; `ICCARM.@CCOptLevel` = `"3"` |
| `Edit`, paths | the literal string | `General.@RTConfigPath2` = `"$TOOLKIT_DIR$\inc\c\DLib_Config_Full.h"` |
| `DeviceSelection` | tab-separated device record | see [the device example](#change-the-target-device) |

**`List`/`Radio` values are element ids, not positional indices.** Some element
ids happen to *be* digits (`ICCARM.@CCOptLevel` has elements `"0"`…`"3"`), which
makes the encoding look positional — it is not. Verified against EWARM 9.7.0:

- `General.@GRuntimeLibSelect` (a `List`) holds `"DLibFull"`, and writing `"2"`
  is **rejected**: *"List option state was not found or was disabled: 2"*. Its
  legal values are the element ids `None`, `DLibNormal`, `DLibFull`,
  `DLibCustom`, `DLibCpp`. The same project's `.ewp` stores this option as
  `<state>1</state>` — so the file's index and the API's value genuinely differ,
  and reading the `.ewp` will mislead you about what to write.
- `General.@OGCoreOrChip` (a `Radio`) holds `"Core"`. It accepts *either* the
  index `"1"` or the tag `"Chip"`, and normalizes both to `"Chip"` on the way
  back out.
- `ICCARM.@CCOptLevel` (a `Radio`) accepts `"3"` because `"3"` is an element id;
  writing the label `"High"` is rejected (*"Unrecognized radio option tag
  value"*).

So the one rule that always holds: **read the option, take the `id` of the
element you want, write that.** Never invent a value, and never translate a
`.ewp` `<state>` number yourself.

Argument variables such as `$TOOLKIT_DIR$` and `$PROJ_DIR$` are stored
unexpanded; write them back the same way.

### Querying an option's legal values

**`elements` *is* the domain query.** There is no separate "get legal values"
RPC: the choices arrive with every `GetOptionsFor*` read, in the
`OptionDescription.elements` list. Each element carries `id` (the value you
write), `label` (what to show a user), `enabled` (whether the IDE would offer
it), `data`, and nested `elements` for tree-shaped options.

Only the list-like types populate it. *Verified* over all 2082 options of one
ARM project's `Debug` configuration:

| Type | Options | With `elements` |
|---|---|---|
| `List` (3) | 51 | 48 |
| `Radio` (4) | 36 | 36 |
| `CheckList` (5) | 2 | 2 |
| `CheckTree` (13) | 1 | 1 |
| `DeviceSelection` (7) | 2 | 2 |
| `Check` (0) | 1821 | **0** |
| `Edit`, `EditB`, `BuildActions`, `JsonKeyValueMap` | 169 | **0** |

So: for `List`, `Radio`, `CheckList`, `CheckTree` and `DeviceSelection`, read
the option and offer its `elements`. For `Edit`-like options there is nothing to
query — the domain is free text, and the only validation is what `Verify*` tells
you. Note three `List` options came back with *no* elements, so handle an empty
`elements` on a list-like option rather than assuming it is populated.

`CheckTree` nests: `C-STAT.CStatChecks` has six top-level elements (CERT,
MISRA C++:2008, MISRA C++:2023, MISRA C:2004, MISRA C:2012/2023, CWE) carrying
13, 50, 54, 19, 24 and 7 child elements respectively.

#### Checkboxes have no domain, and two spellings

A `Check` option carries **no elements at all** — the domain is implicitly
boolean. But you cannot assume `"On"`/`"Off"`, because two families coexist and
each option keeps its own (*verified*):

| Spelling | Count | Where |
|---|---|---|
| `"On"` / `"Off"` | 339 | `General`, `ICCARM`, `ILINK`, `RuntimeChecking`, `C-SPY`, `AARM`, and every probe category |
| `"true"` / `"false"` | 1482 | `C-STAT` (1480), `CONFIGFILEDEBUG`, `generalProjectSettings` |

The service converts on write and answers in the option's own family: writing
`"On"` to `C-STAT.AnalyzeTimeoutEnabled` reads back `"true"`, and writing
`"true"` to `General.@OGBufferedTerminalOutput` reads back `"On"`. Echo back
the form you read, or simply trust `Apply*`'s return value.

> **⚠ A bad boolean does not fail — it silently turns the option off.**
> *Verified*: `"on"`, `"1"` and `"true"` all set on; `"Off"`, `"OFF"`, `"0"` and
> `"false"` set off; and `"Yes"`, `"Nonsense"` and `""` are **accepted without
> error and resolve to off**. Unlike `List`/`Radio`, which reject an unknown
> value loudly, a typo in a checkbox quietly disables the check you meant to
> enable. Validate booleans yourself before writing them — do not rely on the
> service to catch them.

#### `CheckList` is a fixed-width bitmask

A `CheckList` value is **not** a list of selected ids. It is a string of one
character per element, in element order. *Verified* on `ICCARM.@CCAllowList`,
which has 8 elements and the value `"00000000"`: `"10000000"` and `"11000000"`
are accepted, while a 7- or 9-character string is rejected outright.

**Bit *N* is the element at position *N* in `elements` — not the element whose
`id` is `N`.** The distinction is real, because the ids are not in order:
`ICCARM.@CCAllowList` returns them as `0, 1, 2, 3, 6, 4, 5, 7`. Index by
position and ignore the `id` when building a mask.

*Verified* by reading back the actual compiler command line (see
[Checking what a change really did](#checking-what-a-change-really-did)) with
optimization raised to High so the checklist binds:

| Mask | Position 4 holds | Id `4` holds | Flag that disappeared | Verdict |
|---|---|---|---|---|
| `"00001000"` | `id 6` tbaa | clustering | `--no_tbaa` | **positional** |
| `"00000100"` | `id 4` clustering | scheduling | `--no_clustering` | **positional** |

> **A mask write can stick and still do nothing.** The checklist is inert at low
> optimization: at `-Ol` every element reports `enabled=False`, and the compiler
> emits `--no_cse --no_unroll --no_inline --no_code_motion --no_tbaa
> --no_clustering --no_scheduling` regardless of the mask. *Verified*: writing
> `"00001000"` there is accepted **and reads back as `"00001000"`**, yet the
> command line does not change at all. So a successful write and a correct
> readback together still do not prove an effect — check `enabled` on the
> elements, and for anything load-bearing check the command line.

At `-Oh` the same option defaults to `"11111110"`: seven transformations on,
with position 7 (vectorization) off *and* `enabled=False` even at high
optimization.

### Finding the id you want

**Always discover the exact id by reading first.** Call
`GetOptionsForConfiguration` with an empty id list, which returns every option
for that configuration, and match on the result. Two spellings are in
circulation for some options — a bare form (`ICCARM.CCOptLevel`) and an
`@`-prefixed form (`ICCARM.@CCOptLevel`) — so the authoritative spelling is
whatever the read gives you for the project in front of you. Do not hardcode an
id you have not seen come back from a read.

The ids below are real and are the ones you will usually meet on an ARM project,
but treat them as a starting point for matching, not as a contract.

| What a user asks for | Option id | Values |
|---|---|---|
| Target device | `General.@OGChipSelectEditMenu` | tab-separated device record; read `elements` |
| …its companion | `General.@OGCoreOrChip` | `"Core"` / `"Chip"` / `"Custom"` (index `"1"` also accepted, normalized to `"Chip"`) |
| Runtime library | `General.@GRuntimeLibSelect` | `"None"`, `"DLibNormal"`, `"DLibFull"`, `"DLibCustom"`, `"DLibCpp"` — **not** `"0"`/`"1"`/`"2"` |
| Library configuration file | `General.@RTConfigPath2` | path, e.g. `$TOOLKIT_DIR$\inc\c\DLib_Config_Full.h` |
| Compiler optimization level | `ICCARM.@CCOptLevel` | `"0"` None … `"3"` High (element ids that happen to be digits) |
| Optimization strategy | `ICCARM.@CCOptStrategy` | index |
| Debug probe / driver | `C-SPY.@OCDynDriverList` | `ARMSIM_ID`, `IJET_ID`, `CMSISDAP_ID`, `JLINK_ID`, … |
| Enable C-RUN | `RuntimeChecking.@GenRtcEnable` | `"On"` / `"Off"` |
| C-RUN: arithmetic overflow | `RuntimeChecking.@GenRtcIntOverflow` | `"On"` / `"Off"` |
| C-RUN: division by zero | `RuntimeChecking.@GenRtcDivByZero` | `"On"` / `"Off"` |
| C-RUN: bounds checking | `RuntimeChecking.@GenRtcEnableBoundsChecking` | `"On"` / `"Off"` |
| C-RUN: unhandled case | `RuntimeChecking.@GenRtcUnhandledCase` | `"On"` / `"Off"` |
| Extra compiler options | `ICCARM.@IExtraOptions` (+ `…Check` to enable) | text / `"On"` |

For `List`, `Radio` and `DeviceSelection` options the legal choices come back in
the `elements` list of the `OptionDescription`, each element carrying an `id`
(the value to write) and a `label` to show. Read the option, present the
`label`s, write the chosen element's `id` — that is the general recipe and it
avoids hardcoding value tables. Note that `label` is often empty, in which case
the `id` is the only human-readable handle you have; the
[presentation JSON](#3-fetch-the-presentation-json-only-if-you-need-labels-ordering-layout)
supplies proper titles.

## Discovering options, and the presentation JSON

Three levels of discovery, cheapest first. Most tools only ever need the first.

### 1. Read the options (no extra calls, no files)

`GetOptionsForConfiguration` with an empty id list returns every option, each
with its `type`, current `value`, `enabled`/`visible` flags and — for `List`,
`Radio`, `CheckList`, `CheckTree` and `DeviceSelection` — an `elements` list
giving the legal choices. That is enough to present a choice and set a legal
value without any hardcoded table. Prefix-match the ids (`RuntimeChecking.`,
`ICCARM.`) to group them.

See [Querying an option's legal values](#querying-an-options-legal-values) for
which types populate `elements` (checkboxes do not), how to read a `CheckList`
bitmask, and why `label` is frequently empty on `List`/`Radio` — in which case
the [presentation JSON](#3-fetch-the-presentation-json-only-if-you-need-labels-ordering-layout)
is where the human-readable titles live.

### 2. Ask for the categories (one extra call, no files)

```python
projectmanager_call(
    method="GetOptionCategories",
    args_json='[{"filename": "/abs/path/Project.ewp"}, "Debug"]',
)
```

Returns a list of `OptionCategory`, each `{id, optionIds}` — a category id plus
the ids of the options in it. This is the grouping the IDE's options dialog uses
down its left-hand side, available without touching any file.
`GetOptionCategoriesForNode(prj, configName, node)` is the node-scoped variant,
which omits categories that do not apply (a plain text file only has the custom
build category).

For "enable C-RUN"-style requests this is usually all the structure you need:
find the category, take its `optionIds`, read those options.

### 3. Fetch the presentation JSON (only if you need labels, ordering, layout)

The presentation is what turns a flat option list into a dialog: page and group
structure, display order, labels and help text. Get it from the API rather than
from disk — the API resolves cross-file includes for you, so you receive one
complete document:

```python
toolchains = projectmanager_call(method="GetToolchains", args_json="[]")
# each entry is {id, name, tools, ...}; the id is persisted in the project
presentation = projectmanager_call(
    method="GetPresentationForOptionsAsJson",
    args_json='["ARM"]',
)
```

Upstream notes that this is "not meant to be accessed directly by clients" —
meaning it is a GUI-building interface, not a stable contract. Treat its shape
as informational, and prefer levels 1 and 2 for anything load-bearing.

### Where the JSON lives in an installed EW

If you do want to read it from disk — to browse ids offline, or to understand a
category's structure — in a **full Embedded Workbench installation**:

| What | Path |
|---|---|
| The toolchain's own presentation | `<install>/<target>/config/option-presentation.json` |
| Localized variant | `<install>/<target>/config/option-presentation-en-gb.json` |
| Shared parts, included by the above | `<install>/common/config/option-presentation/*.json` |

`<target>` is `arm`, `riscv`, `rx`, … The toolchain file pulls in the shared
parts with include objects that use the `$EW_DIR$` argument variable, which
expands to the installation root:

```json
{ "type": "include", "path": "$EW_DIR$/common/config/option-presentation/C-STAT.json" }
```

so a shared file such as `RuntimeChecking.json` (the C-RUN options) or
`ILINK-ILINK-OPTIMIZATIONS.json` is found under
`<install>/common/config/option-presentation/`. The shared parts carry no
`category` field of their own, so they can be included into any category node.

**Caveat, verified:** a *build-tools-only* installation does not ship these
files. On `bxarm-9.60.2` the install contains only `arm/`, `common/` and a
README; there is no `common/config` directory and no `option-presentation*`
anywhere in the tree. The presentation is an IDE concern and ships with the full
Embedded Workbench. So any code path that reads these files must degrade
gracefully — which is another reason to prefer discovery levels 1 and 2, both of
which work against any installation the ProjectManager service can run on.

### Expanding argument variables

Option values keep `$TOOLKIT_DIR$`, `$PROJ_DIR$`, `$EW_DIR$` and friends
unexpanded. To resolve one to a real path:

```python
projectmanager_call(
    method="ExpandArgVars",
    args_json='''["$TOOLKIT_DIR$/inc/c/DLib_Config_Full.h",
                  {"filename": "/abs/path/Project.ewp"}, "Debug", true]''',
)
```

The last argument is `throwOnFailure`. Write values back in their unexpanded
form; expand only for display or for your own file access.

## Worked examples

All of these work **today** through `projectmanager_call`
(`src/iar_cspy_mcp/tools/project.py`), a generic escape hatch onto any
ProjectManager RPC that coerces JSON objects into the Thrift structs the method
expects, matched by field name. Use it to spike behaviour before designing typed
tools.

If a call cannot reach the service, `ide_services_status()` reports which
services the registry actually holds.

### Read everything, to discover ids

```python
projectmanager_call(
    method="GetOptionsForConfiguration",
    args_json='[{"filename": "/abs/path/Project.ewp"}, "Debug", []]',
)
```

Returns a list of `OptionDescription`. Filter it client-side, e.g. everything
C-RUN:

```python
[o for o in result if o["id"].startswith("RuntimeChecking.")]
```

### Read just the options you care about

```python
projectmanager_call(
    method="GetOptionsForConfiguration",
    args_json='''[{"filename": "/abs/path/Project.ewp"}, "Debug",
                  ["ICCARM.@CCOptLevel", "General.@GRuntimeLibSelect"]]''',
)
```

### Set the compiler optimization level to High

```python
projectmanager_call(
    method="ApplyOptionsForConfiguration",
    args_json='''[{"filename": "/abs/path/Project.ewp"}, "Debug",
                  [{"id": "ICCARM.@CCOptLevel", "value": "3"}]]''',
)
```

The return value lists the options actually set and the values they ended up
with — read it, do not assume (see [gotcha 2](#2-apply-returns-what-was-actually-set)).

### Change the runtime library and its configuration file

These two belong together: selecting the Full library usually means pointing at
the matching `DLib_Config_Full.h`.

```python
projectmanager_call(
    method="ApplyOptionsForConfiguration",
    args_json='''[{"filename": "/abs/path/Project.ewp"}, "Debug",
                  [{"id": "General.@GRuntimeLibSelect", "value": "DLibFull"},
                   {"id": "General.@RTConfigPath2",
                    "value": "$TOOLKIT_DIR$\\\\inc\\\\c\\\\DLib_Config_Full.h"}]]''',
)
```

Apply coupled options in **one call** so validation sees them together.

`"DLibFull"` here is an element id, not a label and not an index — writing
`"2"` fails with *"List option state was not found or was disabled: 2"*.

### Change the target device

Device selection has a companion option: `General.@OGCoreOrChip` must select the
device (rather than a bare core) for the choice to be in effect. Set both at
once.

```python
projectmanager_call(
    method="ApplyOptionsForConfiguration",
    args_json='''[{"filename": "/abs/path/Project.ewp"}, "Debug",
                  [{"id": "General.@OGCoreOrChip", "value": "Chip"},
                   {"id": "General.@OGChipSelectEditMenu",
                    "value": "ADuC7024\\tAnalogDevices ADuC7024"}]]''',
)
```

`"Chip"` is the element id; the index `"1"` is also accepted here and comes back
normalized to `"Chip"`.

The device value is a **tab-separated record whose field count varies** — do not
synthesize it. Verified on EWARM 9.7.0, a selected device read back as three
fields, device / vendor / device-file path:

```
default\tNone\t/…/arm/bin/../config/devices\default.i79
```

To offer a user the valid devices, read the option and use its `elements`
(which do carry real `label`s here, e.g. `CortexM0+MPS2FVP` → "Cortex-M0+ MPS2
FVP"); write back an element `id`, or a record you got from a previous read.

### Change the debug probe

```python
projectmanager_call(
    method="ApplyOptionsForConfiguration",
    args_json='''[{"filename": "/abs/path/Project.ewp"}, "Debug",
                  [{"id": "C-SPY.@OCDynDriverList", "value": "IJET_ID"}]]''',
)
```

`ARMSIM_ID` is the simulator, `IJET_ID` I-jet, `CMSISDAP_ID` CMSIS-DAP,
`JLINK_ID` J-Link. Read the option's `elements` for what this installation
actually offers.

### Enable C-RUN with a couple of checks

```python
projectmanager_call(
    method="ApplyOptionsForConfiguration",
    args_json='''[{"filename": "/abs/path/Project.ewp"}, "Debug",
                  [{"id": "RuntimeChecking.@GenRtcEnable", "value": "On"},
                   {"id": "RuntimeChecking.@GenRtcIntOverflow", "value": "On"},
                   {"id": "RuntimeChecking.@GenRtcDivByZero", "value": "On"}]]''',
)
```

### Try a change without committing it

```python
projectmanager_call(
    method="VerifyOptionsForConfiguration",
    args_json='''[{"filename": "/abs/path/Project.ewp"}, "Debug",
                  [{"id": "ICCARM.@CCOptLevel", "value": "3"}]]''',
)
```

Returns the values the options *would* end up with, leaving the project
untouched. Useful for showing a user the effect of a change, and for discovering
coercion before committing.

### Set an option on one source file only

```python
projectmanager_call(
    method="ApplyOptionsForNode",
    args_json='''[{"filename": "/abs/path/Project.ewp"},
                  {"name": "main.c"}, "Debug",
                  [{"id": "ICCARM.@CCOptLevel", "value": "0"}]]''',
)
```

Only options whose `canBeLocal` is true can be set per node.
`RemoveOptionsForNode` drops all local overrides for that node.

### Checking what a change really did

`GetToolArgumentsForConfiguration(prj, toolId, configName)` returns the actual
command line a tool will be invoked with. It is the **ground truth** for whether
an option change had any effect — far better than re-reading the option, which
can report your new value while the build is entirely unaffected (see the
[`CheckList` note](#checklist-is-a-fixed-width-bitmask)).

```python
projectmanager_call(
    method="GetToolArgumentsForConfiguration",
    args_json='[{"filename": "/abs/path/Project.ewp"}, "ICCARM", "Debug"]',
)
```

```
-o …/Debug/Obj/ --no_cse --no_unroll --no_inline --no_code_motion --no_tbaa
--no_clustering --no_scheduling --debug --endian=little --cpu=Cortex-M3
--fpu=None --dlib_config …/DLib_Config_Full.h -Ol -e
```

`GetToolchains()` cannot currently report a toolchain's tools, so the `toolId`
must be known or assumed to match an option category id — `ICCARM`, `AARM`,
`ILINK` (the thrift comments say as much, citing MAJ-114). Note it accepts only
a **real** configuration name: a working-copy id is rejected with
*"Configuration not found"*, so this check cannot be done inside a transaction.

## Transactions: configuration working copies

To stage several changes and apply them atomically — or abandon them — use a
working copy:

```python
wc = projectmanager_call(
    method="CreateConfigWorkingCopy",
    args_json='[{"filename": "/abs/path/Project.ewp"}, "Debug"]',
)
# ... read and write options using the working copy ...
projectmanager_call(
    method="ApplyConfigWorkingCopy",
    args_json=f'[{{"filename": "/abs/path/Project.ewp"}}, "{wc}"]',
)
```

`ApplyConfigWorkingCopy` overwrites the original configuration and marks the
project as having unsaved changes. `DiscardConfigWorkingCopy` throws the copy
away; its id is invalid afterwards, and **any changes made since the last apply
are lost**. Applying a discarded copy raises.

`Verify*` uses a temporary working copy internally — that is how it avoids
touching the project.

Two asymmetries: working copies are **not supported for project-global
options**, and only partially for nodes (through the `Verify*` functions, plus
`CreateConfigNodeWorkingCopy`).

For most single-option changes a direct `Apply*` is simpler and perfectly
correct. Reach for a working copy when you are making several related changes
that should not land half-applied — but then read [gotcha 1](#1-reading-options-can-change-the-configuration)
first, because mixing a working copy with direct reads is a trap.

## Four gotchas

### 1. Reading options can change the configuration

This is the one that will bite you. Upstream states it plainly:

> *reading options in a configuration will potentially change the configuration
> itself*

Some target options persist derived state back into hidden ("slave") options
when read. The example upstream names is the **compiler optimization list** —
and you can see the machinery in the id list: alongside `ICCARM.@CCOptLevel`
there is an `ICCARM.@CCOptLevelSlave`.

The trap: if you read through a working copy and then discard it, the side
effects of the *read* live only in that copy, and the original configuration is
untouched. The result looks exactly like a lost write.

The upstream guideline:

> either read / write an option directly from the configuration, or use the same
> working copy throughout the whole option transaction (reads, writes) and apply
> it to the original configuration if successful.

Pick one and hold it for the entire transaction. Do not mix direct reads with
working-copy writes, or vice versa.

### 2. `Apply*` returns what was actually set

Option validation may reject a value outright, or coerce it to a legal one, so
the return value of `ApplyOptionsFor*` is the authority on what the
configuration now holds — not the request you sent. Always read it back.

This matters more than it sounds: coupled options mean a value you set can be
adjusted by validation reacting to another option in the same call.

Coercion is easy to observe: writing `"1"` to `General.@OGCoreOrChip` returns
`"Chip"`. Rejection raises rather than coercing — a bad `List` value comes back
as a `ProjectManagerError` with `errorType = 0` (*not* `Busy`), e.g. *"Failed to
modify option with value 'bogus'. List option state was not found or was
disabled: bogus"*. So distinguish the two: `errorType = Busy` is worth
retrying, a validation error is not.

### 3. Options update lazily, and reads cascade

The option system always returns the *updated* value, and computing one option's
value may require updating others first. So a read is not necessarily cheap or
side-effect free (see gotcha 1), and the set of options that got refreshed
depends on what you asked for. Asking for everything refreshes everything, which
is the cleanest mental model and the reason the broad "read all, then match" recipe
above is a good default.

### 4. An invalid boolean is silently accepted as "off"

The one failure mode that produces a wrong result instead of an error. Writing
an unrecognised value to a `Check` option — `"Yes"`, `""`, a typo — does not
raise; it resolves to off. Enabling a check and accidentally disabling it look
identical from the call site.

This is the exact opposite of `List`/`Radio` behaviour, which rejects unknown
values loudly, so a caller that handles option writes uniformly will be caught
out. See
[Checkboxes have no domain, and two spellings](#checkboxes-have-no-domain-and-two-spellings);
validate booleans against the option's own spelling family before sending them,
and check `Apply*`'s return value afterwards.

## Behaviour against a live `iaride`

In standalone mode against a Thrift-enabled `iaride`, these calls do not reach
the raw engine. They pass through a GUI decorator, which adds behaviour worth
designing around:

- **Mutations are refused while the IDE is busy** — a modal dialog is open, or a
  build or C-STAT analysis is running — as a `ProjectManagerError` with
  `errorType = Busy`. That error is **retryable**: back off and try again.
  Match on `errorType`, not on message text. This repo already has `BusyError`
  handling in `tools/project.py`.

  **Expect this routinely, not rarely.** *Verified*: a user simply clicking
  around the IDE was enough to make an `ApplyOptionsForConfiguration` fail with
  `Busy`; it succeeded on the first retry moments later. Since the point of
  these tools is to edit a project a human has open in front of them, a write
  that is not retried is a write that will intermittently fail in ordinary use.
  Retry writes, and be especially careful with a **restore or rollback** step —
  that is the one whose failure leaves the project in a half-changed state.
- **Message boxes are auto-dismissed**, so a remote call cannot strand a dialog
  holding the IDE's UI thread waiting for an answer nobody can give.
- **The IDE's project tree refreshes** after option application; the decorator
  raises the change notification itself.
- **Calls are marshalled onto the UI thread, one at a time.** Treat the service
  as serial — do not fan out concurrent option calls at a live IDE.
- **Interactive methods are refused outright** (anything that would open a file
  dialog), and workspace operations are routed through the IDE's own commands.

### Persistence

`ApplyOptionsForConfiguration` changes the **in-memory** project model and marks
the project modified. The project file is written at **project save**, not by the
call. This is exactly what the IDE's own options dialog does.

If a workflow needs the change on disk — a subsequent command-line build, a git
commit — save the project explicitly. Do not assume apply persisted it.

## Build actions

Build actions (pre-build, pre-link, post-link and dependency-driven commands)
are reachable through the same flat option API. On the ProjectManager interface
the whole table is **one option**, id `BUILDACTION.BuildActions`, whose `value`
is a JSON array string. Each element has exactly five keys:

| Key | Type | Meaning |
|---|---|---|
| `cmdline` | string | the command line |
| `workingDirectory` | string | working directory; new entries default to `$PROJ_DIR$` |
| `buildsequence` | int 0-3 | `0` automatic (from inputs/outputs), `1` pre-compile, `2` pre-link, `3` post-link |
| `dependencies` | string[] | input files |
| `outputs` | string[] | output files |

```json
[
  {
    "cmdline": "ielftool --bin $TARGET_PATH$ $PROJ_DIR$\\output.bin",
    "workingDirectory": "$PROJ_DIR$",
    "buildsequence": 3,
    "dependencies": ["$TARGET_PATH$"],
    "outputs": ["$PROJ_DIR$\\output.bin"]
  }
]
```

So you read that option, parse the JSON, edit the array, serialize and write it
back. There is no id per action at this level — ids are synthesized by
OptionsService for GUI row tracking and are not persisted — so address actions by
position or by matching on `cmdline`.

Note that the backend does not range-check `buildsequence` on this path; an
out-of-range value surfaces later as a fallback to `0`. Validate before writing.

## Where new tools belong

This repo is a thin adapter. Backend behaviour lives in `iar-cspy`
(`../cspy-py`), and the dependency goes one way: `iar_cspy` never imports
`iar_cspy_mcp`. So a typed option API belongs in `iar_cspy` first, with thin
`project_*` tools here that parse JSON args, call it and shape the envelope.

MCP tool names, arguments and result shapes are a public contract for AI
callers, so settle the shape deliberately. A reasonable first cut:

- read options for a configuration, with an optional id filter and an optional
  `startswith` prefix filter (C-RUN, optimization, …);
- read options for a node;
- verify a set of changes;
- apply a set of changes, surfacing the returned actual values prominently and
  mapping `errorType = Busy` onto a retryable error;
- keep the working-copy transaction internal unless a caller genuinely needs to
  stage a multi-step edit.

Returning `elements` for `List`/`Radio`/`DeviceSelection` options is what lets a
model choose a legal value without a hardcoded table — worth preserving in the
tool's result shape rather than flattening to `id`/`value`.

## Further reading

For anyone with access to the IAR Embedded Workbench monorepo:

- `core/ide/ProjectManagerService/README.md` — the authoritative document.
  §"Build and debug options", §"Update semantics", §"Configuration working
  copies", §"Side effects of option reads", §"Option GUI".
- `core/ide/Services/ThriftServiceDefinitions/thrift/projectmanager.thrift` —
  the interface itself, well commented.
- `core/ide/Services/src-python/` — an existing Python Thrift client with
  working examples; `project_manager_utils.py` already calls
  `GetOptionsForConfiguration`.
- `core/ide/ProjectManagerService/send/config/option-presentation/` — the source
  of the shared option presentation files (e.g. `RuntimeChecking.json` for the
  C-RUN options). These install to `<install>/common/config/option-presentation/`,
  so you can browse the same content from a full EW installation without the
  repo.
- `core/ide/IarBuild/src/res/ewproj-arm-target-specification.json` — maps
  friendly setting names to option ids, including coupled options such as the
  device's `@OGCoreOrChip`.
- `core/ide/OptionsService/doc/BuildActions.md` — build actions in every
  representation, with a worked example.
- `core/ide/ProjectManagerEngine/README.md` — the engine beneath the service.
- Auto-generated HTML API docs for all IDE platform Thrift services, linked from
  `ProjectManagerService/README.md` (Jenkins, IAR network only).

In this repo: [`ide-services.md`](ide-services.md) for how the services are
hosted and how to connect in managed versus standalone mode.
