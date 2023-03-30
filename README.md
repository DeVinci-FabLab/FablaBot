# BotFabLab

## 1. Inroduction
Creation of a bot for my association fablab!

Grantt diagramm of the rpoject

```mermaid
gantt

         title Avancée botFabLab
         dateFormat  YYYY-MM-DD
         section Section
         BUREAU 2022/2023                    :a1, 2023-03-15, 15d
         BUREAU 2023/2024                    :after a1  , 50d

         section Création Formation
         Rendu Formation                     :crit, done,2023-03-20, 10d
         Creation arbre                      :crit,active,5d
         Auto Overleaf                       :active,15d

         section Gérer Salons
         Gérer les salons                    :crit,done,salon_bot,2023-03-20,7d
         Créer un serveur qualitratif        :crit,active,12d
         Créer un serveur qualitratif        :crit,active,saloon , after salon_bot,12d

         section Gérer les membres
         Créer un système admin              :after saloon, 20d
         Gérer espaces perso                 :after saloon, 20d
         Créer une bdd                       :after saloon, 40d

```

## 2. Code

1. Cogs
   1. Formation

         In the cog "formation", we created a formation system. It is possible to create, modify, delete, display formations, and display a graph of all formations.

   2. Gestion

         - Manage channels permission
         - Create a channel
         - Clear and reboot commands
   3. Music

         In comming

   4. Welcom

         In comming

```mermaid
---
title: Representation of xurrent programm (simplified)
---
classDiagram
    Bot <|-- Formation
    Bot <|-- Gestion
    Bot <|-- Music

    class Gestion{
        +listPermissions

        +is_a_super_user()
        +permission_user()
        +permission_role()
        +create_channel()
        +clear()
        +reboot()
    }
    class Formation{
        +PlantUml graphFormation
        +GenererGraph()

    }
    class Music{
    }

```