# Validation diagnostics

Run your usual launcher command with `--validate` to check the manifest and PNG
inputs without opening or changing classification stores. Omitted paths retain
the launcher's normal defaults. Validation reports a problem count and one
specific diagnostic per line, including configuration keys or manifest row and
field names, for example:

```text
2 problem(s) found:
  - Invalid manifest row 2, object_id: ID cannot be empty.
  - Invalid manifest row 3, order: 'nan': Order must be a finite number.
```

Independent problems are collected in one pass. Checks that depend on invalid
input are skipped; unreadable files and unusable manifest headers prevent further
checks of that manifest. Expected failures exit with code 2. Local terminal
messages can include configured paths and filesystem reasons; review them before
sharing. Runtime storage failures are reported when encountered and stop startup;
`--validate` does not attempt writes to prove that storage is writable.
