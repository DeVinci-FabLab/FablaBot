-- What the bot itself remembers between two starts, one value per key and for no server: the end of the last retention run.
-- No member and no server is stored here, so the retention has nothing to delete in it.
CREATE TABLE bot_state (
    key TEXT PRIMARY KEY CHECK (length(key) BETWEEN 1 AND 100),
    value TEXT NOT NULL CHECK (length(value) <= 200)
) STRICT;
