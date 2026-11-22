CREATE TABLE character_card_library(
 id TEXT PRIMARY KEY,
 owner_id TEXT NOT NULL REFERENCES users(id),
 slot INTEGER NOT NULL CHECK(slot BETWEEN 1 AND 3),
 name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 80),
 kind TEXT NOT NULL CHECK(kind IN ('speech','meme','world')),
 description TEXT NOT NULL DEFAULT '' CHECK(length(description)<=200),
 content TEXT NOT NULL CHECK(length(content) BETWEEN 1 AND 4000),
 cover TEXT,
 revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(owner_id,slot)
);
CREATE INDEX character_card_library_browse ON character_card_library(created_at DESC,id DESC);
