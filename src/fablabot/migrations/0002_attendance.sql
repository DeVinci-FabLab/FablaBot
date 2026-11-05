-- The attendance check of one training. It points at the announcement and the training's place in it, not at a
-- formation row: saving the message deletes and rebuilds those rows, and the check and its marks must outlive that.
CREATE TABLE appel (
    id INTEGER PRIMARY KEY,
    fm_message_id INTEGER NOT NULL REFERENCES fm_message (id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    trainer_id INTEGER,
    starts_at TEXT NOT NULL,
    ends_at TEXT NOT NULL,
    closes_at TEXT NOT NULL,
    thread_id INTEGER,
    validated_by INTEGER,
    validated_at TEXT,
    present_count INTEGER,
    absent_count INTEGER,
    alert_sent INTEGER NOT NULL DEFAULT 0 CHECK (alert_sent IN (0, 1)),
    handled_by INTEGER,
    handled_at TEXT,
    handled_action TEXT CHECK (handled_action IN ('relaunch', 'ignore', 'cancel', 'done')),
    UNIQUE (fm_message_id, position)
) STRICT;

-- Who was marked present at a check, before and after it is validated.
CREATE TABLE appel_present (
    appel_id INTEGER NOT NULL REFERENCES appel (id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL,
    PRIMARY KEY (appel_id, user_id)
) STRICT;

-- The copies of an alert sent to the Respo Formation, so each can be edited once someone answers.
CREATE TABLE appel_alert (
    appel_id INTEGER NOT NULL REFERENCES appel (id) ON DELETE CASCADE,
    channel_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    PRIMARY KEY (appel_id, message_id)
) STRICT;

-- What is kept of a training after its member lists are deleted: counts only, no member ID, name or pseudo.
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
