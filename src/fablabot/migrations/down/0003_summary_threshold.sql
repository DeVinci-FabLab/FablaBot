-- The way back from schema 3: present and absent are required again. A training whose counts were not kept reads 0,
-- the only value the older schema accepts; nothing else changes.
DROP VIEW training_month;
ALTER TABLE training_summary RENAME TO training_summary_before;

CREATE TABLE training_summary (
    guild_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    position INTEGER NOT NULL,
    name TEXT NOT NULL,
    starts_at TEXT NOT NULL,
    registered INTEGER NOT NULL,
    waitlisted INTEGER NOT NULL,
    withdrawn INTEGER NOT NULL,
    present INTEGER NOT NULL,
    absent INTEGER NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('done', 'not_done', 'cancelled', 'not_asked')),
    PRIMARY KEY (guild_id, message_id, position)
) STRICT;

INSERT INTO training_summary
SELECT guild_id, message_id, position, name, starts_at, registered, waitlisted, withdrawn,
    COALESCE(present, 0), COALESCE(absent, 0), outcome
FROM training_summary_before;

DROP TABLE training_summary_before;

CREATE VIEW training_month AS
SELECT
    guild_id,
    substr(starts_at, 1, 7) AS month,
    COUNT(*) AS trainings,
    SUM(registered) AS registered,
    SUM(waitlisted) AS waitlisted,
    SUM(withdrawn) AS withdrawn,
    SUM(present) AS present,
    SUM(absent) AS absent,
    SUM(outcome = 'not_done') AS not_done,
    SUM(outcome = 'cancelled') AS cancelled
FROM training_summary
GROUP BY guild_id, substr(starts_at, 1, 7);
