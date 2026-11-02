-- Per-server settings. A server without a row has nothing set.
CREATE TABLE guild_settings (
    guild_id INTEGER PRIMARY KEY,
    commands_channel_id INTEGER,
    log_channel_id INTEGER,
    welcome_verify_enabled INTEGER CHECK (welcome_verify_enabled IN (0, 1))
) STRICT;

-- The instance-wide log channel of older releases: read to give it to its server, never written by the bot.
CREATE TABLE legacy_log_channel (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    channel_id INTEGER NOT NULL
) STRICT;

-- Training announcements. No message_id: the server's draft. Published rows stay; the newest is the current one.
CREATE TABLE fm_message (
    id INTEGER PRIMARY KEY,
    guild_id INTEGER NOT NULL,
    message_id INTEGER,
    channel_id INTEGER,
    header TEXT NOT NULL,
    role_id INTEGER NOT NULL,
    intro TEXT NOT NULL,
    end_text TEXT NOT NULL,
    request_forms_url TEXT NOT NULL,
    UNIQUE (guild_id, message_id)
) STRICT;

CREATE UNIQUE INDEX fm_message_one_draft ON fm_message (guild_id) WHERE message_id IS NULL;

-- Two add forms left open can store one emoji twice in a draft, so emojis are not unique here.
CREATE TABLE formation (
    id INTEGER PRIMARY KEY,
    fm_message_id INTEGER NOT NULL REFERENCES fm_message (id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    emoji TEXT NOT NULL,
    name TEXT NOT NULL,
    trainer_mention TEXT NOT NULL,
    start_iso TEXT NOT NULL,
    duration TEXT NOT NULL,
    seats INTEGER NOT NULL,
    description TEXT NOT NULL,
    excusable INTEGER NOT NULL CHECK (excusable IN (0, 1)),
    notified_hour_before INTEGER NOT NULL CHECK (notified_hour_before IN (0, 1)),
    notified_at_start INTEGER NOT NULL CHECK (notified_at_start IN (0, 1)),
    UNIQUE (fm_message_id, position)
) STRICT;

-- Registered and waitlisted members of a formation, in their order.
CREATE TABLE registration (
    formation_id INTEGER NOT NULL REFERENCES formation (id) ON DELETE CASCADE,
    waitlisted INTEGER NOT NULL CHECK (waitlisted IN (0, 1)),
    position INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    username TEXT NOT NULL,
    ts_iso TEXT NOT NULL,
    PRIMARY KEY (formation_id, waitlisted, position)
) STRICT;

-- Tracked messages. No message_id: the server's draft.
CREATE TABLE tracked_message (
    id INTEGER PRIMARY KEY,
    guild_id INTEGER NOT NULL,
    message_id INTEGER,
    channel_id INTEGER,
    content TEXT NOT NULL,
    created_by INTEGER,
    created_at_iso TEXT,
    UNIQUE (guild_id, message_id)
) STRICT;

CREATE UNIQUE INDEX tracked_message_one_draft ON tracked_message (guild_id) WHERE message_id IS NULL;

-- What a reaction on a tracked message does.
CREATE TABLE tracked_reaction (
    tracked_message_id INTEGER NOT NULL REFERENCES tracked_message (id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    emoji TEXT NOT NULL,
    action_type TEXT NOT NULL,
    message_content TEXT NOT NULL,
    target_id INTEGER,
    target_name TEXT,
    message_id INTEGER,
    PRIMARY KEY (tracked_message_id, position)
) STRICT;

-- Reaction history of published trainings and of tracked messages, kept apart by scope.
CREATE TABLE reaction_event (
    id INTEGER PRIMARY KEY,
    scope TEXT NOT NULL CHECK (scope IN ('formation', 'message')),
    guild_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    user_name TEXT,
    emoji TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('add', 'remove')),
    ts_iso TEXT NOT NULL
) STRICT;

CREATE INDEX reaction_event_message ON reaction_event (scope, guild_id, message_id);
CREATE INDEX reaction_event_user ON reaction_event (scope, guild_id, user_id);

-- Temporary admin roles to take back.
CREATE TABLE op_expiry (
    guild_id INTEGER NOT NULL,
    member_id INTEGER NOT NULL,
    expires_at TEXT NOT NULL,
    channel_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, member_id)
) STRICT;

-- Each JSON state file taken into this database, with the fingerprint it had then.
CREATE TABLE state_import (
    file_name TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL,
    imported_at TEXT NOT NULL
) STRICT;
