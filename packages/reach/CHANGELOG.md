# 0.1.0

- **`assurance reach <path>`: what a change to a file or folder reaches, by a code graph.** It reads a
  graph such as Graphify's, never builds one, and follows dependency edges backwards from every symbol
  in the path: each symbol that depends on the change, with the relation and the line of the call or
  import that carries the hop. The relations followed are the ones Graphify's own blast radius
  follows, and a package manifest's `depends_on`; any other is named, never guessed into a dependency.
  Symbols reached only through edges the producer inferred or marked ambiguous are listed apart from
  those reached by extracted edges alone. Every report says first how far the graph is behind the
  code, by each file's content when the producer left a manifest, and ends with what it could not
  determine: hops past the depth, relations it does not follow, symbols in no file, records it could
  not read. `--json` prints it as `assurance.reach/1`. Pinned to the graph Graphify 0.9.77 writes,
  whether run on a whole project or on a folder below where it ran.
- **A graph chooses nothing that is read.** It is often committed, so it is read as input from whoever
  wrote the repository: only regular files inside the folder it covers are opened, a recorded root
  outside that folder is set aside and said, and text from it is printed escaped.
