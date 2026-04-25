-- scripts/init_db.sql
-- Runs on first postgres container start
-- Enables pgvector extension

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
