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
| `/fm appel` | Take or fix the attendance of a training of the last 7 days | Respo Formation, Administrateur |
| `/fm modeles` | Manage the training templates of the server | Respo Formation, Administrateur |
| `/fm brouillon` | See the week's draft, set its text and role, preview it and post it | Administrateur, CoDir, Respo Formation, Admin -temp- |
| `/fm ajouter` | Add a session of a template to the draft, typing the first letters of its name | Administrateur, CoDir, Respo Formation, Admin -temp- |

### Training templates

A template is a training the server gives again and again, typed once: its name, its duration, its number of seats from 1 to 500, a description that may stay empty, and whether an absence may be excused. Each server has its own templates, and a name is used once on a server, whatever its case.

`/fm modeles` shows them, 10 to a page and in alphabetical order, in a private message. **Nouveau modèle** opens the form. **Modifier** and **Supprimer** each ask which template first; a long list is shown 25 at a time, with page buttons. Deleting asks for a confirmation. Only Respo Formation and Administrateur use it: Admin -temp- and CoDir, who may write the weekly announcement, may not change the templates. The roles are read again at every click, so a member who has lost the role is refused and nothing is saved.

A session copies a template when it is added: the template fills in the form, and the session then lives on its own. Changing or deleting a template leaves the sessions already in the draft or published as they are.

### The draft

`/fm brouillon` shows the week's draft in a private message: the intro, then the sessions sorted by start, with the trainer and the seats, and a last line with the role that will be notified and the length of the announcement. Six buttons act on it:

- **Ajouter une session** asks which training to add, from the templates. See below.
- **Modifier** and **Retirer** are greyed out while the draft has no session. Each asks which session in a menu of up to 25, sorted by start; every option shows the emoji, the name, the day and hour and the trainer, and page buttons go on past 25.
  **Modifier** opens a form with the session's trainer, date, hour, seats and description filled in. Sending it changes those four and nothing else: the emoji, name, duration and excusable flag stay, and so does the template the session came from. `/fm edit` changes the rest.
  **Retirer** asks first, naming the session, and takes it out only on **Retirer**; **Annuler** keeps it. At the click the session is found again by its start and emoji. If it was moved with `/fm edit`, removed, or given another emoji since the menu was shown, the form or the confirmation is refused with the reason and nothing changes.
- **Intro / conclusion** opens a form for the intro, the closing, the link of the form where members propose a training (left empty, it keeps the usual one) and the role to notify, which is required. Saving it keeps the sessions and the header.
- **Aperçu** shows the announcement exactly as it will be posted, in a private message that notifies nobody.
- **Publier** asks in which channel to post, starting on the channel of the last announcement, then posts the draft like `/fm publish`. It is greyed out while the draft has no session. It refuses, and posts nothing, when there is no session, no role chosen, a session that has already started, or an announcement past 2 000 characters. It also refuses a draft that is already the published announcement, even once members have signed up on it, or a publication that is still posting: a second post would stop counting the sign-ups on the first. Once posted, the question shows where the announcement went and no longer offers Publier.

#### Adding a session

**Ajouter une session** shows the server's templates in a menu, 24 to a page with page buttons, each with its duration and seats. Choosing one opens a form titled with the training. The form keeps what the template holds, the seats and the description, and asks what the template cannot know: the trainer (a member of the server, not a bot), the date and the hour. The date is `JJ/MM`, for example `12/10`, and means the next such date, today included; `JJ/MM/AAAA` is read as written. The hour is `HH:MM`, `18h30` or `18h`. The seats of the form may differ from the template's, and the template does not change.

The bot picks the session's emoji when the form is sent, the first of its pool of 20 that no session of the draft holds, so two forms sent one after the other never get the same one, and an emoji set with `/fm add` is never taken again. The draft stays sorted by start. A form is refused, with the reason and nothing added, when the date or the hour cannot be read, when the start has already passed, when the seats are not a whole number from 1 to 500, when the trainer is not a member, or when the template has been deleted meanwhile.

**Session libre**, the last option of the menu on every page, is for a training that has no template. It opens a form with five fields: the name, the trainer, the date and hour written together (`JJ/MM HH:MM`, for example `12/10 18:30`, or `JJ/MM/AAAA HH:MM`), the duration as it should be shown, and the seats. The session is added as excusable and with no description; change either with **Modifier** in the brouillon. The same refusals apply as for a template's form, and a name, duration or seats left empty or too long is refused too.

`/fm ajouter modele:` is the keyboard shortcut to the same form. Type the first letters of a name, or any letters it holds, and Discord suggests up to 25 of the server's templates, whatever the case; pick one and the form of that template opens. A name typed in full without picking it also works. A name no template of the server has is refused, with a pointer to the suggestions, and nothing is added. Only the roles of `/fm add` are suggested anything, and only the templates of their own server.

The answer names the session, its start, its trainer, its seats and its emoji, and offers three buttons. **Encore une session** reopens the form on the same training with the date and the trainer kept, to chain the sessions of a day; only the hour is left to write. After a free session it reopens the free form with its name, duration and seats as well, and the date followed by a space, to write the hour after it. A date more than a year ahead is written with its year. **Autre formation** goes back to the templates, and **Voir le brouillon** to the draft.

The template's description comes with the first session of a training only. The announcement is one message of at most 2 000 characters, and the same description repeated on every session of a training would use it up; the later sessions start with an empty description, which can still be filled in by hand. The draft holds 20 sessions at most, since Discord takes no more than 20 reactions on a message and each session is announced by the reaction of its emoji; the twenty-first is refused with that reason.

The length line counts the announcement as posted. It stops at 2 000 because the announcement is one Discord message, and Discord accepts no more in one; sign-ups are read from that one message. Past the limit the draft shows a warning: remove a session or shorten a description.

