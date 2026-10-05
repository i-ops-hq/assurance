# Security

Open a [security advisory](https://github.com/i-ops-hq/assurance/security/advisories/new). Please do
not open a public issue for a vulnerability.

## The threat model, stated plainly

**A graph is data, and it is often committed.** A repository can carry its `graphify-out/` folder, and
the reason to ask what a change reaches is that you have not read all the code yet. So the graph, its
manifest and its `.graphify_root` are input from whoever wrote the repository.

What it does:

- **Reads** the graph and the `manifest.json` beside it as JSON, and `.graphify_root` as text.
- **Hashes** a file the graph names, to say whether it changed, only when it is a regular file inside
  the folder the graph covers, with links followed. A path outside, a link out of it, a pipe or a
  device is counted and never opened: a pipe nobody writes to would wait for ever, `/dev/zero` never
  ends, and a graph can name either.
- **Lists** the folder the graph covers, to find files of the kinds it read that are new to it, and
  stops after 50,000 folders and files.

What it never does: import, run or parse the code it reads about, run the program that built the
graph, write a file, or open a socket. A recorded root outside the folder `graphify-out` sits in is
set aside, so a graph cannot widen what is read, and the report says when one was.

Text from a graph reaches your terminal escaped. A label carrying an escape sequence, which could
clear the screen and print another report over this one, is printed as `\x1b[2J`, not sent; `--json`
escapes it as JSON does.

`tests/test_hostile_graph.py` holds a test for each of these. If you can make this open a file outside
the project, wait on something, or print what the graph does not hold, that is the report we most want.

## Known limits, since a limit nobody wrote down reads as a guarantee

- The answer is as good as the graph. An edge the producer missed is a dependency this cannot see:
  the report names what it did not follow, not what the graph never held. A hostile graph can leave
  out or invent edges, and the report is wrong in exactly that way.
- Without a manifest, staleness is read from modification times, which a copy or a checkout can make
  wrong either way. The report says when that is how it was read.
- The graph is read whole into memory, so a graph of hundreds of megabytes takes memory to match.
- The MD5 compares a file with the producer's own record of it and is trusted for nothing else.
