-- Runs once, only on a fresh postgres_data volume (docker-entrypoint-initdb.d
-- scripts only execute on first container initialization). Creates a second,
-- isolated database for the backend test suite, so host-venv pytest runs
-- never write into the shared dev database that manual/Docker testing uses.
-- See backend/tests/conftest.py and the "Testing" section of the backend
-- README/docs for how this gets used.
CREATE DATABASE cv_tailoring_test OWNER cvapp;
