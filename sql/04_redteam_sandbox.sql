-- 04_redteam_sandbox.sql
-- The role for the red-team sandbox. Attacks replayed WITHOUT the L3 lock (stacks L1 and L1+L2) run in a
-- throwaway database as this role, never as the postgres superuser.
--
-- redteam_sandbox can log in and owns the throwaway database's data, so an unguarded write really takes
-- effect there (that is the point: it shows what the prompt lock alone does not stop). But it is
-- NOSUPERUSER, NOCREATEDB, NOCREATEROLE, and it is not a member of the privileged built-in roles, so it
-- cannot read or write server files, run server programs, or signal other backends.
--
-- copilot.sandbox creates the throwaway database itself (CREATE DATABASE cannot run inside a transaction
-- block, so it is not in this file) and then loads sql/01, the demo data and sql/02 into it. Run this file
-- as the database owner / superuser.

do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'redteam_sandbox') then
    create role redteam_sandbox login nosuperuser nocreatedb nocreaterole;
  else
    alter role redteam_sandbox login nosuperuser nocreatedb nocreaterole;
  end if;
end $$;

-- Make sure it is not a member of any privileged built-in role (a no-op warning if it never was).
revoke pg_execute_server_program from redteam_sandbox;
revoke pg_read_server_files      from redteam_sandbox;
revoke pg_write_server_files     from redteam_sandbox;
revoke pg_signal_backend         from redteam_sandbox;

-- Keep every transaction in the sandbox short, as a harness safety net (the production L3 role has its own
-- timeout; the prompt lock alone does not).
alter role redteam_sandbox set statement_timeout = '5s';
