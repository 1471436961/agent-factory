ALTER TABLE instance_snapshots
    ADD COLUMN configuration_checksum TEXT;

UPDATE instance_snapshots
SET configuration_checksum = (
    SELECT prototypes.checksum
    FROM prototypes
    WHERE prototypes.prototype_id = instance_snapshots.prototype_id
      AND prototypes.version = instance_snapshots.prototype_version
);

CREATE TEMP TABLE instance_configuration_checksum_guard (
    invalid_count INTEGER NOT NULL CHECK (invalid_count = 0)
);

INSERT INTO instance_configuration_checksum_guard (invalid_count)
SELECT COUNT(*)
FROM instance_snapshots
WHERE configuration_checksum IS NULL
   OR length(configuration_checksum) != 64;

DROP TABLE instance_configuration_checksum_guard;

CREATE TRIGGER trg_instance_configuration_checksum_insert
BEFORE INSERT ON instance_snapshots
FOR EACH ROW
WHEN NEW.configuration_checksum IS NULL
  OR length(NEW.configuration_checksum) != 64
BEGIN
    SELECT RAISE(ABORT, 'invalid instance configuration checksum');
END;

CREATE TRIGGER trg_instance_configuration_checksum_update
BEFORE UPDATE OF configuration_checksum ON instance_snapshots
FOR EACH ROW
WHEN NEW.configuration_checksum IS NULL
  OR length(NEW.configuration_checksum) != 64
BEGIN
    SELECT RAISE(ABORT, 'invalid instance configuration checksum');
END;
