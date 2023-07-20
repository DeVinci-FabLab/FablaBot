import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.abspath("../.."))
sys.path.insert(0, os.path.abspath("../../src"))
dotenv_path = os.path.join(os.path.dirname(__file__), "../../.env")
load_dotenv(dotenv_path)

PROJECT = "Présentation Hacker Space"
copyright = "Laboratoire IA et Big Data"
AUTHOR = "Yann Vidamment et Thomas DERUDDER"
RELEASE = "presentation"
autosectionlabel_prefix_document = True
html_title = "Présentation Hacker Space"
html_logo = "_static/images/Ybin.gif"
html_favicon = "_static/images/favicon.ico"

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.duration",
    "sphinx.ext.doctest",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.autosummary",
    "myst_parser",
    "sphinx.ext.autosectionlabel",
    "sphinxcontrib.mermaid",
    "sphinx_copybutton",
    "sphinx_design",
    "sphinx_immaterial",
    "sphinx_togglebutton",
]

autodoc_mock_imports = ["../../noxfile.py"]


def setup(app):
    app.add_css_file("custom.css")


# Set the autodoc options
autodoc_default_options = {
    "members": True,
    "undoc-members": True,
    "show-inheritance": True,
    "reference-labels": True,
}
autosummary_generate = True

templates_path = ["_templates"]
exclude_patterns = []


# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

myst_enable_extensions = {
    "colon_fence": True,
    "substitution": True,
    "deflist": True,
    "dollarmath": True,
    "amsmath": True,
    "smartquotes": True,
    "replacements": True,
    "html_image": True,
    "html_admonition": True,
    "attrs_inline": True,
}

html_theme = "sphinx_immaterial"
html_static_path = ["_static"]
source_parsers = {".md": "recommonmark.parser.CommonMarkParser"}
source_suffix = {
    ".rst": "restructuredtext",
    ".txt": "markdown",
    ".md": "markdown",
}

source_parsers = {".md": "recommonmark.parser.CommonMarkParser"}


html_sidebars = {
    "**": ["logo-text.html", "globaltoc.html", "localtoc.html", "searchbox.html"]
}

html_theme_options = {
    "repo_name": "Sphinx-Immaterial",
    "version_dropdown": True,
    "toc_title": "Contents",
    "palette": [
        {
            "media": "(prefers-color-scheme: light)",
            "scheme": "default",
            "toggle": {
                "icon": "material/toggle-switch-off-outline",
                "name": "Switch to dark mode",
            },
        },
        {
            "media": "(prefers-color-scheme: dark)",
            "scheme": "slate",
            "primary": "teal",
            "accent": "lime",
            "toggle": {
                "icon": "material/toggle-switch",
                "name": "Switch to light mode",
            },
        },
    ],
    "icon": {
        "repo": "fontawesome/brands/github",
        "edit": "material/file-edit-outline",
    },
    "social": [
        {
            "icon": "fontawesome/brands/github",
            "link": "https://github.com/jbms/sphinx-immaterial",
            "name": "Source on github.com",
        },
        {
            "icon": "fontawesome/brands/python",
            "link": "https://pypi.org/project/sphinx-immaterial/",
        },
    ],
    "icon": {
        "admonition": {
            "note": "material/file-document-outline",
        },
    },
    "version_dropdown": True,
}
sphinx_immaterial_override_builtin_admonitions: bool = True
sphinx_immaterial_override_version_directives: bool = False
sphinx_immaterial_custom_admonitions = [
    {
        "name": "versionchanged",
        "color": (27, 138, 236),
        "icon": "material/alert-rhombus",
    }
]
