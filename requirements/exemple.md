# Exemple of function with sphinx

## Comment documenter une fonction python

Pour documenter la fonction suivante dans noxfile,

```{code-block} python
def check_if_commited() -> bool:
"""returns if the current branch is commited
Returns:
    bool: status of branch
"""
```

Vous pouvez utiliser la commande suivante:

```{code-block} markdown
\```{eval-rst}
.. autofunction:: noxfile.check_if_commited
\```
```

:::{admonition} Attention au chemin d'accès
:class: attention
Il faut spécifier à la place de noxfile le chemin d'accès jusqu'au fichier.
:::

:::{admonition} Auto-function!
:class: tip
We can also use automodule to auto-document a file
:::

Cela permet d'afficher le contenu de la manière suivante.

```{eval-rst}
.. autofunction:: noxfile.check_if_commited
```

## Comment Afficher un graph mermaid

```{code-block} markdown
\```{mermaid}
graph TD
    A[Enter Chart Definition] --> B(Preview)
    B --> C{decide}
    C --> D[Keep]
    C --> E[Edit Definition]
    E --> B
    D --> F[Save Image and Code]
    F --> B
\```
```

```{mermaid}
graph TD
    A[Enter Chart Definition] --> B(Preview)
    B --> C{decide}
    C --> D[Keep]
    C --> E[Edit Definition]
    E --> B
    D --> F[Save Image and Code]
    F --> B
```

