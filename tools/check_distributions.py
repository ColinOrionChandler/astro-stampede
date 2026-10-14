"""Install the wheel and sdist in separate fresh environments and smoke-test both.

The sdist installation invokes its build backend without the repository's Git
metadata. Optionally require an exact release version with --expected-version.
"""

import argparse
import json
import os
import subprocess
import venv
from pathlib import Path
from tempfile import TemporaryDirectory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--expected-version")
    args = parser.parse_args()
    directory = args.directory.resolve()
    wheels = list(directory.glob("*.whl"))
    sources = list(directory.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sources) != 1:
        parser.error("Expected exactly one wheel and one source distribution")
    smoke = Path(__file__).with_name("smoke_installed.py").resolve()
    versions = []
    # Prevent user-level import overrides from hiding packaging failures.
    environment = {key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME"}}
    with TemporaryDirectory(prefix="stampede-distributions-") as temporary:
        workspace = Path(temporary)
        for name, artifact in (("wheel", wheels[0]), ("sdist", sources[0])):
            env = workspace / name
            venv.EnvBuilder(with_pip=True).create(env)
            bin_dir = env / ("Scripts" if os.name == "nt" else "bin")
            python = bin_dir / ("python.exe" if os.name == "nt" else "python")

            def run(*arguments):
                return subprocess.check_output(
                    [str(python), *map(str, arguments)], cwd=workspace, env=environment, text=True
                )

            run("-m", "pip", "install", "--quiet", artifact)
            result = json.loads(
                run(
                    "-c",
                    'import importlib.metadata, json; print(json.dumps(importlib.metadata.version("astro-stampede")))',
                )
            )
            versions.append(result)
            if args.expected_version and result != args.expected_version:
                raise RuntimeError(f"{name}: expected {args.expected_version}, got {result}")
            subprocess.run(
                [str(bin_dir / "astro-stampede"), "--help"],
                cwd=workspace,
                env=environment,
                check=True,
                stdout=subprocess.DEVNULL,
            )
            print(run(smoke), end="")
        if len(set(versions)) != 1:
            raise RuntimeError(f"Wheel and sdist versions differ: {versions}")
    print("Wheel and sdist installations agree and passed outside the checkout.")


if __name__ == "__main__":
    main()
