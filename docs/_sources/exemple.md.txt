# Exemple of function with sphinx

## Comment documenter une fonction python

Pour documenter la fonction suivante dans noxfile,


:::{admonition} Attention au chemin d'accès
:class: attention
Il faut spécifier à la place de noxfile le chemin d'accès jusqu'au fichier.
:::

:::{admonition} Auto-function!
:class: tip
We can also use automodule to auto-document a file
:::

Cela permet d'afficher le contenu de la manière suivante.

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
