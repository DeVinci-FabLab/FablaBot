# User guide

This guide lists the commands of FablaBot for members and staff, grouped by domain, with who can run each
one. `/help` shows each member only the commands their roles let them run. The roles named here are the
ones the bot checks today; the [operator guide](OPERATIONS.md#roles) lists them. Every command runs in the
commands channel, except where a section says otherwise.

## /help

`/help` lists the commands you can run on this server, grouped by domain, with a one-line summary of what
your roles open. The list differs per member because each command applies its own rule: a set of roles, a
Discord permission or, for `/user`, a rule per role (what you can do depends on the roles the server has).
A member without a staff role sees `/suggest` and `/help`.

- It works in any channel. The answer is private by default; the `show` option posts it for everyone, in the
  commands channel only.
- It answers in the language the server declares to Discord, `fr` or `de`, else English. The menu shows
  `/help`, `/aide` or `/hilfe` after each member's own Discord language.
- A command that is listed but hidden in the menu means the server has not allowed that role in
  Integrations. Ask a server administrator to follow [Staff commands in the slash menu](OPERATIONS.md#staff-commands-in-the-slash-menu).
- `/help` only reads the rules. It gives no right and changes no check.

In the tables below, "admin roles" means Administrateur, Président, Vice-Président and Secrétaire Général.
`Admin -temp-` is not an admin role: it opens only the commands that name it.

## Text channels

| Command | What it does | Who can run it |
| --- | --- | --- |
| `/text create` | Create a channel | Admin roles |
| `/text rename` | Rename a channel | Admin roles |
| `/text delete` | Delete a channel | Admin roles |

## Voice channels

| Command | What it does | Who can run it |
| --- | --- | --- |
| `/vocal create` | Create your own voice channel; Bureau can create temporary channels only | Admin roles, Bureau |
| `/vocal rename` | Rename a voice channel | Admin roles |
| `/vocal delete` | Delete a voice channel | Admin roles |

## Trainings

| Command | What it does | Who can run it |
| --- | --- | --- |
| `/fm start` | Start over with a new draft that replaces the old one | Administrateur, CoDir, Respo Formation, Admin -temp- |
| `/fm edit_text` | Edit the intro and the closing | Same |
| `/fm add` | Add a training to the draft | Same |
| `/fm edit` | Change a training in the draft | Same |
| `/fm remove` | Take a training out of the draft | Same |
| `/fm clear` | Remove every training from the draft | Same |
| `/fm preview` | Preview the draft in the channel | Same |
| `/fm publish` | Post the draft in a channel | Same |
| `/fm export` | List sign-ups and drop-outs | Same |

## Bot messages

| Command | What it does | Who can run it |
| --- | --- | --- |
| `/msg start` | Start a draft | Admin roles, Bureau |
| `/msg preview` | Preview the draft | Admin roles, Bureau |
| `/msg publish` | Publish the draft and track it | Admin roles, Bureau |
| `/msg follow` | Track a message already sent | Admin roles, Bureau |
| `/msg link_reaction` | Link a reaction to an action | Admin roles, Bureau |
| `/msg unlink_reaction` | Unlink a reaction | Admin roles, Bureau |
| `/msg list` | List tracked messages | Admin roles, Bureau |
| `/msg export` | Export the reactions | Admin roles, Bureau |
| `/msg stop` | Stop tracking a message | Admin roles, Bureau |
| `/msg dm` | Send a group DM | Bureau |
| `/msg clear [messages]` | Delete the last messages in the channel | The Manage Messages permission |
| `/msg purge` | Delete a member's messages | The Administrator permission |

- `/msg clear` removes 5 messages by default and 50 at most. It needs Manage Messages in the channel where
  it runs, and refuses bot channels, the log channel and the commands channel.
- `/msg purge` needs the Administrator permission on the server. It works outside the commands channel, and
  with neither `channel` nor `all_channels` it targets the current channel.
- `/msg link_reaction` takes one of three actions: `user_dm` (a DM to the member who reacts), `role_dm`
  (a DM to the members of a role) or `channel` (a message in a channel).

## Members and roles

| Command | What it does | Who can run it |
| --- | --- | --- |
| `/user op` | Give temporary admin | Admin roles, Respo Numérique |
| `/user deop` | Take temporary admin back | Admin roles, Respo Numérique |
| `/user add_role` | Give a role | Depends on the role, see below |
| `/user remove_role` | Take a role away | Depends on the role, see below |
| `/user add_roles` | Give a role to several members | Depends on the role, see below |
| `/user remove_roles` | Take a role from several members | Depends on the role, see below |
| `/user with_roles` | List who has these roles | Admin roles, Bureau |
| `/user invite` | Create an invite to the server | Admin roles |

- `/user op` ops you; name a `member` to op someone else. It lasts 1 to 5 minutes, 5 by default. It opens
  only the commands that name `Admin -temp-`, today `/fm`. It needs the `Admin -temp-` and `CoDir` roles on the
  server.
- `/user invite` creates a new invite to a channel and shows it to you alone. It lasts 7 days and has no use
  limit unless you pick otherwise; with `temporary`, Discord kicks a newcomer who disconnects before getting a role. You need
  Discord's Create Invite permission on that channel, and so does the bot.
- `/user add_role` never gives a role to oneself. `Admin -temp-` is given only by `/user op`, and nobody
  gives `Administrateur`.
- Whoever gives or takes a role holds a role that sits strictly above it in the server's role list; the server
  owner is exempt. `Admin -temp-` does not count toward that rank. A role at or above your highest one is refused:
  ask a member who ranks higher.
  Where `Admin -temp-` sits above your highest role, it is taken back with `/user deop`, not as a role.
- Who can give or take which role:

  | Role given or taken | Who can |
  | --- | --- |
  | Any role but `Administrateur` | Admin roles |
  | `Sbire Bureau`, `agent MyFab` | Bureau |
  | `Pôle <area>` | The matching `Respo <area>` |
  | The `F - …` roles | Respo Formation: a training manager gives and takes them |

- `/help` lists these four commands when your roles let you give or take at least one role by name. The rank is checked
  when you run the command, so a listed command can still refuse a role.

## Welcoming newcomers

| Command | What it does | Who can run it |
| --- | --- | --- |
| `/welcome verify` | Turn welcoming on or off | Admin roles |
| `/welcome approve <city>` | Approve a newcomer | Admin roles |

`/welcome approve` takes a city (Paris, Nantes or Montpellier) and runs in a `welcome-…` channel, outside the
commands channel.

## Bot logs

| Command | What it does | Who can run it |
| --- | --- | --- |
| `/log set` | Pick the channel that receives the server's errors | Admin roles |
| `/log export [date]` | Export the logs | Admin roles, Respo Numérique, Pôle Numérique |

`/log export` takes a date written `DD/MM/YYYY` (the bot's own prompt writes `JJ/MM/AAAA`), today by default.

## For everyone

| Command | What it does | Who can run it |
| --- | --- | --- |
| `/suggest` | Send an idea or a problem to the staff, anonymously if you like | Everyone |
| `/help` | Show this list | Everyone |

`/suggest` and a private `/help` work outside the commands channel.
