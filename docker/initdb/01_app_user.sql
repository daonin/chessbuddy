DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'chessbuddy_app') THEN
    CREATE ROLE chessbuddy_app LOGIN PASSWORD 'chessbuddy_password' NOSUPERUSER;
  END IF;
END$$;

-- Create schema owned by app user (safe if already exists)
CREATE SCHEMA IF NOT EXISTS chessbuddy AUTHORIZATION chessbuddy_app;
ALTER SCHEMA chessbuddy OWNER TO chessbuddy_app;
GRANT USAGE ON SCHEMA chessbuddy TO chessbuddy_app;

-- Optional: ensure future objects created by chessbuddy_app in schema are accessible to itself
ALTER DEFAULT PRIVILEGES FOR USER chessbuddy_app IN SCHEMA chessbuddy
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO chessbuddy_app;
ALTER DEFAULT PRIVILEGES FOR USER chessbuddy_app IN SCHEMA chessbuddy
GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO chessbuddy_app;
