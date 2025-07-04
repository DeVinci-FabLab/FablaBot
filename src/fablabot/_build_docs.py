"""Utility script used to build the project documentation."""

from pathlib import Path
import sys

import pdoc.render
import pdoc.web

from fablabot import __name__ as module_name

DOCS_PATH = Path("docs")  # Path is relative to project root


def build_docs() -> None:
    """Build the module documentation. @private.

    This function should not be called as is but is supposed to be called from the
    CLI.
    """
    if {"-h", "--help"}.intersection(sys.argv):
        print(f"Build documentation for {__package__}.")
        print("   -h --help      Show this message.")
        print("   -l --live      Start the pdoc web server with live refresh before building the docs.")
        print("   -b --browser   Open the web browser on doc build or web server start.")
        return
    live = bool({"-l", "--live"}.intersection(sys.argv))
    browser = bool({"-b", "--browser"}.intersection(sys.argv))

    pdoc.render.configure(
        docformat="google",
        template_directory=DOCS_PATH,
        favicon="https://my.devinci-fablab.fr/favicon.ico",
        logo="https://devinci-fablab.fr/_next/image?url=%2FlogoName.png&w=640&q=75",
        mermaid=True,
    )

    # Cloned from https://github.com/mitmproxy/pdoc/blob/main/pdoc/__main__.py cli()
    if live:
        host = "localhost"
        port = None
        try:
            try:
                httpd = pdoc.web.DocServer((host, port or 8080), [module_name])
            except OSError:
                # Couldn't bind, let's try again with a random port.
                httpd = pdoc.web.DocServer((host, port or 0), [module_name])
        except OSError as e:
            print(f"Cannot start web server on {host}:{port}: {e}")
            sys.exit(1)

        with httpd:
            url = f"http://{host}:{httpd.server_port}"
            print(f"pdoc server ready at {url}")
            if browser:
                pdoc.web.open_browser(url)
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                httpd.server_close()

    print("Building documentation in `docs`...")
    pdoc.pdoc(module_name, output_directory=DOCS_PATH)

    if browser and not live:
        pdoc.web.open_browser(str(Path("docs", "index.html").absolute()))


if __name__ == "__main__":
    build_docs()
