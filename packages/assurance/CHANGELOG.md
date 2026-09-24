# 0.1.1

- **Requires `assurance-budget` 0.2.1**, so `uvx assurance audit` gets its fixes: paths shown relative
  to the session folder on macOS, no false report of a limits-file change for a path outside the
  project, singular wording at 1, and repeated test commands grouped.

# 0.1.0

- **First release: `pip install assurance` installs every command-line tool**, and `uvx assurance`
  runs one without installing anything. No code of its own — it depends on `assurance-cli`,
  `assurance-deps`, `assurance-budget` and `assurance-authority`, and declares the `assurance`
  command so `uvx` and `pipx` find it on the package that was named.
