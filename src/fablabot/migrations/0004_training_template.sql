-- The trainings a server gives again and again, typed once: adding a session copies them, so editing or deleting a
-- template never changes a session already in a draft or published. No member is stored here.
-- name_key: the name casefolded by the service, so that two names differing in the case of an accented letter are one name;
-- SQLite's NOCASE folds ASCII letters only.
-- AUTOINCREMENT: a deleted template's ID is never given again, so a button kept in a message cannot reach another one.
CREATE TABLE training_template (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
    name_key TEXT NOT NULL CHECK (length(name_key) >= 1),
    duration TEXT NOT NULL CHECK (length(duration) BETWEEN 1 AND 50),
    seats INTEGER NOT NULL CHECK (seats BETWEEN 1 AND 500),
    description TEXT NOT NULL CHECK (length(description) <= 500),
    excusable INTEGER NOT NULL CHECK (excusable IN (0, 1)),
    UNIQUE (guild_id, name_key)
) STRICT;
