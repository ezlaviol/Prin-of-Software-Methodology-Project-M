-- Database objects used by the ORM examples.
-- Assumes the social schema seeded by seed_social.py:
--   users(id, email, ...), posts(id, author_id, body, created_at, ...),
--   friendships(user_id, friend_id, ...), likes(user_id, post_id, ...)
-- Run once in the Supabase SQL Editor (or put it in a migration; see README).


-- 1. A VIEW: a saved query that looks like a table.
--    Every read runs the query again, so it is always up to date.
--    GROUP BY lists author_id and created_at, not just p.id: that lets
--    PostgreSQL apply a filter such as "WHERE author_id = 42" before it
--    groups. But when the view is joined to other tables, PostgreSQL may
--    still compute the whole view first. Check with EXPLAIN (example 5).
CREATE OR REPLACE VIEW post_stats AS
SELECT p.id           AS post_id,
       p.author_id,
       p.created_at,
       count(l.id)    AS like_count,
       max(l.created_at) AS last_liked_at
FROM posts p
LEFT JOIN likes l ON l.post_id = p.id
GROUP BY p.id, p.author_id, p.created_at;


-- 2. A FUNCTION that returns rows: a user's feed.
--    Posts by the people they follow, newest first, with like counts.
--    One call from Python, one round trip, all the work in the database.
CREATE OR REPLACE FUNCTION feed(p_user_id int, p_limit int DEFAULT 20)
RETURNS TABLE (post_id int, author_email text, body text,
               created_at timestamptz, like_count bigint)
LANGUAGE sql STABLE AS $$
    SELECT p.id, u.email, p.body, p.created_at,
           (SELECT count(*) FROM likes l WHERE l.post_id = p.id)
    FROM friendships f
    JOIN posts p ON p.author_id = f.friend_id
    JOIN users u ON u.id = p.author_id
    WHERE f.user_id = p_user_id
    ORDER BY p.created_at DESC
    LIMIT p_limit
$$;


-- 3. A FUNCTION that changes data: like a post, safely, and return the new
--    like count. ON CONFLICT makes a repeated like harmless.
CREATE OR REPLACE FUNCTION like_post(p_user_id int, p_post_id int)
RETURNS bigint
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO likes (user_id, post_id, created_at)
    VALUES (p_user_id, p_post_id, now())
    ON CONFLICT (user_id, post_id) DO NOTHING;
    RETURN (SELECT count(*) FROM likes WHERE post_id = p_post_id);
END;
$$;


-- 4. A TRIGGER that keeps a stored counter up to date, so reading a post's
--    like count is a column read instead of a count over the likes table.
ALTER TABLE posts ADD COLUMN IF NOT EXISTS like_count int NOT NULL DEFAULT 0;

UPDATE posts p SET like_count = s.n
FROM (SELECT post_id, count(*) AS n FROM likes GROUP BY post_id) s
WHERE s.post_id = p.id;

CREATE OR REPLACE FUNCTION likes_counter() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        UPDATE posts SET like_count = like_count + 1 WHERE id = NEW.post_id;
    ELSIF TG_OP = 'DELETE' THEN
        UPDATE posts SET like_count = like_count - 1 WHERE id = OLD.post_id;
    END IF;
    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS likes_counter ON likes;
CREATE TRIGGER likes_counter
AFTER INSERT OR DELETE ON likes
FOR EACH ROW EXECUTE FUNCTION likes_counter();


-- Cleanup:
-- DROP TRIGGER IF EXISTS likes_counter ON likes;
-- DROP FUNCTION IF EXISTS likes_counter(), like_post(int, int), feed(int, int);
-- DROP VIEW IF EXISTS post_stats;
-- ALTER TABLE posts DROP COLUMN IF EXISTS like_count;
