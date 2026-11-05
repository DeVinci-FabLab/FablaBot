-- A training with fewer than 3 registered members keeps no present and absent counts: with the reactions public they
-- could point at a person. The two columns may now be empty, and the counts already kept for such trainings are emptied.
-- 3 is ANONYMOUS_MIN_REGISTERED in helpers/constants.py. SQLite cannot drop a NOT NULL, so the table is rebuilt; the view
-- reads the table, so it goes first and comes back after. The old table is renamed and the new one created under the
-- name, so the schema text stays as it was.
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
    present INTEGER,
    absent INTEGER,
    outcome TEXT NOT NULL CHECK (outcome IN ('done', 'not_done', 'cancelled', 'not_asked')),
    PRIMARY KEY (guild_id, message_id, position)
) STRICT;

INSERT INTO training_summary
SELECT
    guild_id, message_id, position, name, starts_at, registered, waitlisted, withdrawn,
    CASE WHEN registered < 3 THEN NULL ELSE present END,
    CASE WHEN registered < 3 THEN NULL ELSE absent END,
    outcome
FROM training_summary_before;

DROP TABLE training_summary_before;

-- The counts by server and month, for the figures the staff asks for.
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
