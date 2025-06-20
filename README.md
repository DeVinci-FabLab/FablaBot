# FablaBot

> General purpose Discord bot for the DeVinci Fablab discord server.

![Banner](./assets/banner.png)

## Project setup

This library is managed with [`uv`](https://docs.astral.sh/uv/), a fast Python package and project manager written in Rust.

Start by [installing `uv`](https://docs.astral.sh/uv/getting-started/installation/).

While using `uv` is not mandatory, it is the simplest way to get started with this project. If you know what you're doing feel free to use any other tool.

### Installing dependencies

To create a Virtual Environment for the project and install the dependencies, you can simply use:

```bash
uv sync
```

### Environment variables

You will need to create a `secrets/` folder into the project root directory and add three files:

- `secrets/discord_token.secret`
- `secrets/guild_token.secret`
- `secrets/personal_id.secret`

Here are some commands to help you create those:

```bash
mkdir secrets
```

```bash
echo -n 'my_discord_token' > secrets/discord_token.secret
echo -n 'my_personal_id' > secrets/personal_id.secret
echo -n 'my_guild_token' > secrets/guild_token.secret
```

### Running the project

To run the code, you can then use:

```bash
uv run src/main.py
```

## Documentation

We **highly recommend** you to read the user guide before using the bot. It will help you understand the bot's features
and how to use them. You can find the user guide [here](./docs/user-guide.md).

### Building the technical documentation

To build the documentation of a version of this project, you can use the provided `doc_builder` utility.

```bash
uv run doc_builder
```

You can then open the `docs/index.html` file in your browser to view the codebase's documentation.

Add the `--help` flag to this command to see available options.

## Roadmap

The library is still in active development. The next feature and bug resolutions are listed in
the [Project](https://github.com/orgs/DeVinci-FabLab/projects/5/views/2) section of the GitHub repository.

## Security Policy

Consider reading our [SECURITY](https://github.com/MorganKryze/ConsoleAppVisuals/blob/main/.github/SECURITY.md) policy
to know more about how we handle security issues and how to report them. You will also find the stable versions of the
project.

## Acknowledgments

Consider reading
the [ACKNOWLEDGMENTS](https://github.com/MorganKryze/ConsoleAppVisuals/blob/main/.github/ACKNOWLEDGMENTS.md) file. It's
a testament to the collaborative effort that has gone into improving and refining our library. We're deeply grateful to
all our contributors for their invaluable input and the significant difference they've made to the project.

It also lists the open source projects that have been used to build this library until now.

## Contributing

Contributions are what make the open source community such an amazing place to learn, inspire, and create. Any
contributions you make are **greatly appreciated**. To do so, follow the steps described in
the [CONTRIBUTING](https://github.com/MorganKryze/ConsoleAppVisuals/blob/main/.github/CONTRIBUTING.md) file.

We are always open for feedback and discussions. If you are using our library and want to share your use case, or if you
have any suggestions for improvement, please feel free
to [open an issue](https://github.com/MorganKryze/ConsoleAppVisuals/issues)
or [open a discussion](https://github.com/MorganKryze/ConsoleAppVisuals/discussions) on our GitHub repository. Your
input helps us understand possible use cases and make necessary improvements.

Do not hesitate to **star** and **share** the project if you like it!

## License

Distributed under the MIT License. See [LICENSE](./LICENSE) for more information.