The same roles as `/fm add` use it, and they are read again at every click, so a member who has lost the role is refused and nothing is posted. The buttons keep working after the bot restarts; only an open form is lost, and it is opened again from the button.

### Attendance check

When a training starts, the bot sends its trainer a private message: that message is the attendance check (the "appel"). The trainer touches the pseudo of each member who came, and touches it again to take the mark back. A green tick shows who is marked, and the counter under the text follows. Pressing **Valider l'appel** saves the counts of present and absent members; the button then reads **Mettre à jour l'appel**.

`/fm appel` opens the same message, privately, for a training of the last 7 days on the server where it runs: right away when only one is open, otherwise through a menu of up to 25 trainings (newest first). It leaves out a cancelled training and one past its 7 days. Only Respo Formation and Administrateur run it; it does not widen who may take a check, and Admin -temp- and CoDir stay out of it.

If the trainer's private messages are closed, the check goes to a private thread instead, with the same buttons. The bot makes it in the channel of the announcement when that is a text channel, otherwise in the commands channel, and adds the trainer and every Respo Formation to it: nobody else can join, and each of them can take the check there. The message in the thread says so and mentions the trainer. Each later sending of the check uses the same thread. If no thread can be made, the check stays open and the log says so.

If the bot was off at the start, or a newer announcement came out since, the message arrives at its next five-minute check, up to 7 days after the start. A training published again in a newer announcement gets one check: a check already open stays where it is, and otherwise the newer announcement opens it. The trainer can keep correcting for 7 days after the start. A correction made after the validation shows a counter marked "pas encore enregistré" until the update button is pressed. The check lives in the database, so its buttons keep working after the bot restarts.

Up to 20 registered members get a button each. Past that, the message holds menus of 25 members, and it lists 100 members at most: the others count as absent, and the message says so. The waitlist is never listed.

Each validation, and each update after it, sends every Respo Formation a private message: who validated, then the members present and the members absent, each as a mention, `aucun` when a list is empty. A long list goes out in several messages, and an update says "a été mis à jour par". The log channel gets one line per validation with the training, who validated and the counts only; it names no registered member and pings nobody. A Respo Formation whose private messages are closed is skipped, and the others still get theirs. The lists and the counts are those saved at the click, whatever is marked afterwards, and they go out even when the trainer's message could not be refreshed. They are not kept for later: if the bot restarts between the save and the sending, nothing is sent again, and the trainer presses `Mettre à jour l'appel` within the 7 days of the edit window to send the lists (worded as an update). When validations of the same check overlap, the reports go out one after the other, and a report that a newer validation already replaced is not sent, and leaves no line in the log channel either, so the last one a Respo Formation reads is the newest.

When the training's slot is over and nobody saved the check, the bot sends every Respo Formation an alert, once, at its next five-minute check. The slot ends at the start plus the duration of the announcement, or three hours after the start when the duration cannot be read. Nothing is sent once the trainer has saved the check, nor after the 7 days of the edit window. The alert names the trainer (or says "Le formateur" when the announcement names none) and offers three buttons:

- **Relancer le formateur** sends the trainer the check again, in private or in the thread when their private messages are closed. If nobody could be reached, if the training names no trainer, or if the sending failed, you are told which and the alert stays open for another answer. While a relaunch is in progress, a click from another Respo Formation is told `Relance en cours …` and stores nothing: once the relaunch is over, the click can be repeated, and is taken if the relaunch failed.
- **Ignorer** changes nothing else: the check stays open and the trainer can still take it.
- **Annuler la formation** closes the check: nobody can mark or save it any more, and the training counts as not held. Nothing is sent to the registered members.

The first answer decides. Every Respo Formation's copy then shows who answered and when, with the buttons off, and a later click is told the first answer (`Déjà traité : …`) and changes nothing. If the trainer saves the check after the alert, every copy shows `Appel fait par` the trainer, and the alert is closed. Only the Respo Formation holders and the admin roles can answer: someone who lost the role since the alert was sent is refused.

Who can take or correct a check:

- The trainer named in the announcement, while still on the server.
- The Respo Formation holders and the admin roles: Administrateur, Président, Vice-Président, Secrétaire Général.
- `Admin -temp-` and CoDir only as the trainer: whoever `/fm` names as trainer takes the check. Each validation
  is posted in the log channel and sent to Respo Formation, so a check taken by someone else shows at once.

Anyone else, and anyone after the 7 days or once the training is cancelled, gets a private refusal that says nothing was changed. If the announcement names no member as trainer, the check is opened but no message is sent: Respo Formation or Administrateur takes it with `/fm appel`.

## Bot messages

| Command | What it does | Who can run it |
| --- | --- | --- |
| `/msg start` | Start a draft | Admin roles, Bureau |
| `/msg edit` | Edit the draft's text, keeping its reactions | Admin roles, Bureau |
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
| `/user invite` | Create an invite to the server | Admin roles, CoDir |

- `/user op` ops you; name a `member` to op someone else. It lasts 1 to 5 minutes, 5 by default. It opens
  only the commands that name `Admin -temp-`, today `/fm`. It needs the `Admin -temp-` and `CoDir` roles on the
  server. When the op ends, the message says how many actions the audit log holds for that member outside the bot,
  and the log channel lists them: an op is for what the bot cannot do yet.
- `/user invite` creates a new invite to a channel and shows it to you alone. It lasts 7 days and has no use
  limit unless you pick otherwise. With `temporary`, Discord kicks a newcomer who disconnects before getting a
  role. You need to see that channel; Discord's own Create Invite is not needed, so a server can keep it off
  for every role and leave invites to the bot. Each invite is posted in the log channel: who, to where, for how
  long, never the link.
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
