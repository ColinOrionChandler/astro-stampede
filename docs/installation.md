# Installation and synthetic demo

Python 3.10 or later is required. From this checkout:

```sh
python -m venv .venv
source .venv/bin/activate
pip install .
astro-stampede --demo local/demo --reviewer demo
```

The demo creates new synthetic pixels and a runtime manifest in a new directory.
It refuses to overwrite an existing directory. It uses no RCC installation or
research data. To reopen the same demo and its labels:

```sh
astro-stampede --root local/demo --manifest local/demo/manifest.csv \
  --dataset synthetic-demo --state-dir local/demo/state --reviewer demo
```

The application binds to loopback. It is designed for one local review process,
not shared hosted sessions. Source images are read only. Labels are saved to
SQLite; pending browser changes flush periodically and before navigation/export.
Use **Export** or **Shutdown** to write the portable snapshot before closing a session.
