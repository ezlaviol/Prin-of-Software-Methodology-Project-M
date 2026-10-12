-- Run once in the Supabase SQL Editor before starting the API.
-- Assumes users_big from lecture1_demo.sql already exists.

-- Both endpoints sort by (created_at DESC, id DESC). This index lets
-- Postgres read rows in that order instead of sorting the whole table.
CREATE INDEX IF NOT EXISTS users_big_created_id_idx ON users_big (created_at, id);
ANALYZE users_big;

-- Cleanup after experiments that used --insert-every:
-- DELETE FROM users_big WHERE email LIKE 'new%';
