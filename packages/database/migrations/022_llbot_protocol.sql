ALTER TABLE bots DROP CONSTRAINT bots_protocol_check;
ALTER TABLE bots ADD CONSTRAINT bots_protocol_check CHECK (protocol IN ('mock','napcat','llbot','euler'));
