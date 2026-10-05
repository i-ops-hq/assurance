# assurance-reach

**What a change to a file reaches, by a code graph, and how far that graph is behind your code.**

```bash
pip install assurance          # every tool; or: pip install assurance-reach
graphify update .              # writes graphify-out/graph.json; any graph of the same shape works
assurance reach src/billing/money.py
```

It reads a code graph; it does not build one. [Graphify](https://pypi.org/project/graphifyy/) builds
the graph this was written against, from your code alone with no model (`graphify update`), and any
other producer that writes the same shape works too. From the graph it answers one question: if this
file or folder changes, what depends on it, hop by hop, at the line where each dependency is. Then it
says what a graph usually does not: how far behind the code the graph is, and what it could not
determine.

Here it is on [a small project](tests/fixtures/shop) and the graph Graphify built of it:

```
$ assurance reach shop/money.py
What a change to shop/money.py reaches, by graphify-out/graph.json

The graph is current. Since it was built, at 2026-10-05 19:38, none of the 8 files it read has changed, checked by content, and no file of the kinds it reads is new to it.

It reaches 11 symbols in 5 files, to depth 3: 8 by extracted edges, 3 only by inferred ones.

By extracted edges, read from the code:
  shop/cli.py
    L3    cli.py imports_from invoice.py  (2 hops)
  shop/invoice.py
    L3    invoice.py imports_from money.py
    L14   .total() references Money
  shop/report.py
    L3    report.py imports_from invoice.py  (2 hops)
  shop/tax.py
    L3    tax.py imports_from money.py
    L8    tax() references Money
  tests/test_tax.py
    L1    test_tax.py imports_from tax.py  (2 hops)
    L5    test_tax_on_ten() calls tax()  (2 hops)

Only by inferred edges, which the producer inferred rather than read from the code:
  shop/cli.py
    L8    main() calls Invoice  (2 hops)
  shop/invoice.py
    L14   Invoice uses Money  (score 0.95)
  shop/report.py
    L6    monthly() references Invoice  (2 hops)

Not determined: not followed, as not dependencies: contains 8, method 5, rationale_for 7.
```

## What it says

- **What depends on the change, at the call site.** A change reaches what depends on it, so it follows
  dependency edges backwards: the relations Graphify's own blast radius follows (`calls`,
  `indirect_call`, `references`, `imports`, `imports_from`, `dynamic_import`, `re_exports`, `inherits`,
  `extends`, `implements`, `uses`, `mixes_in`, `embeds`, `requires`), and a package manifest's
  `depends_on` and `crate_depends_on`. Each line is a symbol that depends on the change, with the
  relation that carried the hop and the line of the call or import, not of a definition. Three hops by
  default; `--depth N` sets it, and `--depth 0` follows everything.
- **How sure the graph is, never mixed.** A symbol is listed under extracted edges only when a path of
  extracted edges alone reaches it. One reached only through an edge the producer inferred, or marked
  ambiguous, is listed apart, with the score the producer gave the hop where it gave one. A path is as
  sure as its weakest edge.
- **How far the graph is behind the code.** Every report says so, first. With Graphify's manifest,
  every file it read is checked by its content, so a file only touched is not reported as changed:
  which changed, which are gone, and which files of the kinds it reads are new to it. A graph without
  a manifest is checked by modification time, and the report says that is what it did. Whatever it
  could not check, it names, and it does not call a graph current past it.
- **What it could not determine.** Symbols at the last hop with dependents it did not follow; every
  relation it does not follow, by name and count, including any it does not know; reached symbols the
  graph places in no file; records in the graph it could not read, by why. A path the graph holds nothing in is reported as unknown, not as reaching
  nothing.

## What it reads

```
nodes  id, label, source_file, source_location
edges  source, target, relation, confidence, source_file, source_location
```

An edge points from the code that depends to what it depends on, and its own `source_file` and
`source_location` are where that happens. `confidence` is `EXTRACTED`, `INFERRED` or `AMBIGUOUS`;
anything else is read as weaker than all three and shown as itself. Graphify writes this as
node-link JSON, edges under `links`; another producer may use `edges`. A `manifest.json` beside the
graph, as Graphify writes one, gives each file's MD5 as it was read. Records that cannot be read are
counted, by why, in `--json`.

Without `--graph`, it uses the nearest `graphify-out/graph.json` above the path, or above the current
folder. The folder a graph covers is the one its `graphify-out` sits in, which is the one Graphify
read, and paths are relative to it; `--root` names another. Graphify run on a folder below the one it
ran from writes the graph's paths from where it ran and records that folder in `.graphify_root`, so
that front is taken off and the graph and its manifest name each file the same way. An absolute
recorded folder is used only when it is a folder here inside the project, and the report says when
it was not.

## For a program

`--json` prints the same answer as `assurance.reach/1`:

- `changed`, the path asked about, relative to the folder the graph covers.
- `graph`: `path`, `root`, `nodes`, `edges`, `built`, `not_read` (records it could not read, by
  why), and `notes`.
- `staleness`: `how`, `files_read`, `changed`, `gone`, `new`, `outside`, `new_search_stopped`,
  `new_search_unlisted` and `changed_path`.
- `start`, the symbols in the changed path.
- `reached`, each with where it is defined (`file`, `line`); the hop that reached it (`relation`,
  `depends_on`, `depends_on_id`, `call_site`, `hop_confidence`, `hop_score`); its `depth`; and the
  `confidence` of the strongest path to it. Following `depends_on_id` leads back to the change.
- `not_determined`: `depth`, `past_depth`, `unfollowed`, `unknown_relations` and `unlocated`.

It exits 0 when it answered, including "nothing" and "unknown", and 2 when it could not: no graph, a
graph it cannot read, or a path outside the folder the graph covers.

## What it is not

- **Not a graph builder.** It never extracts anything from code. A graph is the producer's job.
- **Not a model.** No edge is generated here, and none is guessed: an unknown relation is not followed,
  and is named.
- **Not a gate, yet.** It prints what a change reaches. Deciding whether the person asking may touch
  all of it, and checking afterwards that a change did what it said, are what this is the first step
  toward.

It reads the graph, and the files it names inside the project, on your machine, and sends nothing
anywhere. A graph is often committed, so it is read as input from whoever wrote the repository:
[SECURITY.md](SECURITY.md) says what one cannot make this do.

Apache-2.0.
