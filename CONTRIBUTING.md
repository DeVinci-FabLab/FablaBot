# Contributing

> *This page is still a work in progress.*

## Linting and formatting

This code base is formatted with [Ruff](https://docs.astral.sh/ruff/), a fast python linter and formatter, written in Rust.

Before submitting any code to the project, please ensure that all of your code is clean and respects all linter rules.

Apply the formatter to the project by running:

```bash
ruff format
```

And check for linter errors with:

```bash
ruff check  # Add the `--fix` flag to automatically apply available fixes
```

> [!note] If you do not have Ruff installed
> Run the commands directly with uv py prepending the above commands with `uvx`.  
> *Giving the commands `uvx ruff format` and `uvx ruff check`.*
